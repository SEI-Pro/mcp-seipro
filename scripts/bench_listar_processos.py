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
import json
import os
import re
import statistics
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin

import httpx
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Importado depois que .env já foi carregado para os.environ
# (import dentro da função main para garantir ordem)


# ---------------------------------------------------------------------------
# .env loader (parser manual, sem dependência nova)
# ---------------------------------------------------------------------------

def load_dotenv(path: Path) -> dict[str, str]:
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
    name: str
    ms: float
    bytes_: Optional[int] = None
    note: str = ""


@dataclass
class RunResult:
    method: str  # "A" or "B"
    phases: list[PhaseTiming] = field(default_factory=list)
    warm_ms: list[float] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    layout: str = ""  # for method B: "resumida" or "detalhada"
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": 0, "p95": 0, "min": 0, "max": 0}
    median = statistics.median(values)
    sorted_v = sorted(values)
    # p95 simples: pega o índice ceil(0.95*N)-1
    idx = max(0, int(round(0.95 * len(sorted_v))) - 1)
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
    from mcp_seipro.sei_client import SEIClient

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
        try:
            raw_resp = await client._client.get(
                f"{client.base_url}/processo/listar",
                params={"limit": limit, "start": 0},
                headers={"token": client._token},
            )
            cold_bytes = len(raw_resp.content)
        except Exception:
            cold_bytes = None

        result.phases.append(PhaseTiming("listar (cold)", cold_ms, cold_bytes))
        result.rows = data.get("processos", []) or []

        # extrai nome dos campos da primeira linha (recursivo, 1 nível)
        if result.rows:
            first = result.rows[0]
            keys: list[str] = []
            for k, v in first.items():
                if isinstance(v, dict):
                    for kk in v.keys():
                        keys.append(f"{k}.{kk}")
                else:
                    keys.append(k)
            result.fields = keys

        # A4 — warm runs
        for i in range(n_warm):
            await asyncio.sleep(0.5)
            t0 = time.perf_counter()
            await client.listar_processos(limit=limit, start=0)
            result.warm_ms.append((time.perf_counter() - t0) * 1000)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        try:
            await client._client.aclose()
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Method B — Web scraper
# ---------------------------------------------------------------------------

class WebSEIScraper:
    def __init__(self, sei_root: str, usuario: str, senha: str, verify_ssl: bool):
        self.sei_root = sei_root.rstrip("/")
        self.login_url = f"{self.sei_root}/sip/login.php?sigla_orgao_sistema=ANTAQ&sigla_sistema=SEI"
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
        self._inbox_url: Optional[httpx.URL] = None
        self._login_get_ms: float = 0.0
        self._login_post_ms: float = 0.0
        # cache do form principal de procedimento_controlar:
        # action (com infra_hash) e hidden fields atuais
        self._form_action: Optional[str] = None
        self._form_hidden: dict[str, str] = {}

    async def aclose(self):
        await self._http.aclose()

    async def login(self) -> None:
        # B1 — GET login page
        t0 = time.perf_counter()
        resp = await self._http.get(self.login_url)
        self._login_get_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            raise RuntimeError(f"GET login.php retornou {resp.status_code}")

        html = resp.text

        # detect CAPTCHA / 2FA
        if "g-recaptcha" in html or "h-captcha" in html or "hcaptcha" in html:
            raise RuntimeError("CAPTCHA presente no login — abortando.")
        if 'name="txtCodigo2FA"' in html or 'id="txtCodigo2FA"' in html:
            raise RuntimeError("2FA solicitado no login — fora de escopo do PoC.")

        soup = BeautifulSoup(html, "html.parser")

        # localiza o form do login (o que contém txtUsuario)
        usuario_input = soup.find("input", attrs={"name": "txtUsuario"})
        if usuario_input is None:
            raise RuntimeError("Campo txtUsuario não encontrado no login.")
        login_form = usuario_input.find_parent("form")
        if login_form is None:
            raise RuntimeError("<form> do login não encontrado.")

        # selOrgao — escolhe option ANTAQ ou o já selecionado
        sel = login_form.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            sel = soup.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            raise RuntimeError("<select name='selOrgao'> não encontrado no login.")
        sel_orgao = None
        # 1) option já selecionado
        for opt in sel.find_all("option"):
            if opt.get("selected") is not None and opt.get("value") and opt.get("value") != "null":
                sel_orgao = opt["value"]
                break
        # 2) option cujo texto contém ANTAQ
        if sel_orgao is None:
            for opt in sel.find_all("option"):
                if "ANTAQ" in opt.get_text(strip=True).upper() and opt.get("value") and opt.get("value") != "null":
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
            raise RuntimeError("Nenhum <option> válido em selOrgao.")

        form: dict[str, str] = {
            "txtUsuario": self.usuario,
            "pwdSenha": self.senha,
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
        self._login_post_ms = (time.perf_counter() - t0) * 1000

        if post_resp.status_code != 200:
            raise RuntimeError(
                f"POST login retornou status inesperado {post_resp.status_code}"
            )

        final_url = post_resp.url
        qs = dict(parse_qsl(
            final_url.query.decode() if isinstance(final_url.query, bytes) else final_url.query
        ))
        if qs.get("acao") == "procedimento_controlar" and "infra_hash" in qs:
            self._inbox_url = final_url
            return

        # Fallback: se a chain não terminou em procedimento_controlar,
        # talvez o servidor renderizou novamente o login (credenciais ruins,
        # 2FA, captcha, etc.).
        body = post_resp.text
        if 'name="txtUsuario"' in body or 'id="txtUsuario"' in body:
            raise RuntimeError(
                "Login falhou: servidor retornou a página de login novamente. "
                "Verifique credenciais ou se há captcha/2FA configurados."
            )

        raise RuntimeError(
            f"Não localizei URL de procedimento_controlar após o login. "
            f"Última URL: {final_url}"
        )

    def _extract_main_form(self, html: str) -> None:
        """Captura action + hidden fields do form principal de procedimento_controlar.

        Atualiza self._form_action e self._form_hidden in-place.
        """
        soup = BeautifulSoup(html, "html.parser")
        for f in soup.find_all("form"):
            action = f.get("action") or ""
            if "procedimento_controlar" in action:
                self._form_action = action.replace("&amp;", "&")
                self._form_hidden = {}
                for h in f.find_all("input", type="hidden"):
                    name = h.get("name")
                    if name:
                        self._form_hidden[name] = h.get("value", "") or ""
                return

    async def fetch_inbox(
        self,
        detalhada: bool = False,
        pagina: int = 0,
    ) -> tuple[int, str]:
        """Busca a página de Controle de Processos.

        - `detalhada=False, pagina=0`: GET simples ao inbox URL (modo padrão).
        - `detalhada=True, pagina=0`: GET inicial para descobrir o form, POST
          com `hdnTipoVisualizacao=D` para forçar a visualização detalhada.
          Subsequentes chamadas com mesmo flag reaproveitam o form action
          (e o servidor lembra a preferência salva).
        - `pagina=N>0`: requer que `_form_action` já esteja em cache; POST com
          `hdnInfraPaginaAtual=N` + `hdnInfraHashCriterios=<cache>`.
        """
        if self._inbox_url is None:
            raise RuntimeError("login() não foi chamado antes de fetch_inbox().")

        # Caso 1: GET simples (modo legado/padrão, página 0, sem detalhada)
        if not detalhada and pagina == 0 and self._form_action is None:
            resp = await self._http.get(
                self._inbox_url,
                headers={"Referer": str(self._inbox_url)},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"fetch_inbox status={resp.status_code}")
            # cacheia o form para futuras navegações
            self._extract_main_form(resp.text)
            return len(resp.content), resp.text

        # Caso 2: precisa do form action — se não temos, faz GET primeiro
        if self._form_action is None:
            seed = await self._http.get(
                self._inbox_url,
                headers={"Referer": str(self._inbox_url)},
            )
            if seed.status_code != 200:
                raise RuntimeError(f"fetch_inbox seed status={seed.status_code}")
            self._extract_main_form(seed.text)
            if self._form_action is None:
                raise RuntimeError("Form principal de procedimento_controlar não encontrado")

        # Caso 3: POST para alternar visualização e/ou navegar páginas
        post_data = dict(self._form_hidden)
        if detalhada:
            post_data["hdnTipoVisualizacao"] = "D"
        if pagina > 0:
            post_data["hdnInfraPaginaAtual"] = str(pagina)
            # hdnInfraHashCriterios já está em _form_hidden (cacheado da última resposta)

        post_url = urljoin(str(self._inbox_url), self._form_action)
        resp = await self._http.post(
            post_url,
            data=post_data,
            headers={"Referer": str(self._inbox_url)},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"fetch_inbox POST status={resp.status_code}")

        # atualiza cache do form (action e hashCriterios podem ter mudado)
        self._extract_main_form(resp.text)
        return len(resp.content), resp.text


def parse_inbox(html: str) -> tuple[str, list[dict]]:
    """Retorna (layout, rows). layout in {'detalhada','resumida','desconhecido'}."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []

    # Detalhada (preferida — mais campos)
    tbl = soup.find("table", id="tblProcessosDetalhado")
    if tbl:
        first_tr = tbl.find("tr")
        if first_tr is None:
            return ("detalhada", [])
        ths = first_tr.find_all("th")
        headers = [th.get_text(" ", strip=True) for th in ths]
        # Heurística: as 4 primeiras colunas são tipicamente
        # checkbox / status icons / Processo / Atribuição (sem header textual).
        # As demais têm header (Tipo, Especificação, Interessados, etc.).
        col_names: list[str] = []
        for i, h in enumerate(headers):
            if h:
                col_names.append(h)
            else:
                col_names.append({0: "_check", 1: "icones", 2: "_processo", 3: "atribuicao"}.get(i, f"col{i}"))

        for tr in tbl.find_all("tr", id=re.compile(r"^P\d+$")):
            tds = tr.find_all("td", recursive=False)
            row: dict[str, Any] = {"id_procedimento": tr["id"][1:]}
            # protocolo: link procedimento_trabalhar (geralmente na coluna 2)
            link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
            if link is not None:
                row["protocolo"] = link.get_text(" ", strip=True)
            # ícones de status (col 1): extrai title/alt
            if len(tds) >= 2:
                icones = []
                for img in tds[1].find_all("img"):
                    title = img.get("title") or img.get("alt") or ""
                    if title:
                        icones.append(title.strip())
                if icones:
                    row["icones"] = icones
            # demais colunas — ignora as auxiliares (_check, _processo)
            for i, name in enumerate(col_names):
                if name.startswith("_") or name == "icones":
                    continue
                if i < len(tds):
                    val = tds[i].get_text(" ", strip=True)
                    if val:
                        row[name] = val
            rows.append(row)
        return ("detalhada", rows)

    # Resumida (default do SEI quando não há filtros)
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
            row: dict[str, Any] = {
                "id_procedimento": tr["id"][1:],
                "origem": origem,
            }
            # localiza link do processo (geralmente na 3ª coluna)
            link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
            if link is not None:
                row["protocolo"] = link.get_text(" ", strip=True)
            # ícones na coluna 2 (status)
            if len(tds) >= 2:
                icones = []
                for img in tds[1].find_all("img"):
                    title = img.get("title") or img.get("alt") or ""
                    if title:
                        icones.append(title.strip())
                if icones:
                    row["icones"] = icones
            # atribuição na última coluna (link para usuário)
            if len(tds) >= 4:
                atrib_text = tds[-1].get_text(" ", strip=True)
                if atrib_text:
                    row["atribuicao"] = atrib_text
            rows.append(row)

    if found_any:
        return ("resumida", rows)
    return ("desconhecido", [])


async def bench_method_b(
    env: dict,
    n_warm: int,
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

    scraper = WebSEIScraper(sei_root, usuario, senha, verify_ssl)

    try:
        # B1+B2 — login
        await scraper.login()
        result.phases.append(PhaseTiming("login GET (cold)", scraper._login_get_ms))
        result.phases.append(PhaseTiming("login POST + redirect", scraper._login_post_ms))
        result.notes.append(f"inbox_url capturado: {scraper._inbox_url}")

        if login_only:
            return result

        # B3 — fetch_inbox cold (com detalhada se solicitado)
        t0 = time.perf_counter()
        bytes_, html = await scraper.fetch_inbox(detalhada=detalhada)
        cold_ms = (time.perf_counter() - t0) * 1000
        result.phases.append(PhaseTiming("fetch_inbox (cold)", cold_ms, bytes_))

        # parse
        layout, rows = parse_inbox(html)
        result.layout = layout
        result.rows = rows
        if rows:
            # coleta keys de TODAS as linhas (não só as primeiras 6),
            # já que cada linha pode ter campos opcionais diferentes
            keys: list[str] = []
            for r in rows:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)
            result.fields = keys

        # B4 — warm runs (mesma página)
        relog_count = 0
        for i in range(n_warm):
            await asyncio.sleep(0.5)
            t0 = time.perf_counter()
            try:
                _, html = await scraper.fetch_inbox(detalhada=detalhada)
            except Exception as e:
                result.notes.append(f"warm {i}: erro {e}")
                continue
            # detecta sessão expirada
            if 'name="txtUsuario"' in html or 'id="txtUsuario"' in html:
                result.notes.append(f"warm {i}: sessão expirou, re-logando")
                relog_count += 1
                await scraper.login()
                scraper._form_action = None  # invalida cache do form
                t0 = time.perf_counter()
                _, html = await scraper.fetch_inbox(detalhada=detalhada)
            result.warm_ms.append((time.perf_counter() - t0) * 1000)

        # B5 — paginação (se solicitado)
        if paginas > 1:
            pag_ms_list: list[float] = []
            pag_rows_total = 0
            for p in range(paginas):
                await asyncio.sleep(0.3)
                t0 = time.perf_counter()
                _, html_p = await scraper.fetch_inbox(detalhada=detalhada, pagina=p)
                pag_ms_list.append((time.perf_counter() - t0) * 1000)
                _, rows_p = parse_inbox(html_p)
                pag_rows_total += len(rows_p)
                # se a página retornou 0 linhas, fim
                if len(rows_p) == 0 and p > 0:
                    break
            total = sum(pag_ms_list)
            result.phases.append(PhaseTiming(
                f"paginação {len(pag_ms_list)}p ({pag_rows_total} linhas)",
                total,
                note=f"individual: {[f'{x:.0f}' for x in pag_ms_list]}",
            ))
        if relog_count == 0:
            result.notes.append("warm runs: sessão reaproveitada (zero re-logins)")
        else:
            result.notes.append(f"warm runs: {relog_count} re-login(s) necessário(s)")

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        await scraper.aclose()

    return result


# ---------------------------------------------------------------------------
# Relatório markdown
# ---------------------------------------------------------------------------

def fmt_ms(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v:,.1f}".replace(",", ".")


def fmt_bytes(v: Optional[int]) -> str:
    if v is None:
        return "-"
    if v < 1024:
        return f"{v} B"
    if v < 1024 * 1024:
        return f"{v / 1024:.1f} KB"
    return f"{v / (1024 * 1024):.2f} MB"


def render_report(a: Optional[RunResult], b: Optional[RunResult], args) -> str:
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    out: list[str] = []
    out.append(f"# bench_listar_processos — {ts}")
    out.append("")
    out.append(f"Unidade: `{args.unit}` · warm runs: `{args.warm}` · limit: `{args.limit}`")
    out.append("")

    # ----------------- Erros antes da tabela -----------------
    if a and a.error:
        out.append(f"> **Method A FAILED**: {a.error}")
        out.append("")
    if b and b.error:
        out.append(f"> **Method B FAILED**: {b.error}")
        out.append("")

    # ----------------- Tabela de timing -----------------
    out.append("## Timing (ms)")
    out.append("")
    out.append("| Fase | Method A (REST) | Method B (Scrape) |")
    out.append("|---|---:|---:|")

    a_phases = {p.name: p for p in (a.phases if a else [])}
    b_phases = {p.name: p for p in (b.phases if b else [])}

    def row(label: str, a_val: Optional[str], b_val: Optional[str]):
        out.append(f"| {label} | {a_val or '-'} | {b_val or '-'} |")

    row(
        "auth (cold)",
        fmt_ms(a_phases.get("auth (cold)").ms) if "auth (cold)" in a_phases else None,
        (
            fmt_ms(
                (b_phases.get("login GET (cold)").ms if "login GET (cold)" in b_phases else 0)
                + (b_phases.get("login POST + redirect").ms if "login POST + redirect" in b_phases else 0)
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
        fmt_bytes(b_phases.get("fetch_inbox (cold)").bytes_) if "fetch_inbox (cold)" in b_phases else None,
    )
    out.append("")

    # Fases adicionais (paginação, etc.) — não cabem no esquema fixo
    extras_known = {
        "auth (cold)", "trocar_unidade", "listar (cold)", "fetch_inbox (cold)",
        "login GET (cold)", "login POST + redirect",
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

    # ----------------- Speedup -----------------
    if a and b and a.warm_ms and b.warm_ms:
        ratio = a_stats["median"] / b_stats["median"] if b_stats["median"] > 0 else 0
        winner = "Web" if ratio > 1 else "REST"
        out.append(f"**Speedup (warm median)**: {ratio:.2f}× — {winner} mais rápido")
        out.append("")

    # ----------------- Dados -----------------
    out.append("## Dados")
    out.append("")
    out.append("| Aspecto | Method A | Method B |")
    out.append("|---|---|---|")
    out.append(f"| linhas retornadas | {len(a.rows) if a else '-'} | {len(b.rows) if b else '-'} |")
    out.append(
        f"| campos por linha (qtd) | {len(a.fields) if a else '-'} | {len(b.fields) if b else '-'} |"
    )
    out.append(
        f"| layout web detectado | n/a | {b.layout if b else '-'} |"
    )
    out.append("")

    if a and a.fields:
        out.append("**Campos Method A** (achatados 1 nível):")
        out.append("")
        out.append("```")
        for f_ in a.fields:
            out.append(f"  {f_}")
        out.append("```")
        out.append("")

    if b and b.fields:
        out.append("**Campos Method B**:")
        out.append("")
        out.append("```")
        for f_ in b.fields:
            out.append(f"  {f_}")
        out.append("```")
        out.append("")

    # ----------------- Sample rows -----------------
    if a and a.rows:
        out.append("**Sample row Method A**:")
        out.append("")
        out.append("```json")
        out.append(json.dumps(a.rows[0], ensure_ascii=False, indent=2)[:4000])
        out.append("```")
        out.append("")
    if b and b.rows:
        out.append("**Sample row Method B**:")
        out.append("")
        out.append("```json")
        out.append(json.dumps(b.rows[0], ensure_ascii=False, indent=2)[:4000])
        out.append("```")
        out.append("")

    # ----------------- Sanity de contagem -----------------
    if a and b and a.rows and b.rows:
        diff = abs(len(a.rows) - len(b.rows))
        if diff > 2:
            out.append(
                f"> ⚠️  **WARNING**: contagem de linhas difere em {diff} "
                f"(A={len(a.rows)}, B={len(b.rows)}). "
                "Pode ser por origem (recebidos vs gerados), filtros do painel, "
                "ou parser incompleto."
            )
            out.append("")

    # ----------------- Notes -----------------
    if (a and a.notes) or (b and b.notes):
        out.append("## Notes")
        out.append("")
        for n in (a.notes if a else []):
            out.append(f"- A: {n}")
        for n in (b.notes if b else []):
            out.append(f"- B: {n}")
        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main_async(args) -> int:
    env_path = PROJECT_ROOT / ".env"
    env = load_dotenv(env_path)
    if not env.get("SEI_USUARIO") and not os.environ.get("SEI_USUARIO"):
        print(f"ERRO: .env não encontrado em {env_path} ou SEI_USUARIO não definido", file=sys.stderr)
        return 2

    a_result: Optional[RunResult] = None
    b_result: Optional[RunResult] = None

    if not args.skip_a:
        print("→ rodando Method A (REST)…", file=sys.stderr)
        a_result = await bench_method_a(args.unit, args.warm, args.limit)
        if a_result.error:
            print(f"  ✗ {a_result.error}", file=sys.stderr)
        else:
            print(f"  ✓ {len(a_result.rows)} processos", file=sys.stderr)

    if not args.skip_b:
        print("→ rodando Method B (Web scraper)…", file=sys.stderr)
        b_result = await bench_method_b(
            env,
            args.warm,
            login_only=args.login_only,
            detalhada=not args.no_detalhada,
            paginas=args.paginas,
        )
        if b_result.error:
            print(f"  ✗ {b_result.error}", file=sys.stderr)
        else:
            if args.login_only:
                print(f"  ✓ login OK; inbox_url={b_result.notes[-1] if b_result.notes else '?'}", file=sys.stderr)
            else:
                print(f"  ✓ {len(b_result.rows)} processos (layout={b_result.layout})", file=sys.stderr)

    print()
    print(render_report(a_result, b_result, args))

    # exit codes
    if a_result and a_result.error:
        return 3
    if b_result and b_result.error:
        return 3
    if a_result and not args.skip_a and not a_result.rows:
        return 2
    if b_result and not args.skip_b and not args.login_only and not b_result.rows:
        return 2
    return 0


def main():
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
