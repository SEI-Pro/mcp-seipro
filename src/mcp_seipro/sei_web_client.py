"""Cliente HTTP para o frontend web do SEI (scraper).

Alternativa de alta performance ao mod-wssei REST para operações de listagem
e navegação. Login via formulário SIP, navegação via páginas pré-assinadas
com `infra_hash` capturado na cadeia de redirects.

Performance medida (sei.antaq.gov.br, abril/2026):
- listar_processos: ~14.5 s (REST) → ~0.6 s (web) → 23× mais rápido
- consultar_processo: ~5.9 s (REST 2 calls) → ~0.9 s (web 2 calls) → 6× mais rápido

Limitações:
- Requer cadeia inicial de login (~3-4 s, uma vez por sessão)
- Layout dos campos depende da configuração de painel do usuário no SEI
- Sem suporte a 2FA ou CAPTCHA (aborta com erro)
- Específico para instâncias SEI com Infra v1.5x+ (login form com hdnToken)
"""

from __future__ import annotations

import logging
import os
import re
import warnings
from typing import Any, Optional
from urllib.parse import parse_qsl, urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class SEIWebClient:
    """Cliente HTTP assíncrono para o frontend web do SEI.

    Mantém uma sessão SIP autenticada e cacheia o `infra_hash` da inbox URL
    e o action+hidden fields do form principal de procedimento_controlar.

    Uso:
        client = SEIWebClient()
        await client.login()
        layout, rows = await client.listar_processos(detalhada=True)
        await client.close()

    A reutilização da sessão é o que torna esse client rápido — login custa
    ~3 s mas listagens subsequentes custam ~600 ms cada.
    """

    def __init__(self, **kwargs: Any) -> None:
        # Reusa as mesmas env vars do SEIClient REST
        sei_url = kwargs.get("sei_url", os.environ.get("SEI_URL", ""))
        # Deriva a raiz web a partir da URL da REST
        # Ex: https://sei.antaq.gov.br/sei/modulos/wssei/... → https://sei.antaq.gov.br
        if "/sei/" in sei_url:
            self.sei_root = sei_url.split("/sei/", 1)[0]
        else:
            self.sei_root = sei_url.rstrip("/")

        self._usuario = kwargs.get("sei_usuario", os.environ.get("SEI_USUARIO", ""))
        self._senha = kwargs.get("sei_senha", os.environ.get("SEI_SENHA", ""))
        # SEI_ORGAO no .env é o id da REST (geralmente "0"). O selOrgao do SIP
        # é descoberto dinamicamente do <select> na página de login.
        self._sigla_orgao = kwargs.get("sei_sigla_orgao", os.environ.get("SEI_SIGLA_ORGAO", "ANTAQ"))
        self._sigla_sistema = kwargs.get("sei_sigla_sistema", os.environ.get("SEI_SIGLA_SISTEMA", "SEI"))

        verify_ssl = kwargs.get("sei_verify_ssl", os.environ.get("SEI_VERIFY_SSL", "true"))
        if isinstance(verify_ssl, str):
            verify_ssl = verify_ssl.lower() != "false"
        if not verify_ssl:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")

        self.login_url = (
            f"{self.sei_root}/sip/login.php"
            f"?sigla_orgao_sistema={self._sigla_orgao}&sigla_sistema={self._sigla_sistema}"
        )

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
        # cache do form principal de procedimento_controlar (action + hidden fields)
        self._form_action: Optional[str] = None
        self._form_hidden: dict[str, str] = {}
        # cache de URLs de processos individuais (protocolo → href pré-assinado)
        self._trabalhar_links: dict[str, str] = {}

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """Faz login via formulário SIP e captura a inbox URL com infra_hash."""
        resp = await self._http.get(self.login_url)
        if resp.status_code != 200:
            raise RuntimeError(f"GET login.php retornou {resp.status_code}")

        html = resp.text
        if "g-recaptcha" in html or "h-captcha" in html or "hcaptcha" in html:
            raise RuntimeError("CAPTCHA presente no login — abortando.")
        if 'name="txtCodigo2FA"' in html or 'id="txtCodigo2FA"' in html:
            raise RuntimeError("2FA solicitado no login — não suportado.")

        soup = BeautifulSoup(html, "html.parser")
        usuario_input = soup.find("input", attrs={"name": "txtUsuario"})
        if usuario_input is None:
            raise RuntimeError("Campo txtUsuario não encontrado na página de login.")
        login_form = usuario_input.find_parent("form")
        if login_form is None:
            raise RuntimeError("<form> do login não encontrado.")

        sel_orgao = self._descobrir_sel_orgao(login_form, soup)

        form: dict[str, str] = {
            "txtUsuario": self._usuario,
            "pwdSenha": self._senha,
            "selOrgao": sel_orgao,
            # Crítico: o backend só processa o login se receber o par
            # name=value do botão submit. Sem isso, o PHP renderiza apenas
            # a página de login novamente sem mensagem de erro.
            "sbmLogin": "Acessar",
        }
        for h in login_form.find_all("input", type="hidden"):
            name = h.get("name")
            if name and h.get("value") is not None:
                form[name] = h["value"]
        sel_ctx = login_form.find("select", attrs={"name": "selContexto"})
        if sel_ctx is not None:
            ctx_val = ""
            for opt in sel_ctx.find_all("option"):
                if opt.get("selected") is not None:
                    ctx_val = opt.get("value") or ""
                    break
            form["selContexto"] = ctx_val

        action = login_form.get("action") or self.login_url
        post_url = urljoin(self.login_url, action)
        post_resp = await self._http.post(
            post_url,
            data=form,
            headers={"Referer": self.login_url, "Origin": self.sei_root},
        )
        if post_resp.status_code != 200:
            raise RuntimeError(f"POST login retornou {post_resp.status_code}")

        # após follow_redirects, resp.url é a URL final da cadeia
        # sip/login → sei/inicializar.php → sei/controlador.php?acao=procedimento_controlar
        final_url = post_resp.url
        qs = dict(parse_qsl(
            final_url.query.decode() if isinstance(final_url.query, bytes) else final_url.query
        ))
        if qs.get("acao") != "procedimento_controlar" or "infra_hash" not in qs:
            body = post_resp.text
            if 'name="txtUsuario"' in body or 'id="txtUsuario"' in body:
                raise RuntimeError(
                    "Login falhou: o servidor retornou a página de login novamente. "
                    "Verifique credenciais."
                )
            raise RuntimeError(f"URL inesperada após login: {final_url}")

        self._inbox_url = final_url
        # popula cache do form principal e dos links de processos a partir
        # da própria resposta do post-login (já contém o HTML da inbox)
        self._extract_main_form(post_resp.text)
        self._populate_trabalhar_links(post_resp.text)
        logger.info("SEI web login bem-sucedido — inbox capturada")

    def _descobrir_sel_orgao(self, login_form, soup) -> str:
        """Descobre o value do <select selOrgao> que corresponde ao órgão.

        Estratégia: option já selecionado → option com texto contendo a sigla
        do órgão → primeiro option não-vazio.
        """
        sel = login_form.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            sel = soup.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            raise RuntimeError("<select name='selOrgao'> não encontrado")

        # 1) option já selecionado
        for opt in sel.find_all("option"):
            if opt.get("selected") is not None and opt.get("value") and opt.get("value") != "null":
                return opt["value"]
        # 2) option cujo texto contém a sigla do órgão (ex: ANTAQ)
        sigla_upper = self._sigla_orgao.upper()
        for opt in sel.find_all("option"):
            if sigla_upper in opt.get_text(strip=True).upper() and opt.get("value") and opt.get("value") != "null":
                return opt["value"]
        # 3) primeiro option válido
        for opt in sel.find_all("option"):
            v = opt.get("value")
            if v and v != "null":
                return v
        raise RuntimeError("Nenhum <option> válido em selOrgao.")

    def _extract_main_form(self, html: str) -> None:
        """Captura action + hidden fields do form principal de procedimento_controlar.

        Esse form tem seu próprio `infra_hash` (diferente da inbox URL) e é
        usado para alternar visualização (resumida↔detalhada) e paginação.
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

    def _populate_trabalhar_links(self, inbox_html: str) -> None:
        """Mapeia protocolo → URL pré-assinada de procedimento_trabalhar.

        Sem isso não conseguimos navegar para um processo específico —
        a infra_hash é gerada server-side e não pode ser reconstruída.
        """
        soup = BeautifulSoup(inbox_html, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"acao=procedimento_trabalhar")):
            txt = a.get_text(strip=True)
            href = a.get("href", "").replace("&amp;", "&")
            if txt and href:
                self._trabalhar_links.setdefault(txt, href)

    # ------------------------------------------------------------------
    # Listar processos (Controle de Processos / inbox)
    # ------------------------------------------------------------------

    async def fetch_inbox(
        self,
        detalhada: bool = True,
        pagina: int = 0,
        apenas_meus: bool = False,
    ) -> tuple[int, str]:
        """Busca o HTML da página de Controle de Processos.

        - `detalhada=True`: força a visualização Detalhada via POST
          `hdnTipoVisualizacao=D`. A primeira chamada precisa de um GET prévio
          para descobrir o form action; chamadas subsequentes reaproveitam o cache.
        - `pagina=N>0`: POST com `hdnInfraPaginaAtual=N` + `hdnInfraHashCriterios`
          (cacheado da resposta anterior).
        - `apenas_meus=True`: POST `hdnMeusProcessos=M` (TA_MINHAS) — retorna
          apenas processos atribuídos ao usuário logado. Sempre passa o valor
          explicitamente (T ou M) para não herdar de chamadas anteriores.

        Retorna `(bytes, html)`.
        """
        if self._inbox_url is None:
            raise RuntimeError("login() não foi chamado antes de fetch_inbox().")

        # Caso simples: GET inicial sem detalhada/filtros/paginação
        if not detalhada and pagina == 0 and not apenas_meus and self._form_action is None:
            resp = await self._http.get(
                self._inbox_url,
                headers={"Referer": str(self._inbox_url)},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"fetch_inbox status={resp.status_code}")
            self._extract_main_form(resp.text)
            self._populate_trabalhar_links(resp.text)
            return len(resp.content), resp.text

        # Precisa do form action — fetch inicial se ainda não temos
        if self._form_action is None:
            seed = await self._http.get(
                self._inbox_url,
                headers={"Referer": str(self._inbox_url)},
            )
            if seed.status_code != 200:
                raise RuntimeError(f"seed inbox status={seed.status_code}")
            self._extract_main_form(seed.text)
            if self._form_action is None:
                raise RuntimeError("Form principal de procedimento_controlar não encontrado")

        # POST para alternar visualização / aplicar filtros / navegar páginas
        post_data = dict(self._form_hidden)
        if detalhada:
            post_data["hdnTipoVisualizacao"] = "D"
        # apenas_meus: sempre seta explicitamente (M ou T) para não herdar
        # estado de chamadas anteriores. Valores em AtividadeRN.php:
        # T=TODAS, M=MINHAS, D=DEFINIDAS, E=ESPECIFICAS.
        post_data["hdnMeusProcessos"] = "M" if apenas_meus else "T"
        if pagina > 0:
            post_data["hdnInfraPaginaAtual"] = str(pagina)

        post_url = urljoin(str(self._inbox_url), self._form_action)
        resp = await self._http.post(
            post_url,
            data=post_data,
            headers={"Referer": str(self._inbox_url)},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"fetch_inbox POST status={resp.status_code}")

        # detecta sessão expirada
        body = resp.text
        if 'name="txtUsuario"' in body or 'id="txtUsuario"' in body:
            logger.info("Sessão SEI expirou, re-logando")
            self._form_action = None
            self._form_hidden = {}
            await self.login()
            return await self.fetch_inbox(
                detalhada=detalhada, pagina=pagina, apenas_meus=apenas_meus
            )

        # atualiza cache do form (action e hashCriterios podem mudar entre páginas)
        self._extract_main_form(body)
        self._populate_trabalhar_links(body)
        return len(resp.content), body

    # ------------------------------------------------------------------
    # Consultar processo (página de detalhe)
    # ------------------------------------------------------------------

    async def consultar_processo(self, protocolo_formatado: str) -> dict:
        """Busca dados de um processo navegando pela cadeia de páginas web.

        Fluxo:
        1. Garante que o protocolo está no cache `_trabalhar_links` (links
           pré-assinados extraídos da inbox). Se não, faz fetch_inbox uma vez
           para popular.
        2. GET procedimento_trabalhar.php (frameset, ~70 ms) — confirma o
           id_procedimento e captura a URL assinada do iframe da árvore.
        3. GET procedimento_visualizar / arvore_montar.php (~1 s) — extrai o
           array Nos[] do JS e popula a lista de documentos.

        Retorna:
            {
              "id_procedimento": str,
              "protocolo": str,
              "tipo": str,           # da tooltip do nó raiz
              "documentos": [{id, label, tipo_no, link}, ...],
              "total_documentos": int,
              "relacionados": [str, ...],
            }

        Raises se o protocolo não for encontrado nos links da inbox.
        Para enriquecer com especificacao/assuntos/interessados (que só estão
        na REST), combine com `SEIClient.consultar_processo_completo()`.
        """
        if self._inbox_url is None:
            raise RuntimeError("login() não foi chamado antes de consultar_processo()")

        # garante que o protocolo está no cache de links da inbox
        if protocolo_formatado not in self._trabalhar_links:
            # tenta uma fetch da inbox (pode trazer mais links)
            await self.fetch_inbox(detalhada=False)
            if protocolo_formatado not in self._trabalhar_links:
                raise RuntimeError(
                    f"Protocolo {protocolo_formatado!r} não encontrado nos "
                    f"links da inbox. Disponíveis: "
                    f"{list(self._trabalhar_links.keys())[:5]}..."
                )

        trab_url = urljoin(str(self._inbox_url), self._trabalhar_links[protocolo_formatado])

        # Step 1: procedimento_trabalhar.php (frameset, leve)
        r1 = await self._http.get(trab_url, headers={"Referer": str(self._inbox_url)})
        if r1.status_code != 200:
            raise RuntimeError(f"procedimento_trabalhar status={r1.status_code}")

        # detecta sessão expirada
        if 'name="txtUsuario"' in r1.text or 'id="txtUsuario"' in r1.text:
            logger.info("Sessão SEI expirou, re-logando")
            self._form_action = None
            self._form_hidden = {}
            await self.login()
            return await self.consultar_processo(protocolo_formatado)

        soup_fs = BeautifulSoup(r1.text, "html.parser")
        ifr = soup_fs.find("iframe", id="ifrArvore")
        if ifr is None:
            raise RuntimeError("ifrArvore não encontrado no frameset")
        arvore_src = ifr.get("src", "").replace("&amp;", "&")
        arvore_url = urljoin(str(r1.url), arvore_src)

        # extrai id_procedimento da URL do trabalhar
        m_id = re.search(r"id_procedimento=(\d+)", str(r1.url))
        id_proc = m_id.group(1) if m_id else None

        # Step 2: procedimento_visualizar (arvore_montar.php)
        r2 = await self._http.get(arvore_url, headers={"Referer": trab_url})
        if r2.status_code != 200:
            raise RuntimeError(f"procedimento_visualizar status={r2.status_code}")

        nos = parse_arvore_nos(r2.text)

        result: dict[str, Any] = {
            "id_procedimento": id_proc or "",
            "protocolo": protocolo_formatado,
        }
        if nos:
            root = nos[0]
            result["tipo"] = root.get("tooltip", "")
            result["icone"] = root.get("icone", "")
            # documentos = todos os Nos exceto o root e exceto PASTA
            docs = [
                {
                    "id": n["id"],
                    "label": n.get("label", ""),
                    "tipo_no": n.get("tipo_no", ""),
                    "link": n.get("link", ""),
                }
                for n in nos[1:]
                if n.get("tipo_no") not in ("PASTA",)
            ]
            result["documentos"] = docs
            result["total_documentos"] = len(docs)

        # processos relacionados (cards na sidebar do arvore_montar)
        soup_arv = BeautifulSoup(r2.text, "html.parser")
        rels: list[str] = []
        for div_rel in soup_arv.find_all("div", class_=re.compile(r"cardRelacionado")):
            link_rel = div_rel.find("a")
            if link_rel:
                rels.append(link_rel.get_text(strip=True))
        if rels:
            result["relacionados"] = rels

        return result

    async def listar_processos(
        self,
        detalhada: bool = True,
        pagina: int = 0,
        apenas_meus: bool = False,
        tipo: str = "",
        filtro: str = "",
    ) -> dict:
        """Lista processos da caixa da unidade atual via web scraper.

        Filtros server-side (POST form fields):
        - `apenas_meus=True`: hdnMeusProcessos=M (apenas atribuídos ao usuário logado)

        Filtros client-side (após fetch, em substring case-insensitive):
        - `tipo`: filtra pela coluna "Tipo" (apenas detalhada)
        - `filtro`: filtra por substring em qualquer campo de texto

        Retorna dict no formato:
            {
              "processos": [{...}, ...],
              "total_itens": N,            # total no servidor (antes de filtros client-side)
              "total_filtrados": N,        # após filtros client-side
              "pagina_atual": int,
              "tem_proxima": bool,
              "layout": "detalhada"|"resumida",
            }
        """
        _, html = await self.fetch_inbox(
            detalhada=detalhada, pagina=pagina, apenas_meus=apenas_meus
        )
        layout, rows = parse_inbox(html)

        # total_itens: vem dos hidden fields hdn{Selecao}NroItens (capturados
        # pelo _extract_main_form via fetch_inbox). Esses campos têm o total
        # da seleção atual no servidor, não só da página visível.
        if layout == "detalhada":
            total_servidor = int(self._form_hidden.get("hdnDetalhadoNroItens", "0") or "0")
        else:
            total_servidor = (
                int(self._form_hidden.get("hdnRecebidosNroItens", "0") or "0")
                + int(self._form_hidden.get("hdnGeradosNroItens", "0") or "0")
            )
        if total_servidor == 0:
            total_servidor = len(rows)

        # Filtros client-side: aplicados após o parse, sobre os rows.
        rows_filtrados = rows
        if tipo:
            tipo_lower = tipo.lower()
            rows_filtrados = [
                r for r in rows_filtrados
                if tipo_lower in (r.get("Tipo", "") or "").lower()
            ]
        if filtro:
            filtro_lower = filtro.lower()
            rows_filtrados = [
                r for r in rows_filtrados
                if any(
                    filtro_lower in str(v).lower()
                    for v in r.values()
                    if isinstance(v, (str, int, float))
                )
            ]

        return {
            "processos": rows_filtrados,
            "total_itens": total_servidor,
            "total_filtrados": len(rows_filtrados),
            "pagina_atual": pagina,
            "tem_proxima": len(rows) > 0 and (pagina + 1) * max(len(rows), 1) < total_servidor,
            "layout": layout,
        }


# ---------------------------------------------------------------------------
# Parsers de HTML (independentes de instância)
# ---------------------------------------------------------------------------

def parse_arvore_nos(html: str) -> list[dict]:
    """Extrai o array `Nos[]` do JS de arvore_montar.php.

    Cada nó é construído como `Nos[i] = new infraArvoreNo(tipo, id, pai, link,
    target, label, tooltip, icone, ...)`. Retorna lista de dicts com as
    primeiras 8 posições nomeadas. O primeiro elemento (Nos[0]) é a raiz —
    o próprio processo.
    """
    out: list[dict] = []
    for m in re.finditer(
        r"Nos\[\d+\]\s*=\s*new infraArvoreNo\(([^;]*?)\);",
        html,
        re.S,
    ):
        args_str = m.group(1)
        # tokenizer simples: separa por vírgula respeitando aspas
        args: list[str] = []
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
            elif ch == ",":
                args.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            args.append(cur.strip())

        def unquote(s: str) -> str:
            s = s.strip()
            if s in ("null", ""):
                return ""
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                return s[1:-1]
            return s

        if len(args) >= 7:
            out.append({
                "tipo_no": unquote(args[0]),
                "id": unquote(args[1]),
                "pai": unquote(args[2]),
                "link": unquote(args[3]),
                "target": unquote(args[4]),
                "label": unquote(args[5]),
                "tooltip": unquote(args[6]),
                "icone": unquote(args[7]) if len(args) > 7 else "",
            })
    return out


_RE_TOOLTIP = re.compile(
    r"infraTooltipMostrar\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)"
)


def _extract_tooltip(link_tag, row: dict) -> None:
    """Extrai especificacao e tipo do onmouseover do link do processo.

    O SEI renderiza um tooltip JS em TODOS os links de processo da inbox:
        onmouseover="return infraTooltipMostrar('Especificação','Tipo Processual')"

    Esse tooltip contém a especificação INDEPENDENTE de a coluna estar
    habilitada no painel — é sempre renderizado.
    """
    mouseover = link_tag.get("onmouseover", "")
    m = _RE_TOOLTIP.search(mouseover)
    if m:
        especificacao = m.group(1).strip()
        tipo_tooltip = m.group(2).strip()
        if especificacao:
            row["especificacao"] = especificacao
        if tipo_tooltip and "Tipo" not in row:
            row["tipo"] = tipo_tooltip


def parse_inbox(html: str) -> tuple[str, list[dict]]:
    """Parseia o HTML de procedimento_controlar.php e extrai lista de processos.

    Suporta dois layouts:
    - **Detalhada**: tabela única `tblProcessosDetalhado` com colunas
      configuráveis (Tipo, Especificação, Interessados, etc.)
    - **Resumida**: duas tabelas `tblProcessosRecebidos` + `tblProcessosGerados`
      (default do SEI quando o usuário não trocou para Detalhada)

    Retorna tupla `(layout, rows)` onde layout in {'detalhada','resumida','desconhecido'}.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []

    tbl = soup.find("table", id="tblProcessosDetalhado")
    if tbl:
        first_tr = tbl.find("tr")
        if first_tr is None:
            return ("detalhada", [])
        ths = first_tr.find_all("th")
        headers = [th.get_text(" ", strip=True) for th in ths]
        # 4 primeiras colunas tipicamente sem header textual:
        # checkbox / status icons / Processo / Atribuição
        col_names: list[str] = []
        for i, h in enumerate(headers):
            if h:
                col_names.append(h)
            else:
                col_names.append(
                    {0: "_check", 1: "icones", 2: "_processo", 3: "atribuicao"}.get(i, f"col{i}")
                )

        for tr in tbl.find_all("tr", id=re.compile(r"^P\d+$")):
            tds = tr.find_all("td", recursive=False)
            row: dict[str, Any] = {"id_procedimento": tr["id"][1:]}
            link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
            if link is not None:
                row["protocolo"] = link.get_text(" ", strip=True)
                # Especificação + tipo estão no tooltip do link do processo:
                # onmouseover="return infraTooltipMostrar('Especificação','Tipo')"
                # Disponível INDEPENDENTE de a coluna estar habilitada no painel.
                _extract_tooltip(link, row)
            if len(tds) >= 2:
                icones = []
                for img in tds[1].find_all("img"):
                    title = img.get("title") or img.get("alt") or ""
                    if title:
                        icones.append(title.strip())
                if icones:
                    row["icones"] = icones
            for i, name in enumerate(col_names):
                if name.startswith("_") or name == "icones":
                    continue
                if i < len(tds):
                    val = tds[i].get_text(" ", strip=True)
                    if val:
                        row[name] = val
            rows.append(row)
        return ("detalhada", rows)

    # Resumida — fallback
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
            link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
            if link is not None:
                row["protocolo"] = link.get_text(" ", strip=True)
                _extract_tooltip(link, row)
            if len(tds) >= 2:
                icones = []
                for img in tds[1].find_all("img"):
                    title = img.get("title") or img.get("alt") or ""
                    if title:
                        icones.append(title.strip())
                if icones:
                    row["icones"] = icones
            if len(tds) >= 4:
                atrib_text = tds[-1].get_text(" ", strip=True)
                if atrib_text:
                    row["atribuicao"] = atrib_text
            rows.append(row)

    if found_any:
        return ("resumida", rows)
    return ("desconhecido", [])
