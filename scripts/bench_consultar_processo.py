"""PoC: benchmark `consultar_processo` — REST mod-wssei vs Web scraper.

Compara o método atual do MCP (REST `/processo/consultar`) contra
um scraper que navega pela cadeia de páginas web do SEI:

  inbox → procedimento_trabalhar (frameset) → procedimento_visualizar (arvore_montar)
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


# ---------------------------------------------------------------------------
# .env loader
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
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PhaseTiming:
    name: str
    ms: float
    bytes_: Optional[int] = None
    note: str = ""


@dataclass
class RunResult:
    method: str
    phases: list[PhaseTiming] = field(default_factory=list)
    warm_ms: list[float] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": 0, "p95": 0, "min": 0, "max": 0}
    sorted_v = sorted(values)
    idx = max(0, int(round(0.95 * len(sorted_v))) - 1)
    return {
        "median": statistics.median(values),
        "p95": sorted_v[idx],
        "min": min(values),
        "max": max(values),
    }


def _flatten_keys(d: dict, prefix: str = "") -> list[str]:
    """Achata chaves de dict aninhado (1 nível) para comparação."""
    keys: list[str] = []
    for k, v in d.items():
        full = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            for kk in v.keys():
                keys.append(f"{full}.{kk}")
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            keys.append(f"{full}[]")
        else:
            keys.append(full)
    return keys


# ---------------------------------------------------------------------------
# Method A — REST mod-wssei
# ---------------------------------------------------------------------------

async def _consultar_completo(client, protocolo: str) -> tuple[dict, int]:
    """Faz a sequência completa do REST para obter dados rich do processo:

    1. GET /processo/consultar?protocoloFormatado=  → id + nome do tipo
    2. GET /processo/consultar/{id}                 → especificacao, assuntos,
       interessados, observacoes, nivelAcesso, etc.

    Combina os dois em um único dict.
    """
    # call 1
    resp1 = await client._client.get(
        f"{client.base_url}/processo/consultar",
        params={"protocoloFormatado": protocolo},
        headers={"token": client._token},
    )
    bytes_1 = len(resp1.content)
    j1 = resp1.json()
    if not j1.get("sucesso"):
        raise Exception(f"call 1: {j1.get('mensagem')}")
    d1 = j1["data"]
    id_proc = d1.get("IdProcedimento")

    if not id_proc:
        return d1, bytes_1

    # call 2 (rich)
    resp2 = await client._client.get(
        f"{client.base_url}/processo/consultar/{id_proc}",
        headers={"token": client._token},
    )
    bytes_2 = len(resp2.content)
    j2 = resp2.json()
    rich = j2.get("data", {}) if j2.get("sucesso") else {}

    # merge — flat
    combined = dict(d1)
    for k, v in rich.items():
        combined[k] = v
    return combined, bytes_1 + bytes_2


async def bench_method_a(protocolo: str, n_warm: int, unit_id: str) -> RunResult:
    from mcp_seipro.sei_client import SEIClient

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

        # consultar completo (cold) — 2 chamadas em sequência
        t0 = time.perf_counter()
        data, cold_bytes = await _consultar_completo(client, protocolo)
        cold_ms = (time.perf_counter() - t0) * 1000

        result.phases.append(PhaseTiming("consultar (cold)", cold_ms, cold_bytes))
        result.data = data
        result.fields = _flatten_keys(data)

        # warm runs
        for i in range(n_warm):
            await asyncio.sleep(0.5)
            t0 = time.perf_counter()
            await _consultar_completo(client, protocolo)
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
    """Versão simplificada do WebSEIScraper focada em consultar processo."""

    def __init__(self, sei_root: str, usuario: str, senha: str, verify_ssl: bool):
        self.sei_root = sei_root.rstrip("/")
        self.login_url = f"{self.sei_root}/sip/login.php?sigla_orgao_sistema=ANTAQ&sigla_sistema=SEI"
        self.usuario = usuario
        self.senha = senha
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
        # cache: protocolo → URL pré-assinada de procedimento_trabalhar
        self._trabalhar_links: dict[str, str] = {}

    async def aclose(self):
        await self._http.aclose()

    async def login(self) -> None:
        t0 = time.perf_counter()
        resp = await self._http.get(self.login_url)
        self._login_get_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            raise RuntimeError(f"GET login retornou {resp.status_code}")
        html = resp.text
        if "g-recaptcha" in html or "h-captcha" in html:
            raise RuntimeError("CAPTCHA presente — abortando.")
        if 'name="txtCodigo2FA"' in html:
            raise RuntimeError("2FA solicitado — fora de escopo.")

        soup = BeautifulSoup(html, "html.parser")
        usuario_input = soup.find("input", attrs={"name": "txtUsuario"})
        if usuario_input is None:
            raise RuntimeError("txtUsuario não encontrado")
        login_form = usuario_input.find_parent("form")

        # selOrgao
        sel = login_form.find("select", attrs={"name": "selOrgao"}) or soup.find("select", attrs={"name": "selOrgao"})
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
        if sel_orgao is None:
            sel_orgao = "0"

        form: dict[str, str] = {
            "txtUsuario": self.usuario,
            "pwdSenha": self.senha,
            "selOrgao": sel_orgao,
            "sbmLogin": "Acessar",
        }
        for h in login_form.find_all("input", type="hidden"):
            if h.get("name") and h.get("value") is not None:
                form[h["name"]] = h["value"]

        action = login_form.get("action") or self.login_url
        post_url = urljoin(self.login_url, action)
        t0 = time.perf_counter()
        post_resp = await self._http.post(
            post_url, data=form,
            headers={"Referer": self.login_url, "Origin": self.sei_root},
        )
        self._login_post_ms = (time.perf_counter() - t0) * 1000

        if post_resp.status_code != 200:
            raise RuntimeError(f"POST login status={post_resp.status_code}")
        final_url = post_resp.url
        qs = dict(parse_qsl(
            final_url.query.decode() if isinstance(final_url.query, bytes) else final_url.query
        ))
        if qs.get("acao") != "procedimento_controlar" or "infra_hash" not in qs:
            body = post_resp.text
            if 'name="txtUsuario"' in body:
                raise RuntimeError("Login falhou — credenciais inválidas?")
            raise RuntimeError(f"URL inesperada após login: {final_url}")
        self._inbox_url = final_url
        # popula cache de links de processo a partir da própria inbox
        self._populate_trabalhar_links(post_resp.text)

    def _populate_trabalhar_links(self, inbox_html: str) -> None:
        """Extrai links pré-assinados de procedimento_trabalhar da inbox.

        Mapeia protocolo → URL completa (com infra_hash). Sem isso, não
        conseguimos navegar para um processo (não temos como gerar o hash).
        """
        soup = BeautifulSoup(inbox_html, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"acao=procedimento_trabalhar")):
            txt = a.get_text(strip=True)
            href = a.get("href", "").replace("&amp;", "&")
            if txt and href:
                # txt é o protocolo formatado (ex: 50300.007186/2026-69)
                self._trabalhar_links.setdefault(txt, href)

    async def fetch_process_data(
        self,
        protocolo: str,
        com_historico: bool = False,
    ) -> dict:
        """Busca os dados de um processo navegando pela cadeia de páginas.

        Retorna dict com timing por fase + dados extraídos.
        """
        if self._inbox_url is None:
            raise RuntimeError("login() não foi chamado")
        if protocolo not in self._trabalhar_links:
            raise RuntimeError(
                f"Protocolo {protocolo!r} não encontrado nos links da inbox. "
                f"Disponíveis: {list(self._trabalhar_links.keys())[:5]}..."
            )

        result_data: dict[str, Any] = {}
        timings: dict[str, float] = {}
        sizes: dict[str, int] = {}

        trab_url = urljoin(str(self._inbox_url), self._trabalhar_links[protocolo])

        # Step 1 — procedimento_trabalhar (frameset)
        t0 = time.perf_counter()
        r1 = await self._http.get(trab_url, headers={"Referer": str(self._inbox_url)})
        timings["trabalhar"] = (time.perf_counter() - t0) * 1000
        sizes["trabalhar"] = len(r1.content)
        if r1.status_code != 200:
            raise RuntimeError(f"trabalhar status={r1.status_code}")

        soup_fs = BeautifulSoup(r1.text, "html.parser")
        title = soup_fs.find("title")
        if title:
            result_data["titulo_pagina"] = title.get_text(strip=True)
        # extrai id_procedimento da URL
        m_id = re.search(r"id_procedimento=(\d+)", str(r1.url))
        if m_id:
            result_data["id_procedimento"] = m_id.group(1)
        result_data["protocolo"] = protocolo

        ifr_arvore = soup_fs.find("iframe", id="ifrArvore")
        if not ifr_arvore:
            raise RuntimeError("ifrArvore não encontrado no frameset")
        arvore_src = ifr_arvore.get("src", "").replace("&amp;", "&")
        arvore_url = urljoin(str(r1.url), arvore_src)

        # Step 2 — procedimento_visualizar (arvore_montar)
        t0 = time.perf_counter()
        r2 = await self._http.get(arvore_url, headers={"Referer": trab_url})
        timings["arvore"] = (time.perf_counter() - t0) * 1000
        sizes["arvore"] = len(r2.content)
        if r2.status_code != 200:
            raise RuntimeError(f"arvore status={r2.status_code}")

        # parsing da árvore: extrai Nos[] do JS
        arvore_html = r2.text
        nos = self._parse_arvore_nos(arvore_html)
        if nos:
            # Nos[0] é o root processo
            root = nos[0]
            result_data["tipo"] = root.get("tooltip", "")
            result_data["icone"] = root.get("icone", "")
            # documentos = todos os Nos exceto raiz e exceto PASTA
            docs = [
                {"id": n["id"], "protocolo": n.get("label", ""), "tipo": n.get("tipo", "")}
                for n in nos[1:]
                if n.get("tipo_no") not in ("PASTA",)
            ]
            result_data["documentos"] = docs
            result_data["total_documentos"] = len(docs)

        # parse processos relacionados
        soup_arv = BeautifulSoup(arvore_html, "html.parser")
        rels: list[str] = []
        for div_rel in soup_arv.find_all("div", class_=re.compile(r"cardRelacionado")):
            link_rel = div_rel.find("a")
            if link_rel:
                rels.append(link_rel.get_text(strip=True))
        if rels:
            result_data["relacionados"] = rels

        # link de andamento (para historico opcional)
        m_hist = re.search(
            r'(controlador\.php\?acao=procedimento_consultar_historico[^"\']*infra_hash=[a-f0-9]+)',
            arvore_html,
        )
        hist_link = m_hist.group(1).replace("&amp;", "&") if m_hist else None

        # Step 3 (opcional) — procedimento_consultar_historico
        if com_historico and hist_link:
            hist_url = urljoin(str(r2.url), hist_link)
            t0 = time.perf_counter()
            r3 = await self._http.get(hist_url, headers={"Referer": str(r2.url)})
            timings["historico"] = (time.perf_counter() - t0) * 1000
            sizes["historico"] = len(r3.content)
            if r3.status_code == 200:
                soup_h = BeautifulSoup(r3.text, "html.parser")
                tbl = soup_h.find("table", id="tblHistorico")
                if tbl:
                    rows = tbl.find_all("tr")
                    andamentos = []
                    for tr in rows[1:]:  # pula header
                        tds = tr.find_all("td")
                        if len(tds) >= 4:
                            andamentos.append({
                                "data_hora": tds[0].get_text(" ", strip=True),
                                "unidade": tds[1].get_text(" ", strip=True),
                                "usuario": tds[2].get_text(" ", strip=True),
                                "descricao": tds[3].get_text(" ", strip=True),
                            })
                    result_data["andamentos"] = andamentos
                    result_data["total_andamentos"] = len(andamentos)

        return {
            "data": result_data,
            "timings_ms": timings,
            "sizes_bytes": sizes,
            "total_ms": sum(timings.values()),
            "total_bytes": sum(sizes.values()),
        }

    @staticmethod
    def _parse_arvore_nos(html: str) -> list[dict]:
        """Extrai array Nos[] do JS de arvore_montar.php.

        Cada Nos[i] = new infraArvoreNo(tipo, id, pai, link, target, label,
                                          tooltip, icone, ...);
        """
        out: list[dict] = []
        for m in re.finditer(
            r"Nos\[\d+\]\s*=\s*new infraArvoreNo\(([^;]*?)\);",
            html,
            re.S,
        ):
            args_str = m.group(1)
            # parser muito simples para args separados por vírgula respeitando aspas
            args = []
            cur = ""
            in_str = False
            quote_char = None
            for ch in args_str:
                if in_str:
                    cur += ch
                    if ch == quote_char:
                        in_str = False
                elif ch in ('"', "'"):
                    in_str = True
                    quote_char = ch
                    cur += ch
                elif ch == "," :
                    args.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            if cur.strip():
                args.append(cur.strip())

            def unq(s: str) -> str:
                s = s.strip()
                if s in ("null", ""):
                    return ""
                if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                    return s[1:-1]
                return s

            if len(args) >= 7:
                out.append({
                    "tipo_no": unq(args[0]),
                    "id": unq(args[1]),
                    "pai": unq(args[2]),
                    "link": unq(args[3]),
                    "target": unq(args[4]),
                    "label": unq(args[5]),
                    "tooltip": unq(args[6]),
                    "icone": unq(args[7]) if len(args) > 7 else "",
                })
        return out


async def bench_method_b(
    env: dict,
    protocolo: Optional[str],
    n_warm: int,
    com_historico: bool,
) -> RunResult:
    result = RunResult(method="B")

    sei_url = env.get("SEI_URL") or os.environ.get("SEI_URL", "")
    if not sei_url:
        result.error = "SEI_URL não definido"
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
        await scraper.login()
        result.phases.append(PhaseTiming("login GET (cold)", scraper._login_get_ms))
        result.phases.append(PhaseTiming("login POST + redirect", scraper._login_post_ms))
        result.notes.append(
            f"links de processos disponíveis na inbox: {len(scraper._trabalhar_links)}"
        )

        if not protocolo:
            if not scraper._trabalhar_links:
                result.error = "Inbox vazia — passe --protocolo explicitamente"
                return result
            protocolo = next(iter(scraper._trabalhar_links.keys()))
            result.notes.append(f"protocolo escolhido automaticamente: {protocolo}")

        # cold fetch
        t0 = time.perf_counter()
        cold = await scraper.fetch_process_data(protocolo, com_historico=com_historico)
        cold_ms = (time.perf_counter() - t0) * 1000
        result.phases.append(PhaseTiming(
            "consultar cadeia (cold)",
            cold_ms,
            cold["total_bytes"],
            note=f"steps: {cold['timings_ms']}",
        ))
        result.data = cold["data"]
        result.fields = _flatten_keys(cold["data"])

        # warm runs
        relog = 0
        for i in range(n_warm):
            await asyncio.sleep(0.5)
            t0 = time.perf_counter()
            try:
                _ = await scraper.fetch_process_data(protocolo, com_historico=com_historico)
            except Exception as e:
                result.notes.append(f"warm {i}: {e}")
                continue
            result.warm_ms.append((time.perf_counter() - t0) * 1000)
        if relog == 0:
            result.notes.append("warm runs: sessão reaproveitada")

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        await scraper.aclose()

    return result


# ---------------------------------------------------------------------------
# Relatório
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
    out: list[str] = [f"# bench_consultar_processo — {ts}", ""]
    out.append(f"protocolo: `{args.protocolo or '(auto da inbox)'}` · warm: `{args.warm}` · com_historico: `{args.com_historico}`")
    out.append("")

    if a and a.error:
        out.append(f"> **Method A FAILED**: {a.error}")
        out.append("")
    if b and b.error:
        out.append(f"> **Method B FAILED**: {b.error}")
        out.append("")

    out.append("## Timing (ms)")
    out.append("")
    out.append("| Fase | Method A (REST) | Method B (Web) |")
    out.append("|---|---:|---:|")
    a_ph = {p.name: p for p in (a.phases if a else [])}
    b_ph = {p.name: p for p in (b.phases if b else [])}

    def row(label, av, bv):
        out.append(f"| {label} | {av or '-'} | {bv or '-'} |")

    row(
        "auth (cold)",
        fmt_ms(a_ph["auth (cold)"].ms) if "auth (cold)" in a_ph else None,
        fmt_ms(
            (b_ph["login GET (cold)"].ms if "login GET (cold)" in b_ph else 0)
            + (b_ph["login POST + redirect"].ms if "login POST + redirect" in b_ph else 0)
        ) if "login GET (cold)" in b_ph else None,
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
        fmt_bytes(b_ph["consultar cadeia (cold)"].bytes_) if "consultar cadeia (cold)" in b_ph else None,
    )
    out.append("")

    if a and b and a.warm_ms and b.warm_ms and b_st["median"] > 0:
        ratio = a_st["median"] / b_st["median"]
        winner = "Web" if ratio > 1 else "REST"
        out.append(f"**Speedup (warm median)**: {ratio:.2f}× — {winner} mais rápido")
        out.append("")

    # detalhamento dos steps do Method B
    if b and "consultar cadeia (cold)" in b_ph:
        note = b_ph["consultar cadeia (cold)"].note
        if note:
            out.append(f"**Steps do Method B (cold)**: `{note}`")
            out.append("")

    # ----- Dados -----
    out.append("## Dados")
    out.append("")
    out.append("| Aspecto | Method A | Method B |")
    out.append("|---|---|---|")
    out.append(f"| campos retornados | {len(a.fields) if a else '-'} | {len(b.fields) if b else '-'} |")
    out.append("")

    if a and a.fields:
        out.append("**Campos Method A**:")
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

    # diff de campos (set difference)
    if a and b and a.fields and b.fields:
        a_set = set(a.fields)
        b_set = set(b.fields)
        only_a = sorted(a_set - b_set)
        only_b = sorted(b_set - a_set)
        if only_a or only_b:
            out.append("**Diff de campos**:")
            out.append("")
            if only_a:
                out.append(f"- só em REST ({len(only_a)}): " + ", ".join(only_a))
            if only_b:
                out.append(f"- só em Web ({len(only_b)}): " + ", ".join(only_b))
            out.append("")

    # samples
    if a and a.data:
        out.append("**Sample Method A** (truncado a 3 KB):")
        out.append("")
        out.append("```json")
        out.append(json.dumps(a.data, ensure_ascii=False, indent=2)[:3000])
        out.append("```")
        out.append("")
    if b and b.data:
        out.append("**Sample Method B** (truncado a 3 KB):")
        out.append("")
        out.append("```json")
        out.append(json.dumps(b.data, ensure_ascii=False, indent=2)[:3000])
        out.append("```")
        out.append("")

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
        print(f"ERRO: SEI_USUARIO não definido em {env_path}", file=sys.stderr)
        return 2

    a_result: Optional[RunResult] = None
    b_result: Optional[RunResult] = None

    # Method B primeiro: ele descobre o protocolo se não foi passado
    if not args.skip_b:
        print("→ Method B (Web scraper)…", file=sys.stderr)
        b_result = await bench_method_b(env, args.protocolo, args.warm, args.com_historico)
        if b_result.error:
            print(f"  ✗ {b_result.error}", file=sys.stderr)
        else:
            print(f"  ✓ {len(b_result.fields)} campos extraídos", file=sys.stderr)

    # protocolo a usar para REST
    protocolo_a = args.protocolo
    if not protocolo_a and b_result and not b_result.error:
        protocolo_a = b_result.data.get("protocolo")

    if not args.skip_a:
        if not protocolo_a:
            print("ERRO: --protocolo não foi passado e Method B falhou", file=sys.stderr)
            return 2
        print(f"→ Method A (REST) para {protocolo_a}…", file=sys.stderr)
        a_result = await bench_method_a(protocolo_a, args.warm, args.unit)
        if a_result.error:
            print(f"  ✗ {a_result.error}", file=sys.stderr)
        else:
            print(f"  ✓ {len(a_result.fields)} campos retornados", file=sys.stderr)

    # ajusta args.protocolo para o relatório
    if not args.protocolo and protocolo_a:
        args.protocolo = protocolo_a

    print()
    print(render_report(a_result, b_result, args))

    if a_result and a_result.error:
        return 3
    if b_result and b_result.error:
        return 3
    return 0


def main():
    parser = argparse.ArgumentParser(description="Benchmark consultar_processo REST vs Web")
    parser.add_argument("--protocolo", default="", help="protocolo formatado (ex: 50300.007186/2026-69). Se vazio, usa o primeiro da inbox.")
    parser.add_argument("--unit", default="110000037", help="ID da unidade SEI")
    parser.add_argument("--warm", type=int, default=5, help="warm runs (default 5)")
    parser.add_argument("--com-historico", action="store_true", help="incluir step procedimento_consultar_historico no Method B")
    parser.add_argument("--skip-a", action="store_true")
    parser.add_argument("--skip-b", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
