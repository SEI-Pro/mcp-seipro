"""PoC: benchmark `listar_processos` — WSSEI REST API vs Web Frontend scraper.

Compara o método atual do MCP (REST `/processo/listar` via mod-wssei) contra
um scraper HTTP da página `controlador.php?acao=procedimento_controlar`.

Uso:
    python scripts/bench_listar_processos.py
    python scripts/bench_listar_processos.py --unit 110000037 --warm 5
    python scripts/bench_listar_processos.py --skip-b              # só REST
    python scripts/bench_listar_processos.py --skip-a --login-only # só login Web

Limitações conhecidas:
  1. Modo de visualização (resumida vs detalhada) é setting per-user/unit do
     servidor — o PoC parseia o que o servidor retornar.
  2. Paginação do `procedimento_controlar.php` usa POST com hidden fields,
     não GET — o PoC mede apenas a página 1.
  3. CAPTCHA (g-recaptcha/h-captcha) e 2FA (txtCodigo2FA) são detectados e
     o script aborta com mensagem clara — a conta de teste não tem nenhum.
  4. Sessão SIP expirada entre warm runs → re-login automático (marcado).
  5. SEI_ORGAO=0 no .env é o id da REST API e NÃO é o `selOrgao` do dropdown
     SIP — o script lê o <option> correto do GET inicial.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import statistics
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from todos.sei_client import SEIClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

HTTP_OK = 200
KB = 1024
MB = 1024 * 1024
SAMPLE_MAX_BYTES = 4000
P95_PERCENTILE = 0.95
ROW_COUNT_DIFF_THRESHOLD = 2
MIN_TABLE_COLS = 4
MIN_ICONES_COLS = 2


# ---------------------------------------------------------------------------
# .env loader (parser manual, sem dependência nova)
# ---------------------------------------------------------------------------


def load_dotenv(path: Path) -> dict[str, str]:
    """Load key=value pairs from a .env file into os.environ and return as dict."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        env[k] = v
        os.environ.setdefault(k, v)
    return env


# ---------------------------------------------------------------------------
# Dataclasses para timing
# ---------------------------------------------------------------------------


@dataclass
class PhaseTiming:
    """Timing data for a single benchmark phase."""

    name: str
    ms: float
    bytes_: int | None = None
    note: str = ""


@dataclass
class RunResult:
    """Accumulated result of a single benchmark method run."""

    method: str  # "A" or "B"
    phases: list[PhaseTiming] = field(default_factory=list)
    warm_ms: list[float] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    layout: str = ""  # for method B: "resumida" or "detalhada"
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def stats(values: list[float]) -> dict[str, float]:
    """Compute median, p95, min, max statistics over a list of float values."""
    if not values:
        return {"median": 0, "p95": 0, "min": 0, "max": 0}
    median = statistics.median(values)
    sorted_v = sorted(values)
    # p95 simples: pega o índice ceil(0.95*N)-1
    idx = max(0, round(P95_PERCENTILE * len(sorted_v)) - 1)
    p95 = sorted_v[idx]
    return {
        "median": median,
        "p95": p95,
        "min": min(values),
        "max": max(values),
    }


# ---------------------------------------------------------------------------
# Method A — REST mod-wssei
# ---------------------------------------------------------------------------


async def bench_method_a(unit_id: str, n_warm: int, limit: int) -> RunResult:
    """Benchmark Method A: REST mod-wssei listar_processos."""
    result = RunResult(method="A")
    client = SEIClient()

    try:
        # A1 — autenticar (cold)
        t0 = time.perf_counter()
        await client.autenticar()
        result.phases.append(PhaseTiming("auth (cold)", (time.perf_counter() - t0) * 1000))

        # A2 — trocar_unidade
        t0 = time.perf_counter()
        await client.trocar_unidade(unit_id)
        result.phases.append(PhaseTiming("trocar_unidade", (time.perf_counter() - t0) * 1000))

        # A3 — listar (cold) + medir bytes via chamada raw
        t0 = time.perf_counter()
        data = await client.listar_processos(limit=limit, start=0)
        cold_ms = (time.perf_counter() - t0) * 1000

        # bytes do payload — chamada raw paralela só para essa medição
        cold_bytes = None
        try:
            raw_resp = await client.http_client.get(
                f"{client.base_url}/processo/listar",
                params={"limit": limit, "start": 0},
                headers={"token": client.auth_token},
            )
            cold_bytes = len(raw_resp.content)
        except (httpx.HTTPError, OSError):
            cold_bytes = None

        result.phases.append(PhaseTiming("listar (cold)", cold_ms, cold_bytes))
        result.rows = data.get("processos", []) or []

        # extrai nome dos campos da primeira linha (recursivo, 1 nível)
        if result.rows:
            first = result.rows[0]
            keys: list[str] = []
            for k, v in first.items():
                if isinstance(v, dict):
                    keys.extend(f"{k}.{kk}" for kk in v)
                else:
                    keys.append(k)
            result.fields = keys

        # A4 — warm runs
        for _ in range(n_warm):
            await asyncio.sleep(0.5)
            t0 = time.perf_counter()
            await client.listar_processos(limit=limit, start=0)
            result.warm_ms.append((time.perf_counter() - t0) * 1000)

    except (RuntimeError, httpx.HTTPError) as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            await client.http_client.aclose()

    return result


# ---------------------------------------------------------------------------
# Method B — Web scraper
# ---------------------------------------------------------------------------


class WebSEIScraper:
    """Simplified SEI web scraper for benchmarking inbox requests."""

    def __init__(self, sei_root: str, usuario: str, senha: str, *, verify_ssl: bool) -> None:
        """Initialize the scraper with connection parameters."""
        self.sei_root = sei_root.rstrip("/")
        self.login_url = (
            f"{self.sei_root}/sip/login.php?sigla_orgao_sistema=ANTAQ&sigla_sistema=SEI"
        )
        self.usuario = usuario
        self.senha = senha
        # follow_redirects=True: a cadeia pós-login passa por sip/login →
        # sei/inicializar.php → sei/controlador.php?acao=procedimento_controlar.
        # httpx segue tudo e a URL final fica em resp.url.
        self._http = httpx.AsyncClient(
            verify=verify_ssl,
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=10.0, read=45.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
        )
        self.inbox_url: httpx.URL | None = None
        self.login_get_ms: float = 0.0
        self.login_post_ms: float = 0.0
        # cache do form principal de procedimento_controlar:
        # action (com infra_hash) e hidden fields atuais
        self.form_action: str | None = None
        self._form_hidden: dict[str, str] = {}

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def login(self) -> None:
        """Perform the SIP login sequence and capture the inbox URL."""
        # B1 — GET login page
        t0 = time.perf_counter()
        resp = await self._http.get(self.login_url)
        self.login_get_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != HTTP_OK:
            msg = f"GET login.php retornou {resp.status_code}"
            raise RuntimeError(msg)

        html = resp.text

        # detect CAPTCHA / 2FA
        if "g-recaptcha" in html or "h-captcha" in html or "hcaptcha" in html:
            msg = "CAPTCHA presente no login — abortando."
            raise RuntimeError(msg)
        if 'name="txtCodigo2FA"' in html or 'id="txtCodigo2FA"' in html:
            msg = "2FA solicitado no login — fora de escopo do PoC."
            raise RuntimeError(msg)

        soup = BeautifulSoup(html, "html.parser")

        # localiza o form do login (o que contém txtUsuario)
        usuario_input = soup.find("input", attrs={"name": "txtUsuario"})
        if usuario_input is None:
            msg = "Campo txtUsuario não encontrado no login."
            raise RuntimeError(msg)
        login_form = usuario_input.find_parent("form")
        if login_form is None:
            msg = "<form> do login não encontrado."
            raise RuntimeError(msg)

        # selOrgao — escolhe option ANTAQ ou o já selecionado
        sel = login_form.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            sel = soup.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            msg = "<select name='selOrgao'> não encontrado no login."
            raise RuntimeError(msg)
        sel_orgao = _select_orgao(sel)

        form = _build_login_form(login_form, sel_orgao, self.usuario, self.senha)

        # B2 — POST login (usa action do form, resolvido contra a URL atual).
        # follow_redirects=True no client → segue toda a cadeia
        # sip/login.php → sei/inicializar.php → sei/controlador.php?acao=procedimento_controlar
        # automaticamente; resp.url terá a URL final.
        action = login_form.get("action") or self.login_url
        post_url = urljoin(self.login_url, action)
        t0 = time.perf_counter()
        post_resp = await self._http.post(
            post_url,
            data=form,
            headers={
                "Referer": self.login_url,
                "Origin": "https://sei.antaq.gov.br",
            },
        )
        self.login_post_ms = (time.perf_counter() - t0) * 1000

        self.inbox_url = _parse_login_response(post_resp)

    def _extract_main_form(self, html: str) -> None:
        """Captura action + hidden fields do form principal de procedimento_controlar.

        Atualiza self.form_action e self._form_hidden in-place.
        """
        soup = BeautifulSoup(html, "html.parser")
        for f in soup.find_all("form"):
            action = f.get("action") or ""
            if "procedimento_controlar" in action:
                self.form_action = action.replace("&amp;", "&")
                self._form_hidden = {}
                for h in f.find_all("input", type="hidden"):
                    name = h.get("name")
                    if name:
                        self._form_hidden[name] = h.get("value", "") or ""
                return

    async def fetch_inbox(
        self,
        *,
        detalhada: bool = False,
        pagina: int = 0,
    ) -> tuple[int, str]:
        """Busca a página de Controle de Processos.

        - `detalhada=False, pagina=0`: GET simples ao inbox URL (modo padrão).
        - `detalhada=True, pagina=0`: GET inicial para descobrir o form, POST
          com `hdnTipoVisualizacao=D` para forçar a visualização detalhada.
          Subsequentes chamadas com mesmo flag reaproveitam o form action
          (e o servidor lembra a preferência salva).
        - `pagina=N>0`: requer que `form_action` já esteja em cache; POST com
          `hdnInfraPaginaAtual=N` + `hdnInfraHashCriterios=<cache>`.
        """
        if self.inbox_url is None:
            msg = "login() não foi chamado antes de fetch_inbox()."
            raise RuntimeError(msg)

        # Caso 1: GET simples (modo legado/padrão, página 0, sem detalhada)
        if not detalhada and pagina == 0 and self.form_action is None:
            resp = await self._http.get(
                self.inbox_url,
                headers={"Referer": str(self.inbox_url)},
            )
            if resp.status_code != HTTP_OK:
                msg = f"fetch_inbox status={resp.status_code}"
                raise RuntimeError(msg)
            # cacheia o form para futuras navegações
            self._extract_main_form(resp.text)
            return len(resp.content), resp.text

        # Caso 2: precisa do form action — se não temos, faz GET primeiro
        if self.form_action is None:
            seed = await self._http.get(
                self.inbox_url,
                headers={"Referer": str(self.inbox_url)},
            )
            if seed.status_code != HTTP_OK:
                msg = f"fetch_inbox seed status={seed.status_code}"
                raise RuntimeError(msg)
            self._extract_main_form(seed.text)
            if self.form_action is None:
                msg = "Form principal de procedimento_controlar não encontrado"
                raise RuntimeError(msg)

        # Caso 3: POST para alternar visualização e/ou navegar páginas
        post_data = dict(self._form_hidden)
        if detalhada:
            post_data["hdnTipoVisualizacao"] = "D"
        if pagina > 0:
            post_data["hdnInfraPaginaAtual"] = str(pagina)
            # hdnInfraHashCriterios já está em _form_hidden (cacheado da última resposta)

        post_url = urljoin(str(self.inbox_url), self.form_action)
        resp = await self._http.post(
            post_url,
            data=post_data,
            headers={"Referer": str(self.inbox_url)},
        )
        if resp.status_code != HTTP_OK:
            msg = f"fetch_inbox POST status={resp.status_code}"
            raise RuntimeError(msg)

        # atualiza cache do form (action e hashCriterios podem ter mudado)
        self._extract_main_form(resp.text)
        return len(resp.content), resp.text


def _select_orgao(sel: Tag) -> str:
    """Select the appropriate selOrgao value from the login form select element."""
    sel_orgao = None
    # 1) option já selecionado
    for opt in sel.find_all("option"):
        if opt.get("selected") is not None and opt.get("value") and opt.get("value") != "null":
            sel_orgao = opt["value"]
            break
    # 2) option cujo texto contém ANTAQ
    if sel_orgao is None:
        for opt in sel.find_all("option"):
            if (
                "ANTAQ" in opt.get_text(strip=True).upper()
                and opt.get("value")
                and opt.get("value") != "null"
            ):
                sel_orgao = opt["value"]
                break
    # 3) fallback: primeiro option não-vazio e não-null
    if sel_orgao is None:
        for opt in sel.find_all("option"):
            v = opt.get("value")
            if v and v != "null":
                sel_orgao = v
                break
    if sel_orgao is None:
        msg = "Nenhum <option> válido em selOrgao."
        raise RuntimeError(msg)
    return sel_orgao


def _build_login_form(
    login_form: Tag,
    sel_orgao: str,
    usuario: str,
    senha: str,
) -> dict[str, str]:
    """Build the POST data dict for the SIP login form submission."""
    form: dict[str, str] = {
        "txtUsuario": usuario,
        "pwdSenha": senha,
        "selOrgao": sel_orgao,
        # Crítico: o backend só processa o login se receber o par
        # name=value do botão submit (sbmLogin=Acessar). Sem ele, o
        # script PHP renderiza apenas a página de login novamente.
        "sbmLogin": "Acessar",
    }

    # captura todos os hidden inputs (CSRF token dinâmico hdnToken<hash>, etc.)
    for hidden in login_form.find_all("input", type="hidden"):
        name = hidden.get("name")
        if name and hidden.get("value") is not None:
            form[name] = hidden["value"]

    # selContexto: campo existe mas geralmente vazio para ANTAQ
    sel_ctx = login_form.find("select", attrs={"name": "selContexto"})
    if sel_ctx is not None:
        ctx_val = ""
        for opt in sel_ctx.find_all("option"):
            if opt.get("selected") is not None:
                ctx_val = opt.get("value") or ""
                break
        form["selContexto"] = ctx_val

    return form


def _parse_login_response(post_resp: httpx.Response) -> httpx.URL:
    """Validate the POST login response and return the inbox URL."""
    if post_resp.status_code != HTTP_OK:
        msg = f"POST login retornou status inesperado {post_resp.status_code}"
        raise RuntimeError(msg)

    final_url = post_resp.url
    qs = dict(
        parse_qsl(
            final_url.query.decode() if isinstance(final_url.query, bytes) else final_url.query
        )
    )
    if qs.get("acao") == "procedimento_controlar" and "infra_hash" in qs:
        return final_url

    # Fallback: se a chain não terminou em procedimento_controlar,
    # talvez o servidor renderizou novamente o login (credenciais ruins,
    # 2FA, captcha, etc.).
    body = post_resp.text
    if 'name="txtUsuario"' in body or 'id="txtUsuario"' in body:
        msg = (
            "Login falhou: servidor retornou a página de login novamente. "
            "Verifique credenciais ou se há captcha/2FA configurados."
        )
        raise RuntimeError(msg)

    msg = f"Não localizei URL de procedimento_controlar após o login. Última URL: {final_url}"
    raise RuntimeError(msg)


def _col_names_from_headers(headers: list[str]) -> list[str]:
    """Map raw <th> text list to column name list for the detalhada table."""
    fallback = {0: "_check", 1: "icones", 2: "_processo", 3: "atribuicao"}
    return [h or fallback.get(i, f"col{i}") for i, h in enumerate(headers)]


def _build_detalhada_row(tr: Tag, tds: list, col_names: list[str]) -> dict[str, Any]:
    """Build a single row dict from a <tr> in the tblProcessosDetalhado table."""
    row: dict[str, Any] = {"id_procedimento": tr["id"][1:]}
    link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
    if link is not None:
        row["protocolo"] = link.get_text(" ", strip=True)
    if len(tds) >= MIN_ICONES_COLS:
        icones = [
            (img.get("title") or img.get("alt") or "").strip()
            for img in tds[1].find_all("img")
            if img.get("title") or img.get("alt")
        ]
        if icones:
            row["icones"] = icones
    for i, name in enumerate(col_names):
        if name.startswith("_") or name == "icones":
            continue
        if i < len(tds):
            val = tds[i].get_text(" ", strip=True)
            if val:
                row[name] = val
    return row


def _parse_detalhada(soup: BeautifulSoup) -> tuple[str, list[dict]]:
    """Parse the 'detalhada' (detailed) inbox table from the SEI HTML."""
    tbl = soup.find("table", id="tblProcessosDetalhado")
    if not tbl:
        return ("", [])
    first_tr = tbl.find("tr")
    if first_tr is None:
        return ("detalhada", [])
    headers = [th.get_text(" ", strip=True) for th in first_tr.find_all("th")]
    col_names = _col_names_from_headers(headers)
    rows: list[dict] = []
    for tr in tbl.find_all("tr", id=re.compile(r"^P\d+$")):
        tds = tr.find_all("td", recursive=False)
        rows.append(_build_detalhada_row(tr, tds, col_names))
    return ("detalhada", rows)


def _build_resumida_row(tr: Tag, tds: list, origem: str) -> dict[str, Any]:
    """Build a single row dict from a <tr> in a tblProcessosRecebidos/Gerados table."""
    row: dict[str, Any] = {"id_procedimento": tr["id"][1:], "origem": origem}
    link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
    if link is not None:
        row["protocolo"] = link.get_text(" ", strip=True)
    if len(tds) >= MIN_ICONES_COLS:
        icones = [
            (img.get("title") or img.get("alt") or "").strip()
            for img in tds[1].find_all("img")
            if img.get("title") or img.get("alt")
        ]
        if icones:
            row["icones"] = icones
    if len(tds) >= MIN_TABLE_COLS:
        atrib_text = tds[-1].get_text(" ", strip=True)
        if atrib_text:
            row["atribuicao"] = atrib_text
    return row


def _parse_resumida(soup: BeautifulSoup) -> tuple[str, list[dict]]:
    """Parse the 'resumida' (summary) inbox tables from the SEI HTML."""
    rows: list[dict] = []
    found_any = False
    for tbl_id, origem in [
        ("tblProcessosRecebidos", "recebido"),
        ("tblProcessosGerados", "gerado"),
    ]:
        tbl = soup.find("table", id=tbl_id)
        if tbl is None:
            continue
        found_any = True
        for tr in tbl.find_all("tr", id=re.compile(r"^P\d+$")):
            tds = tr.find_all("td", recursive=False)
            rows.append(_build_resumida_row(tr, tds, origem))

    if found_any:
        return ("resumida", rows)
    return ("", [])


def parse_inbox(html: str) -> tuple[str, list[dict]]:
    """Retorna (layout, rows). layout in {'detalhada','resumida','desconhecido'}."""
    soup = BeautifulSoup(html, "html.parser")

    # Detalhada (preferida — mais campos)
    layout, rows = _parse_detalhada(soup)
    if layout:
        return (layout, rows)

    # Resumida (default do SEI quando não há filtros)
    layout, rows = _parse_resumida(soup)
    if layout:
        return (layout, rows)

    return ("desconhecido", [])


async def _run_warm_runs(
    scraper: WebSEIScraper,
    n_warm: int,
    result: RunResult,
    *,
    detalhada: bool,
) -> None:
    """Execute warm-up runs for method B and record timings."""
    for i in range(n_warm):
        await asyncio.sleep(0.5)
        t0 = time.perf_counter()
        try:
            _, html = await scraper.fetch_inbox(detalhada=detalhada)
        except (RuntimeError, httpx.HTTPError) as e:
            result.notes.append(f"warm {i}: erro {e}")
            continue
        # detecta sessão expirada
        if 'name="txtUsuario"' in html or 'id="txtUsuario"' in html:
            result.notes.append(f"warm {i}: sessão expirou, re-logando")
            try:
                await scraper.login()
                scraper.form_action = None  # invalida cache do form
                t0 = time.perf_counter()
                _, html = await scraper.fetch_inbox(detalhada=detalhada)
            except (RuntimeError, httpx.HTTPError) as e:
                result.notes.append(f"warm {i}: re-login falhou: {e}")
                continue
        result.warm_ms.append((time.perf_counter() - t0) * 1000)


async def _run_pagination(
    scraper: WebSEIScraper,
    paginas: int,
    result: RunResult,
    *,
    detalhada: bool,
) -> None:
    """Fetch multiple inbox pages sequentially and record a combined timing phase."""
    pag_ms_list: list[float] = []
    pag_rows_total = 0
    # Start at p=1: page 0 was already fetched as the cold B3 measurement.
    for p in range(1, paginas):
        await asyncio.sleep(0.3)
        t0 = time.perf_counter()
        _, html_p = await scraper.fetch_inbox(detalhada=detalhada, pagina=p)
        pag_ms_list.append((time.perf_counter() - t0) * 1000)
        _, rows_p = parse_inbox(html_p)
        pag_rows_total += len(rows_p)
        if len(rows_p) == 0 and p > 0:
            break
    total = sum(pag_ms_list)
    result.phases.append(
        PhaseTiming(
            f"paginação {len(pag_ms_list)}p ({pag_rows_total} linhas)",
            total,
            note=f"individual: {[f'{x:.0f}' for x in pag_ms_list]}",
        )
    )


async def bench_method_b(
    env: dict,
    n_warm: int,
    *,
    login_only: bool = False,
    detalhada: bool = True,
    paginas: int = 1,
) -> RunResult:
    """Benchmark do scraper web.

    - `detalhada=True`: força a visualização Detalhada via POST hdnTipoVisualizacao=D
    - `paginas=N`: além do warm normal, mede o tempo de buscar N páginas em sequência
    """
    result = RunResult(method="B")

    sei_url = env.get("SEI_URL") or os.environ.get("SEI_URL", "")
    if not sei_url:
        result.error = "SEI_URL não definido no .env"
        return result
    sei_root = sei_url.split("/sei/", 1)[0]

    usuario = env.get("SEI_USUARIO") or os.environ.get("SEI_USUARIO", "")
    senha = env.get("SEI_SENHA") or os.environ.get("SEI_SENHA", "")
    verify_ssl_str = (env.get("SEI_VERIFY_SSL") or os.environ.get("SEI_VERIFY_SSL", "true")).lower()
    verify_ssl = verify_ssl_str != "false"

    if not verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    scraper = WebSEIScraper(sei_root, usuario, senha, verify_ssl=verify_ssl)

    try:
        # B1+B2 — login
        await scraper.login()
        result.phases.append(PhaseTiming("login GET (cold)", scraper.login_get_ms))
        result.phases.append(PhaseTiming("login POST + redirect", scraper.login_post_ms))
        result.notes.append(f"inbox_url capturado: {scraper.inbox_url}")

        if login_only:
            return result

        # B3 — fetch_inbox cold (com detalhada se solicitado)
        t0 = time.perf_counter()
        bytes_, html = await scraper.fetch_inbox(detalhada=detalhada)
        cold_ms = (time.perf_counter() - t0) * 1000
        result.phases.append(PhaseTiming("fetch_inbox (cold)", cold_ms, bytes_))

        layout, rows = parse_inbox(html)
        result.layout = layout
        result.rows = rows
        if rows:
            result.fields = list(dict.fromkeys(k for r in rows for k in r))

        # B4 — warm runs (mesma página)
        await _run_warm_runs(scraper, n_warm, result, detalhada=detalhada)
        relog_count = sum(1 for n in result.notes if "re-logando" in n)

        # B5 — paginação (se solicitado)
        if paginas > 1:
            await _run_pagination(scraper, paginas, result, detalhada=detalhada)

        if relog_count == 0:
            result.notes.append("warm runs: sessão reaproveitada (zero re-logins)")
        else:
            result.notes.append(f"warm runs: {relog_count} re-login(s) necessário(s)")

    except (RuntimeError, httpx.HTTPError) as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        await scraper.aclose()

    return result


# ---------------------------------------------------------------------------
# Relatório markdown
# ---------------------------------------------------------------------------


def fmt_ms(v: float | None) -> str:
    """Format milliseconds for display, or '-' if None."""
    if v is None:
        return "-"
    return f"{v:,.1f}".replace(",", ".")


def fmt_bytes(v: int | None) -> str:
    """Format bytes in human-readable form, or '-' if None."""
    if v is None:
        return "-"
    if v < KB:
        return f"{v} B"
    if v < MB:
        return f"{v / KB:.1f} KB"
    return f"{v / MB:.2f} MB"


def _render_timing_section(
    a: RunResult | None,
    b: RunResult | None,
    out: list[str],
) -> None:
    """Render the timing comparison table into out."""
    out.append("## Timing (ms)")
    out.append("")
    out.append("| Fase | Method A (REST) | Method B (Scrape) |")
    out.append("|---|---:|---:|")

    a_phases = {p.name: p for p in (a.phases if a else [])}
    b_phases = {p.name: p for p in (b.phases if b else [])}

    def row(label: str, a_val: str | None, b_val: str | None) -> None:
        out.append(f"| {label} | {a_val or '-'} | {b_val or '-'} |")

    row(
        "auth (cold)",
        fmt_ms(a_phases.get("auth (cold)").ms) if "auth (cold)" in a_phases else None,
        (
            fmt_ms(
                (b_phases.get("login GET (cold)").ms if "login GET (cold)" in b_phases else 0)
                + (
                    b_phases.get("login POST + redirect").ms
                    if "login POST + redirect" in b_phases
                    else 0
                )
            )
            if "login GET (cold)" in b_phases or "login POST + redirect" in b_phases
            else None
        ),
    )
    row(
        "trocar_unidade",
        fmt_ms(a_phases.get("trocar_unidade").ms) if "trocar_unidade" in a_phases else None,
        "n/a (no redirect)",
    )
    row(
        "listar (cold)",
        fmt_ms(a_phases.get("listar (cold)").ms) if "listar (cold)" in a_phases else None,
        fmt_ms(b_phases.get("fetch_inbox (cold)").ms) if "fetch_inbox (cold)" in b_phases else None,
    )

    a_stats = stats(a.warm_ms) if a else stats([])
    b_stats = stats(b.warm_ms) if b else stats([])
    row(
        f"listar warm median (n={len(a.warm_ms) if a else 0}/{len(b.warm_ms) if b else 0})",
        fmt_ms(a_stats["median"]) if a and a.warm_ms else None,
        fmt_ms(b_stats["median"]) if b and b.warm_ms else None,
    )
    row(
        "listar warm p95",
        fmt_ms(a_stats["p95"]) if a and a.warm_ms else None,
        fmt_ms(b_stats["p95"]) if b and b.warm_ms else None,
    )
    row(
        "listar warm min/max",
        f"{fmt_ms(a_stats['min'])} / {fmt_ms(a_stats['max'])}" if a and a.warm_ms else None,
        f"{fmt_ms(b_stats['min'])} / {fmt_ms(b_stats['max'])}" if b and b.warm_ms else None,
    )
    row(
        "bytes payload (cold listar)",
        fmt_bytes(a_phases.get("listar (cold)").bytes_) if "listar (cold)" in a_phases else None,
        fmt_bytes(b_phases.get("fetch_inbox (cold)").bytes_)
        if "fetch_inbox (cold)" in b_phases
        else None,
    )
    out.append("")

    # Fases adicionais (paginação, etc.) — não cabem no esquema fixo
    extras_known = {
        "auth (cold)",
        "trocar_unidade",
        "listar (cold)",
        "fetch_inbox (cold)",
        "login GET (cold)",
        "login POST + redirect",
    }
    a_extras = [p for p in (a.phases if a else []) if p.name not in extras_known]
    b_extras = [p for p in (b.phases if b else []) if p.name not in extras_known]
    if a_extras or b_extras:
        out.append("**Fases extras**:")
        out.append("")
        for p in a_extras:
            note = f" — {p.note}" if p.note else ""
            out.append(f"- A: `{p.name}` = {fmt_ms(p.ms)} ms{note}")
        for p in b_extras:
            note = f" — {p.note}" if p.note else ""
            out.append(f"- B: `{p.name}` = {fmt_ms(p.ms)} ms{note}")
        out.append("")

    # Speedup
    if a and b and a.warm_ms and b.warm_ms:
        ratio = a_stats["median"] / b_stats["median"] if b_stats["median"] > 0 else 0
        winner = "Web" if ratio > 1 else "REST"
        out.append(f"**Speedup (warm median)**: {ratio:.2f}x — {winner} mais rápido")
        out.append("")


def _render_data_section(
    a: RunResult | None,
    b: RunResult | None,
    out: list[str],
) -> None:
    """Render the data comparison section into out."""
    out.append("## Dados")
    out.append("")
    out.append("| Aspecto | Method A | Method B |")
    out.append("|---|---|---|")
    out.append(f"| linhas retornadas | {len(a.rows) if a else '-'} | {len(b.rows) if b else '-'} |")
    out.append(
        f"| campos por linha (qtd) | {len(a.fields) if a else '-'} | {len(b.fields) if b else '-'} |"
    )
    out.append(f"| layout web detectado | n/a | {b.layout if b else '-'} |")
    out.append("")

    if a and a.fields:
        out.append("**Campos Method A** (achatados 1 nível):")
        out.append("")
        out.append("```")
        out.extend(f"  {f_}" for f_ in a.fields)
        out.append("```")
        out.append("")

    if b and b.fields:
        out.append("**Campos Method B**:")
        out.append("")
        out.append("```")
        out.extend(f"  {f_}" for f_ in b.fields)
        out.append("```")
        out.append("")

    # Sample rows
    if a and a.rows:
        out.append("**Sample row Method A**:")
        out.append("")
        out.append("```json")
        out.append(json.dumps(a.rows[0], ensure_ascii=False, indent=2)[:SAMPLE_MAX_BYTES])
        out.append("```")
        out.append("")
    if b and b.rows:
        out.append("**Sample row Method B**:")
        out.append("")
        out.append("```json")
        out.append(json.dumps(b.rows[0], ensure_ascii=False, indent=2)[:SAMPLE_MAX_BYTES])
        out.append("```")
        out.append("")

    # Sanity de contagem
    if a and b and a.rows and b.rows:
        diff = abs(len(a.rows) - len(b.rows))
        if diff > ROW_COUNT_DIFF_THRESHOLD:
            out.append(
                f"> ⚠️  **WARNING**: contagem de linhas difere em {diff} "
                f"(A={len(a.rows)}, B={len(b.rows)}). "
                "Pode ser por origem (recebidos vs gerados), filtros do painel, "
                "ou parser incompleto."
            )
            out.append("")


def _render_notes_section(
    a: RunResult | None,
    b: RunResult | None,
    out: list[str],
) -> None:
    """Render the notes section into out."""
    if (a and a.notes) or (b and b.notes):
        out.append("## Notes")
        out.append("")
        out.extend(f"- A: {n}" for n in (a.notes if a else []))
        out.extend(f"- B: {n}" for n in (b.notes if b else []))
        out.append("")


def render_report(a: RunResult | None, b: RunResult | None, args: argparse.Namespace) -> str:
    """Render a Markdown benchmark comparison report."""
    ts = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    out: list[str] = []
    out.append(f"# bench_listar_processos — {ts}")
    out.append("")
    out.append(f"Unidade: `{args.unit}` · warm runs: `{args.warm}` · limit: `{args.limit}`")
    out.append("")

    # Erros antes da tabela
    if a and a.error:
        out.append(f"> **Method A FAILED**: {a.error}")
        out.append("")
    if b and b.error:
        out.append(f"> **Method B FAILED**: {b.error}")
        out.append("")

    _render_timing_section(a, b, out)
    _render_data_section(a, b, out)
    _render_notes_section(a, b, out)

    return "\n".join(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _log_a_result(result: RunResult) -> None:
    """Write Method A result summary to stderr."""
    if result.error:
        sys.stderr.write(f"  ✗ {result.error}\n")
    else:
        sys.stderr.write(f"  ✓ {len(result.rows)} processos\n")


def _log_b_result(result: RunResult, *, login_only: bool) -> None:
    """Write Method B result summary to stderr."""
    if result.error:
        sys.stderr.write(f"  ✗ {result.error}\n")
    elif login_only:
        sys.stderr.write(f"  ✓ login OK; inbox_url={result.notes[-1] if result.notes else '?'}\n")
    else:
        sys.stderr.write(f"  ✓ {len(result.rows)} processos (layout={result.layout})\n")


def _exit_code(
    a_result: RunResult | None,
    b_result: RunResult | None,
    args: argparse.Namespace,
) -> int:
    """Compute exit code based on benchmark results."""
    if a_result and a_result.error:
        return 3
    if b_result and b_result.error:
        return 3
    if a_result and not args.skip_a and not a_result.rows:
        return 2
    if b_result and not args.skip_b and not args.login_only and not b_result.rows:
        return 2
    return 0


async def main_async(args: argparse.Namespace) -> int:
    """Run both benchmark methods and print the report."""
    env_path = PROJECT_ROOT / ".env"
    env = load_dotenv(env_path)
    if not env.get("SEI_USUARIO") and not os.environ.get("SEI_USUARIO"):
        sys.stderr.write(f"ERRO: .env não encontrado em {env_path} ou SEI_USUARIO não definido\n")
        return 2

    a_result: RunResult | None = None
    b_result: RunResult | None = None

    if not args.skip_a:
        sys.stderr.write("→ rodando Method A (REST)…\n")
        a_result = await bench_method_a(args.unit, args.warm, args.limit)
        _log_a_result(a_result)

    if not args.skip_b:
        sys.stderr.write("→ rodando Method B (Web scraper)…\n")
        b_result = await bench_method_b(
            env,
            args.warm,
            login_only=args.login_only,
            detalhada=not args.no_detalhada,
            paginas=args.paginas,
        )
        _log_b_result(b_result, login_only=args.login_only)

    sys.stdout.write("\n")
    sys.stdout.write(render_report(a_result, b_result, args) + "\n")

    return _exit_code(a_result, b_result, args)


def main() -> None:
    """Entry point: parse args and run the async main loop."""
    parser = argparse.ArgumentParser(description="Benchmark listar_processos REST vs Web scraper")
    parser.add_argument("--unit", default="110000037", help="ID da unidade SEI (default 110000037)")
    parser.add_argument("--warm", type=int, default=5, help="número de warm runs (default 5)")
    parser.add_argument("--limit", type=int, default=50, help="page size para REST (default 50)")
    parser.add_argument("--skip-a", action="store_true", help="pular Method A (REST)")
    parser.add_argument("--skip-b", action="store_true", help="pular Method B (Web scraper)")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Method B faz só o login e captura infra_hash, sem fetch_inbox",
    )
    parser.add_argument(
        "--no-detalhada",
        action="store_true",
        help="Method B usa visualização Resumida (default: força Detalhada via POST)",
    )
    parser.add_argument(
        "--paginas",
        type=int,
        default=1,
        help="Method B: número de páginas a buscar em sequência (default 1; >1 mede paginação)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
