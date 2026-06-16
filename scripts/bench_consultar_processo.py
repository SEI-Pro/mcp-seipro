"""PoC: benchmark `consultar_processo` — REST mod-wssei vs Web scraper.

Compara o método atual do MCP (REST `/processo/consultar`) contra
um scraper que navega pela cadeia de páginas web do SEI:

  inbox -> procedimento_trabalhar (frameset) -> procedimento_visualizar (arvore_montar)
  + opcional: procedimento_consultar_historico (andamentos)

Diferente de listar_processos, aqui o scraper precisa de N requests para
montar uma resposta equivalente. O ponto de comparação é se essa cadeia
ainda é mais rápida (e quanto perde de dados estruturados).

Uso:
    python scripts/bench_consultar_processo.py
    python scripts/bench_consultar_processo.py --warm 5 --com-historico
    python scripts/bench_consultar_processo.py --protocolo 50300.007186/2026-69
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
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urljoin

import httpx
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from bs4.element import Tag

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from todos.sei_client import SEIClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HTTP_OK = 200
MIN_ARVORE_ARGS = 7
MIN_HISTORY_COLS = 4
KB = 1024
P95_QUANTILE = 0.95
WARM_RUN_SLEEP_S = 0.5
MAX_SAMPLE_CHARS_A = 3000
MAX_SAMPLE_CHARS_B = 3000
HTTP_TIMEOUT_S = 60.0
HTTP_CONNECT_TIMEOUT_S = 10.0
HTTP_READ_TIMEOUT_S = 45.0

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


def load_dotenv(path: Path) -> dict[str, str]:
    """Load key=value pairs from a .env file into a dict and os.environ."""
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
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PhaseTiming:
    """Timing for a single benchmark phase."""

    name: str
    ms: float
    bytes_: int | None = None
    note: str = ""


@dataclass
class RunResult:
    """Aggregated result of a single benchmark method run."""

    method: str
    phases: list[PhaseTiming] = field(default_factory=list)
    warm_ms: list[float] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def stats(values: list[float]) -> dict[str, float]:
    """Return median, p95, min, and max for a list of float values."""
    if not values:
        return {"median": 0, "p95": 0, "min": 0, "max": 0}
    sorted_v = sorted(values)
    idx = max(0, round(P95_QUANTILE * len(sorted_v)) - 1)
    return {
        "median": statistics.median(values),
        "p95": sorted_v[idx],
        "min": min(values),
        "max": max(values),
    }


def _flatten_keys(d: dict, prefix: str = "") -> list[str]:
    """Achata chaves de dict aninhado (1 nivel) para comparacao."""
    keys: list[str] = []
    for k, v in d.items():
        full = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            keys.extend(f"{full}.{kk}" for kk in v)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            keys.append(f"{full}[]")
        else:
            keys.append(full)
    return keys


# ---------------------------------------------------------------------------
# Method A -- REST mod-wssei
# ---------------------------------------------------------------------------


async def _consultar_completo(client: SEIClient, protocolo: str) -> tuple[dict, int]:
    """Faz a sequencia completa do REST para obter dados rich do processo.

    1. GET /processo/consultar?protocoloFormatado=  -> id + nome do tipo
    2. GET /processo/consultar/{id}                 -> especificacao, assuntos,
       interessados, observacoes, nivelAcesso, etc.

    Combina os dois em um unico dict.
    """
    # call 1
    resp1 = await client.http_client.get(
        f"{client.base_url}/processo/consultar",
        params={"protocoloFormatado": protocolo},
        headers={"token": client.auth_token},
    )
    bytes_1 = len(resp1.content)
    j1 = resp1.json()
    if not j1.get("sucesso"):
        msg = f"call 1: {j1.get('mensagem')}"
        raise RuntimeError(msg)
    d1 = j1["data"]
    id_proc = d1.get("IdProcedimento")

    if not id_proc:
        return d1, bytes_1

    # call 2 (rich)
    resp2 = await client.http_client.get(
        f"{client.base_url}/processo/consultar/{id_proc}",
        headers={"token": client.auth_token},
    )
    bytes_2 = len(resp2.content)
    j2 = resp2.json()
    rich = j2.get("data", {}) if j2.get("sucesso") else {}

    # merge -- flat
    combined = {**d1, **rich}
    return combined, bytes_1 + bytes_2


async def _run_method_a(
    protocolo_a: str,
    args: argparse.Namespace,
) -> RunResult:
    """Run Method A (REST) and return the result."""
    a_result = await bench_method_a(protocolo_a, args.warm, args.unit)
    if a_result.error:
        sys.stderr.write(f"  x {a_result.error}\n")
    else:
        sys.stderr.write(f"  ok {len(a_result.fields)} campos retornados\n")
    return a_result


async def bench_method_a(protocolo: str, n_warm: int, unit_id: str) -> RunResult:
    """Benchmark do cliente REST mod-wssei para consultar_processo."""
    result = RunResult(method="A")
    client = SEIClient()

    try:
        # auth + unidade
        t0 = time.perf_counter()
        await client.autenticar()
        result.phases.append(PhaseTiming("auth (cold)", (time.perf_counter() - t0) * 1000))

        t0 = time.perf_counter()
        await client.trocar_unidade(unit_id)
        result.phases.append(PhaseTiming("trocar_unidade", (time.perf_counter() - t0) * 1000))

        # consultar completo (cold) -- 2 chamadas em sequencia
        t0 = time.perf_counter()
        data, cold_bytes = await _consultar_completo(client, protocolo)
        cold_ms = (time.perf_counter() - t0) * 1000

        result.phases.append(PhaseTiming("consultar (cold)", cold_ms, cold_bytes))
        result.data = data
        result.fields = _flatten_keys(data)

        # warm runs
        for _ in range(n_warm):
            await asyncio.sleep(WARM_RUN_SLEEP_S)
            t0 = time.perf_counter()
            await _consultar_completo(client, protocolo)
            result.warm_ms.append((time.perf_counter() - t0) * 1000)

    except (RuntimeError, httpx.HTTPError, OSError) as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            await client.http_client.aclose()

    return result


# ---------------------------------------------------------------------------
# Method B -- Web scraper
# ---------------------------------------------------------------------------


class WebSEIScraper:
    """Versao simplificada do WebSEIScraper focada em consultar processo."""

    def __init__(self, sei_root: str, usuario: str, senha: str, *, verify_ssl: bool) -> None:
        """Initialise the scraper with connection parameters."""
        self.sei_root = sei_root.rstrip("/")
        self.login_url = (
            f"{self.sei_root}/sip/login.php?sigla_orgao_sistema=ANTAQ&sigla_sistema=SEI"
        )
        self.usuario = usuario
        self.senha = senha
        self._http = httpx.AsyncClient(
            verify=verify_ssl,
            follow_redirects=True,
            timeout=httpx.Timeout(
                HTTP_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S, read=HTTP_READ_TIMEOUT_S
            ),
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
        self._inbox_url: httpx.URL | None = None
        self.login_get_ms: float = 0.0
        self.login_post_ms: float = 0.0
        # cache: protocolo -> URL pre-assinada de procedimento_trabalhar
        self.trabalhar_links: dict[str, str] = {}

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def login(self) -> None:
        """Perform SIP login and populate the inbox URL and process link cache."""
        t0 = time.perf_counter()
        resp = await self._http.get(self.login_url)
        self.login_get_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != HTTP_OK:
            msg = f"GET login retornou {resp.status_code}"
            raise RuntimeError(msg)
        html = resp.text
        if "g-recaptcha" in html or "h-captcha" in html:
            msg = "CAPTCHA presente -- abortando."
            raise RuntimeError(msg)
        if 'name="txtCodigo2FA"' in html:
            msg = "2FA solicitado -- fora de escopo."
            raise RuntimeError(msg)

        soup = BeautifulSoup(html, "html.parser")
        usuario_input = soup.find("input", attrs={"name": "txtUsuario"})
        if usuario_input is None:
            msg = "txtUsuario nao encontrado"
            raise RuntimeError(msg)
        login_form = usuario_input.find_parent("form")

        sel_orgao = self._find_sel_orgao(
            login_form.find("select", attrs={"name": "selOrgao"})
            or soup.find("select", attrs={"name": "selOrgao"})
        )
        form = self._build_login_form(login_form, sel_orgao)
        form["txtUsuario"] = self.usuario
        form["pwdSenha"] = self.senha

        action = login_form.get("action") or self.login_url
        post_url = urljoin(self.login_url, action)
        t0 = time.perf_counter()
        post_resp = await self._http.post(
            post_url,
            data=form,
            headers={"Referer": self.login_url, "Origin": self.sei_root},
        )
        self.login_post_ms = (time.perf_counter() - t0) * 1000

        if post_resp.status_code != HTTP_OK:
            msg = f"POST login status={post_resp.status_code}"
            raise RuntimeError(msg)
        final_url = post_resp.url
        qs = dict(
            parse_qsl(
                final_url.query.decode() if isinstance(final_url.query, bytes) else final_url.query
            )
        )
        if qs.get("acao") != "procedimento_controlar" or "infra_hash" not in qs:
            body = post_resp.text
            if 'name="txtUsuario"' in body:
                msg = "Login falhou -- credenciais invalidas?"
                raise RuntimeError(msg)
            msg = f"URL inesperada apos login: {final_url}"
            raise RuntimeError(msg)
        self._inbox_url = final_url
        # popula cache de links de processo a partir da propria inbox
        self._populate_trabalhar_links(post_resp.text)

    @staticmethod
    def _find_sel_orgao(sel: Tag | None) -> str:
        """Find the selOrgao option value from the login form select element."""
        if sel is None:
            return "0"
        sel_orgao = None
        for opt in sel.find_all("option"):
            if opt.get("selected") and opt.get("value") and opt.get("value") != "null":
                sel_orgao = opt["value"]
                break
        if sel_orgao is None:
            for opt in sel.find_all("option"):
                if "ANTAQ" in opt.get_text(strip=True).upper():
                    sel_orgao = opt.get("value")
                    break
        return sel_orgao if sel_orgao is not None else "0"

    @staticmethod
    def _build_login_form(login_form: Tag, sel_orgao: str) -> dict[str, str]:
        """Build the login POST form dict from the parsed login form."""
        form: dict[str, str] = {
            "txtUsuario": "",
            "pwdSenha": "",
            "selOrgao": sel_orgao,
            "sbmLogin": "Acessar",
        }
        for h in login_form.find_all("input", type="hidden"):
            if h.get("name") and h.get("value") is not None:
                form[h["name"]] = h["value"]
        return form

    def _populate_trabalhar_links(self, inbox_html: str) -> None:
        """Extrai links pre-assinados de procedimento_trabalhar da inbox.

        Mapeia protocolo -> URL completa (com infra_hash).
        """
        soup = BeautifulSoup(inbox_html, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"acao=procedimento_trabalhar")):
            txt = a.get_text(strip=True)
            href = a.get("href", "").replace("&amp;", "&")
            if txt and href:
                self.trabalhar_links.setdefault(txt, href)

    async def fetch_process_data(
        self,
        protocolo: str,
        *,
        com_historico: bool = False,
    ) -> dict:
        """Busca os dados de um processo navegando pela cadeia de paginas.

        Retorna dict com timing por fase + dados extraidos.
        """
        if self._inbox_url is None:
            msg = "login() nao foi chamado"
            raise RuntimeError(msg)
        if protocolo not in self.trabalhar_links:
            msg = (
                f"Protocolo {protocolo!r} nao encontrado nos links da inbox. "
                f"Disponiveis: {list(self.trabalhar_links.keys())[:5]}..."
            )
            raise RuntimeError(msg)

        timings: dict[str, float] = {}
        sizes: dict[str, int] = {}
        trab_url = urljoin(str(self._inbox_url), self.trabalhar_links[protocolo])

        # Step 1 — procedimento_trabalhar (frameset)
        t0 = time.perf_counter()
        r1 = await self._http.get(trab_url, headers={"Referer": str(self._inbox_url)})
        timings["trabalhar"] = (time.perf_counter() - t0) * 1000
        sizes["trabalhar"] = len(r1.content)
        if r1.status_code != HTTP_OK:
            msg = f"trabalhar status={r1.status_code}"
            raise RuntimeError(msg)
        result_data, arvore_url = self._parse_frameset(r1, protocolo)

        # Step 2 — procedimento_visualizar (arvore_montar)
        t0 = time.perf_counter()
        r2 = await self._http.get(arvore_url, headers={"Referer": trab_url})
        timings["arvore"] = (time.perf_counter() - t0) * 1000
        sizes["arvore"] = len(r2.content)
        if r2.status_code != HTTP_OK:
            msg = f"arvore status={r2.status_code}"
            raise RuntimeError(msg)
        hist_link = self._parse_arvore_html(r2.text, result_data)

        # Step 3 (opcional) — procedimento_consultar_historico
        if com_historico and hist_link:
            await self._fetch_historico(r2.url, hist_link, timings, sizes, result_data)

        return {
            "data": result_data,
            "timings_ms": timings,
            "sizes_bytes": sizes,
            "total_ms": sum(timings.values()),
            "total_bytes": sum(sizes.values()),
        }

    @staticmethod
    def _parse_frameset(r1: httpx.Response, protocolo: str) -> tuple[dict[str, Any], str]:
        """Parse the procedimento_trabalhar frameset, return (result_data, arvore_url)."""
        soup = BeautifulSoup(r1.text, "html.parser")
        result_data: dict[str, Any] = {"protocolo": protocolo}
        title = soup.find("title")
        if title:
            result_data["titulo_pagina"] = title.get_text(strip=True)
        m_id = re.search(r"id_procedimento=(\d+)", str(r1.url))
        if m_id:
            result_data["id_procedimento"] = m_id.group(1)
        ifr_arvore = soup.find("iframe", id="ifrArvore")
        if not ifr_arvore:
            msg = "ifrArvore nao encontrado no frameset"
            raise RuntimeError(msg)
        arvore_src = ifr_arvore.get("src", "").replace("&amp;", "&")
        return result_data, urljoin(str(r1.url), arvore_src)

    def _parse_arvore_html(self, arvore_html: str, result_data: dict[str, Any]) -> str | None:
        """Parse arvore page HTML, update result_data, return hist_link or None."""
        nos = self._parse_arvore_nos(arvore_html)
        if nos:
            root = nos[0]
            result_data["tipo"] = root.get("tooltip", "")
            result_data["icone"] = root.get("icone", "")
            docs = [
                {"id": n["id"], "protocolo": n.get("label", ""), "tipo": n.get("tipo", "")}
                for n in nos[1:]
                if n.get("tipo_no") != "PASTA"
            ]
            result_data["documentos"] = docs
            result_data["total_documentos"] = len(docs)

        soup_arv = BeautifulSoup(arvore_html, "html.parser")
        rels = [
            link_rel.get_text(strip=True)
            for div_rel in soup_arv.find_all("div", class_=re.compile(r"cardRelacionado"))
            for link_rel in [div_rel.find("a")]
            if link_rel
        ]
        if rels:
            result_data["relacionados"] = rels

        m_hist = re.search(
            r'(controlador\.php\?acao=procedimento_consultar_historico[^"\']*infra_hash=[a-f0-9]+)',
            arvore_html,
        )
        return m_hist.group(1).replace("&amp;", "&") if m_hist else None

    async def _fetch_historico(
        self,
        base_url: httpx.URL,
        hist_link: str,
        timings: dict[str, float],
        sizes: dict[str, int],
        result_data: dict[str, Any],
    ) -> None:
        """Fetch procedimento_consultar_historico and update result_data with andamentos."""
        hist_url = urljoin(str(base_url), hist_link)
        t0 = time.perf_counter()
        r3 = await self._http.get(hist_url, headers={"Referer": str(base_url)})
        timings["historico"] = (time.perf_counter() - t0) * 1000
        sizes["historico"] = len(r3.content)
        if r3.status_code != HTTP_OK:
            return
        soup_h = BeautifulSoup(r3.text, "html.parser")
        tbl = soup_h.find("table", id="tblHistorico")
        if not tbl:
            return
        andamentos = [
            {
                "data_hora": tds[0].get_text(" ", strip=True),
                "unidade": tds[1].get_text(" ", strip=True),
                "usuario": tds[2].get_text(" ", strip=True),
                "descricao": tds[3].get_text(" ", strip=True),
            }
            for tr in tbl.find_all("tr")[1:]
            for tds in [tr.find_all("td")]
            if len(tds) >= MIN_HISTORY_COLS
        ]
        result_data["andamentos"] = andamentos
        result_data["total_andamentos"] = len(andamentos)

    @staticmethod
    def _split_js_args(args_str: str) -> list[str]:
        """Split comma-separated JS constructor args, respecting string literals."""
        args: list[str] = []
        cur = ""
        in_str = False
        quote_char: str | None = None
        for ch in args_str:
            if in_str:
                cur += ch
                if ch == quote_char:
                    in_str = False
            elif ch in ('"', "'"):
                in_str = True
                quote_char = ch
                cur += ch
            elif ch == ",":
                args.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            args.append(cur.strip())
        return args

    @staticmethod
    def _unq_js_str(s: str) -> str:
        """Unquote a JS string literal; return empty string for null/empty."""
        s = s.strip()
        if s in ("null", ""):
            return ""
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        return s

    def _parse_arvore_nos(self, html: str) -> list[dict]:
        """Extrai array Nos[] do JS de arvore_montar.php."""
        out: list[dict] = []
        for m in re.finditer(
            r"Nos\[\d+\]\s*=\s*new infraArvoreNo\(([^;]*?)\);",
            html,
            re.DOTALL,
        ):
            args = self._split_js_args(m.group(1))
            if len(args) >= MIN_ARVORE_ARGS:
                unq = self._unq_js_str
                out.append(
                    {
                        "tipo_no": unq(args[0]),
                        "id": unq(args[1]),
                        "pai": unq(args[2]),
                        "link": unq(args[3]),
                        "target": unq(args[4]),
                        "label": unq(args[5]),
                        "tooltip": unq(args[6]),
                        "icone": unq(args[7]) if len(args) > MIN_ARVORE_ARGS else "",
                    }
                )
        return out


async def _run_method_b(
    env: dict,
    args: argparse.Namespace,
) -> tuple[RunResult, str | None]:
    """Run Method B (Web scraper) and return result plus discovered protocolo."""
    b_result = await bench_method_b(
        env,
        args.protocolo,
        args.warm,
        com_historico=args.com_historico,
    )
    if b_result.error:
        sys.stderr.write(f"  x {b_result.error}\n")
    else:
        sys.stderr.write(f"  ok {len(b_result.fields)} campos extraidos\n")

    protocolo = b_result.data.get("protocolo") if not b_result.error else None
    return b_result, protocolo


async def bench_method_b(
    env: dict,
    protocolo: str | None,
    n_warm: int,
    *,
    com_historico: bool,
) -> RunResult:
    """Benchmark do scraper web para consultar_processo."""
    result = RunResult(method="B")

    sei_url = env.get("SEI_URL") or os.environ.get("SEI_URL", "")
    if not sei_url:
        result.error = "SEI_URL nao definido"
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
        await scraper.login()
        result.phases.append(PhaseTiming("login GET (cold)", scraper.login_get_ms))
        result.phases.append(PhaseTiming("login POST + redirect", scraper.login_post_ms))
        result.notes.append(
            f"links de processos disponiveis na inbox: {len(scraper.trabalhar_links)}"
        )

        if not protocolo:
            if not scraper.trabalhar_links:
                result.error = "Inbox vazia -- passe --protocolo explicitamente"
                return result
            protocolo = next(iter(scraper.trabalhar_links.keys()))
            result.notes.append(f"protocolo escolhido automaticamente: {protocolo}")

        # cold fetch
        t0 = time.perf_counter()
        cold = await scraper.fetch_process_data(protocolo, com_historico=com_historico)
        cold_ms = (time.perf_counter() - t0) * 1000
        result.phases.append(
            PhaseTiming(
                "consultar cadeia (cold)",
                cold_ms,
                cold["total_bytes"],
                note=f"steps: {cold['timings_ms']}",
            )
        )
        result.data = cold["data"]
        result.fields = _flatten_keys(cold["data"])

        # warm runs
        relog = 0
        for i in range(n_warm):
            await asyncio.sleep(WARM_RUN_SLEEP_S)
            t0 = time.perf_counter()
            try:
                await scraper.fetch_process_data(protocolo, com_historico=com_historico)
            except (RuntimeError, httpx.HTTPError, OSError) as e:
                result.notes.append(f"warm {i}: {e}")
                continue
            result.warm_ms.append((time.perf_counter() - t0) * 1000)
        if relog == 0:
            result.notes.append("warm runs: sessao reaproveitada")

    except (RuntimeError, httpx.HTTPError, OSError) as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        await scraper.aclose()

    return result


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------


def fmt_ms(v: float | None) -> str:
    """Format a millisecond value as a string with one decimal place."""
    if v is None:
        return "-"
    return f"{v:,.1f}".replace(",", ".")


def fmt_bytes(v: int | None) -> str:
    """Format a byte count as a human-readable string."""
    if v is None:
        return "-"
    if v < KB:
        return f"{v} B"
    if v < KB * KB:
        return f"{v / KB:.1f} KB"
    return f"{v / (KB * KB):.2f} MB"


def _render_timing_table(a: RunResult | None, b: RunResult | None, out: list[str]) -> None:
    """Append the timing comparison section to out."""
    out.append("## Timing (ms)")
    out.append("")
    out.append("| Fase | Method A (REST) | Method B (Web) |")
    out.append("|---|---:|---:|")
    a_ph = {p.name: p for p in (a.phases if a else [])}
    b_ph = {p.name: p for p in (b.phases if b else [])}

    def row(label: str, av: str | None, bv: str | None) -> None:
        out.append(f"| {label} | {av or '-'} | {bv or '-'} |")

    row(
        "auth (cold)",
        fmt_ms(a_ph["auth (cold)"].ms) if "auth (cold)" in a_ph else None,
        fmt_ms(
            (b_ph["login GET (cold)"].ms if "login GET (cold)" in b_ph else 0)
            + (b_ph["login POST + redirect"].ms if "login POST + redirect" in b_ph else 0)
        )
        if "login GET (cold)" in b_ph
        else None,
    )
    row(
        "trocar_unidade",
        fmt_ms(a_ph["trocar_unidade"].ms) if "trocar_unidade" in a_ph else None,
        "n/a",
    )
    row(
        "consultar (cold)",
        fmt_ms(a_ph["consultar (cold)"].ms) if "consultar (cold)" in a_ph else None,
        fmt_ms(b_ph["consultar cadeia (cold)"].ms) if "consultar cadeia (cold)" in b_ph else None,
    )
    a_st = stats(a.warm_ms) if a else stats([])
    b_st = stats(b.warm_ms) if b else stats([])
    row(
        f"warm median (n={len(a.warm_ms) if a else 0}/{len(b.warm_ms) if b else 0})",
        fmt_ms(a_st["median"]) if a and a.warm_ms else None,
        fmt_ms(b_st["median"]) if b and b.warm_ms else None,
    )
    row(
        "warm p95",
        fmt_ms(a_st["p95"]) if a and a.warm_ms else None,
        fmt_ms(b_st["p95"]) if b and b.warm_ms else None,
    )
    row(
        "warm min/max",
        f"{fmt_ms(a_st['min'])} / {fmt_ms(a_st['max'])}" if a and a.warm_ms else None,
        f"{fmt_ms(b_st['min'])} / {fmt_ms(b_st['max'])}" if b and b.warm_ms else None,
    )
    row(
        "bytes payload (cold)",
        fmt_bytes(a_ph["consultar (cold)"].bytes_) if "consultar (cold)" in a_ph else None,
        fmt_bytes(b_ph["consultar cadeia (cold)"].bytes_)
        if "consultar cadeia (cold)" in b_ph
        else None,
    )
    out.append("")
    if a and b and a.warm_ms and b.warm_ms and b_st["median"] > 0:
        ratio = a_st["median"] / b_st["median"]
        winner = "Web" if ratio > 1 else "REST"
        out.append(f"**Speedup (warm median)**: {ratio:.2f}x -- {winner} mais rapido")
        out.append("")
    if b and "consultar cadeia (cold)" in b_ph:
        note = b_ph["consultar cadeia (cold)"].note
        if note:
            out.append(f"**Steps do Method B (cold)**: `{note}`")
            out.append("")


def _render_data_section(a: RunResult | None, b: RunResult | None, out: list[str]) -> None:
    """Append the data fields comparison section to out."""
    out.append("## Dados")
    out.append("")
    out.append("| Aspecto | Method A | Method B |")
    out.append("|---|---|---|")
    out.append(
        f"| campos retornados | {len(a.fields) if a else '-'} | {len(b.fields) if b else '-'} |"
    )
    out.append("")

    if a and a.fields:
        out.extend(["**Campos Method A**:", "", "```"])
        out.extend(f"  {f_}" for f_ in a.fields)
        out.extend(["```", ""])

    if b and b.fields:
        out.extend(["**Campos Method B**:", "", "```"])
        out.extend(f"  {f_}" for f_ in b.fields)
        out.extend(["```", ""])

    if a and b and a.fields and b.fields:
        only_a = sorted(set(a.fields) - set(b.fields))
        only_b = sorted(set(b.fields) - set(a.fields))
        if only_a or only_b:
            out.append("**Diff de campos**:")
            out.append("")
            if only_a:
                out.append(f"- so em REST ({len(only_a)}): " + ", ".join(only_a))
            if only_b:
                out.append(f"- so em Web ({len(only_b)}): " + ", ".join(only_b))
            out.append("")

    if a and a.data:
        out.extend(["**Sample Method A** (truncado a 3 KB):", "", "```json"])
        out.append(json.dumps(a.data, ensure_ascii=False, indent=2)[:MAX_SAMPLE_CHARS_A])
        out.extend(["```", ""])
    if b and b.data:
        out.extend(["**Sample Method B** (truncado a 3 KB):", "", "```json"])
        out.append(json.dumps(b.data, ensure_ascii=False, indent=2)[:MAX_SAMPLE_CHARS_B])
        out.extend(["```", ""])

    if (a and a.notes) or (b and b.notes):
        out.append("## Notes")
        out.append("")
        out.extend(f"- A: {n}" for n in (a.notes if a else []))
        out.extend(f"- B: {n}" for n in (b.notes if b else []))
        out.append("")


def render_report(a: RunResult | None, b: RunResult | None, args: argparse.Namespace) -> str:
    """Render a markdown benchmark report comparing Method A and Method B."""
    ts = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    out: list[str] = [f"# bench_consultar_processo -- {ts}", ""]
    out.append(
        f"protocolo: `{args.protocolo or '(auto da inbox)'}` warm: `{args.warm}` com_historico: `{args.com_historico}`"
    )
    out.append("")
    if a and a.error:
        out.extend([f"> **Method A FAILED**: {a.error}", ""])
    if b and b.error:
        out.extend([f"> **Method B FAILED**: {b.error}", ""])
    _render_timing_table(a, b, out)
    _render_data_section(a, b, out)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main_async(args: argparse.Namespace) -> int:
    """Run both benchmark methods and print the comparison report."""
    env_path = PROJECT_ROOT / ".env"
    env = load_dotenv(env_path)
    if not env.get("SEI_USUARIO") and not os.environ.get("SEI_USUARIO"):
        sys.stderr.write(f"ERRO: SEI_USUARIO nao definido em {env_path}\n")
        return 2

    a_result: RunResult | None = None
    b_result: RunResult | None = None

    if not args.skip_b:
        sys.stderr.write("-> Method B (Web scraper)...\n")
        b_result, auto_protocolo = await _run_method_b(env, args)
    else:
        auto_protocolo = None

    protocolo_a = args.protocolo or auto_protocolo

    if not args.skip_a:
        if not protocolo_a:
            sys.stderr.write("ERRO: --protocolo nao foi passado e Method B falhou\n")
            return 2
        sys.stderr.write(f"-> Method A (REST) para {protocolo_a}...\n")
        a_result = await _run_method_a(protocolo_a, args)

    if not args.protocolo and protocolo_a:
        args.protocolo = protocolo_a

    sys.stdout.write("\n")
    sys.stdout.write(render_report(a_result, b_result, args) + "\n")

    if a_result and a_result.error:
        return 3
    if b_result and b_result.error:
        return 3
    return 0


def main() -> None:
    """Entry point for the benchmark script."""
    parser = argparse.ArgumentParser(description="Benchmark consultar_processo REST vs Web")
    parser.add_argument(
        "--protocolo",
        default="",
        help="protocolo formatado (ex: 50300.007186/2026-69). Se vazio, usa o primeiro da inbox.",
    )
    parser.add_argument("--unit", default="110000037", help="ID da unidade SEI")
    parser.add_argument("--warm", type=int, default=5, help="warm runs (default 5)")
    parser.add_argument(
        "--com-historico",
        action="store_true",
        help="incluir step procedimento_consultar_historico no Method B",
    )
    parser.add_argument("--skip-a", action="store_true")
    parser.add_argument("--skip-b", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
