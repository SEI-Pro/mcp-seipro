"""Cliente HTTP para o frontend web do SEI (scraper).

Alternativa de alta performance ao mod-wssei REST para operações de listagem
e navegação. Login via formulário SIP, navegação via páginas pré-assinadas
com `infra_hash` capturado na cadeia de redirects.

Performance medida (sei.antaq.gov.br, abril/2026):
- listar_processos: ~14.5 s (REST) → ~0.6 s (web) → 23x mais rápido
- consultar_processo: ~5.9 s (REST 2 calls) → ~0.9 s (web 2 calls) → 6x mais rápido

Limitações:
- Requer cadeia inicial de login (~3-4 s, uma vez por sessão)
- Layout dos campos depende da configuração de painel do usuário no SEI
- Sem suporte a 2FA ou CAPTCHA (aborta com erro)
- Específico para instâncias SEI com Infra v1.5x+ (login form com hdnToken)
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from urllib.parse import quote as _quote

import httpx
from bs4 import BeautifulSoup, Tag

if TYPE_CHECKING:
    from types import ModuleType

from todos.backends.models import (
    DocumentoExternoInclusaoWeb,
    NovoProcessoWeb,
    OpcoesTramitacaoWeb,
    SEIWebClientConfig,
)
from todos.exceptions import (
    SEIAuthError,
    SEICaptchaError,
    SEIConnectionError,
    SEICredenciaisError,
    SEIError,
    SEINotFoundError,
    SEIParseError,
    SEIValidationError,
)
from todos.settings import get_settings

logger = logging.getLogger(__name__)

_keyring: ModuleType | None = None
try:
    import keyring as _keyring
except ImportError:
    logger.debug("keyring not available; password will be read from SEI_SENHA env var")

# TTL do cache da árvore do processo (links assinados valem a sessão inteira;
# o TTL curto limita apenas a janela de staleness do conteúdo da árvore)
_ARVORE_CACHE_TTL = 30.0
SEI_WEB_PAGE_SIZE = 10

# ---------------------------------------------------------------------------
# Nomes de campos do form frmProcedimentoCadastro (criar/alterar processo).
# O JS do SEI vira hdnFlag*Cadastro de '1'→'2' antes de submeter; com '1' o
# servidor apenas re-exibe o form sem salvar (no-op silencioso). Se o SEI
# renomear esses campos em versão futura, basta atualizar aqui.
# ---------------------------------------------------------------------------
_FIELD_FLAG_PROC_CADASTRO = "hdnFlagProcedimentoCadastro"  # JS: '1'→'2' obrigatório
_FIELD_FLAG_DOC_CADASTRO = "hdnFlagDocumentoCadastro"  # idem para documentos
_FIELD_ASSUNTOS = "hdnAssuntos"  # formato: id±texto separados por ¥ (U+00A5)
_FIELD_NIVEL_ACESSO = "rdoNivelAcesso"  # 0=público, 1=restrito, 2=sigiloso
_FIELD_NIVEL_ACESSO_GLOBAL = "hdnStaNivelAcessoGlobal"  # espelho do rdoNivelAcesso
_FIELD_NIVEL_ACESSO_LOCAL = "hdnStaNivelAcessoLocal"  # idem, escopo documento
_FIELD_DESCRICAO = "txtDescricao"  # especificação do processo (máx. 100 chars)
_FIELD_INTERESSADOS = "hdnInteressadosProcedimento"  # mesmo formato de hdnAssuntos
_FIELD_FILTRO_TIPO_PROC = "hdnFiltroTipoProcedimento"  # 'T'=todos, 'F'=favoritos
_FIELD_ID_TIPO_PROC = "hdnIdTipoProcedimento"  # id do tipo selecionado no fluxo escolher_tipo

# ---------------------------------------------------------------------------
# HTML layout invariants — column counts for specific SEI tables.
# These reflect fixed server-rendered layouts; update if the SEI template changes.
# ---------------------------------------------------------------------------
_UNIT_TABLE_MIN_CELLS = 2  # troca-de-unidade row: id_col + sigla_col
_EXPECTED_SIBLING_COUNT = 2  # link-pair detection in tree navigation
_HISTORY_TABLE_COLS = 4  # histórico de atribuições: data/hora/usuario/ação
_BLOCK_TABLE_MIN_COLS = 2  # bloco table: at least descrição + estado
_INBOX_PAGE_CAP = 500  # server-side cap on hdnDetalhadoNroItens
_ONE_KB = 1024  # bytes per kilobyte (file size formatting)
_UPLOAD_RESP_MIN_PARTS = 2  # upload response must have nome_upload + at least one field
_UPLOAD_RESP_IDX_TAM = 3  # upload response field index: tamanho
_UPLOAD_RESP_IDX_DH = 4  # upload response field index: data_hora
_DOC_LINK_ARGS_MIN = 7  # minimum JS args in objTabelaAnexos.adicionar([...])
_INBOX_TABLE_MIN_COLS = 2  # inbox row: protocolo + icones columns
_INBOX_ATRIB_COL = 4  # inbox row column index for atribuição text
_META_TABLE_PAIR = 2  # metadata table: exactly key + value cells
_META_KEY_MAX_LEN = 60  # maximum length for a valid metadata key string
_SIG_TABLE_COLS = 3  # assinaturas/ciências table: signatário + cargo + data
_STATUS_TABLE_MIN_COLS = 2  # unidades/sobrestamentos status table minimum cols
_ENTRY_TABLE_MIN_COLS = 2  # histórico entry table: at least tipo col
_ENTRY_TABLE_OBS_COL = 3  # histórico entry table index for observação col


def _decode_response(content: bytes, content_type: str) -> str:
    """Decode HTTP response bytes using charset from Content-Type, defaulting to iso-8859-1."""
    charset = "iso-8859-1"
    for part in content_type.split(";"):
        if "charset=" in part.lower():
            charset = part.split("=", 1)[1].strip().strip('"')
            break
    try:
        return content.decode(charset)
    except (UnicodeDecodeError, LookupError):
        logger.warning(
            "Charset %r inválido ou incompatível com os bytes; fallback iso-8859-1/replace",
            charset,
        )
        return content.decode("iso-8859-1", "replace")


def _tag_str(tag: Tag | None, attr: str, default: str = "") -> str:
    """Return a BS4 tag attribute as plain str (Tag.get returns str|list|None)."""
    if tag is None:
        return default
    v = tag.get(attr, default)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return v[0] if v else default
    return default


def _check(r: httpx.Response) -> None:
    """Raise a typed SEIError for any non-2xx response."""
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise SEIAuthError(str(exc)) from exc
        if status == httpx.codes.NOT_FOUND:
            raise SEINotFoundError(str(exc)) from exc
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise SEIConnectionError(str(exc)) from exc
        raise SEIValidationError(str(exc)) from exc


def _safe_int(val: str, default: int = 0) -> int:
    """Convert val to int, returning default on ValueError (e.g. server returns 'N/A')."""
    try:
        return int(val)
    except ValueError:
        return default


def _extrair_erro_sei(html: str) -> str | None:
    """Extrai mensagem de erro do HTML do SEI, se houver.

    O SEI exibe erros em divs/spans com classes infraMsg ou infraMensagemErro,
    ou como alertas JavaScript. Retorna None se não houver erro detectável.
    """
    soup = BeautifulSoup(html, "html.parser")
    for el in (
        soup.find(class_="infraMsg"),
        soup.find(class_="infraMensagemErro"),
        soup.find(id="divInfraMensagem"),
        soup.find(class_="alert-danger"),
    ):
        if el is not None:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt
    # JavaScript alert("mensagem de erro") — busca apenas em <script>.
    # Scripts de validação de formulário definem funções com alert() para feedback
    # do usuário — não são erros do servidor. Scripts de erro SEI são bare (sem funções).
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        src = script.get_text()
        if re.search(r"\bfunction\s+\w+\s*\(", src):
            continue
        # [^<>'"]{10,300} evita match de HTML embutido nos scripts (nomes de assinantes)
        m = re.search(r"alert\(['\"]([^<>'\"]{10,300})['\"]", src)
        if m:
            return m.group(1)
    return None


def _extrair_submit_btn(form: Tag) -> tuple[str, str] | None:
    """Extrai o par (name, value) do botão submit de um form.

    O PHP do SEI exige o par name=value do botão submit no POST; sem ele
    ignora o form silenciosamente. Válido para input[type=submit] e button.
    """
    btn = form.find("input", type="submit") or form.find("button", type="submit")
    if btn is not None:
        name = _tag_str(btn, "name")
        if name:
            value = _tag_str(btn, "value") or btn.get_text(strip=True) or "Enviar"
            return name, value
    return None


def _coletar_estado_form(form: Tag) -> dict[str, str]:
    """Coleta o estado atual de todos os campos de um form para reenvio.

    Inclui inputs (exceto submit; radios/checkboxes apenas se marcados),
    a opção selecionada de cada select e o conteúdo de cada textarea. O
    chamador sobrescreve apenas os campos que deseja alterar.
    """
    estado: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = _tag_str(inp, "name")
        if not name:
            continue
        itype = _tag_str(inp, "type", "text").lower()
        if itype in {"radio", "checkbox"}:
            if inp.has_attr("checked"):
                estado[name] = _tag_str(inp, "value")
        elif itype != "submit":
            estado[name] = _tag_str(inp, "value")
    for sel in form.find_all("select"):
        name = _tag_str(sel, "name")
        if not name:
            continue
        opt = sel.find("option", selected=True)
        estado[name] = _tag_str(opt, "value") if isinstance(opt, Tag) else ""
    for ta in form.find_all("textarea"):
        name = _tag_str(ta, "name")
        if name:
            estado[name] = ta.get_text()
    return estado


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

    def __init__(self, config: SEIWebClientConfig | None = None) -> None:
        """Initialise from a SEIWebClientConfig (or env vars when config is None/default)."""
        cfg = config or SEIWebClientConfig()
        settings = get_settings()
        # Reusa as mesmas env vars do SEIClient REST
        _sei_url = cfg.sei_url or settings.sei_url
        # SEI_WEB_URL permite modo web-only (sem mod-wssei) apontando direto para
        # a raiz do SEI (ex: https://sei.orgao.gov.br). Tem precedência sobre SEI_URL.
        _sei_web_url = cfg.sei_web_url or settings.sei_web_url
        if _sei_web_url:
            self.sei_root = _sei_web_url.rstrip("/")
        elif "/sei/" in _sei_url:
            # Deriva raiz a partir da URL da REST
            # Ex: https://sei.antaq.gov.br/sei/modulos/wssei/... → https://sei.antaq.gov.br
            self.sei_root = _sei_url.split("/sei/", 1)[0]
        else:
            self.sei_root = _sei_url.rstrip("/")

        self._usuario = cfg.sei_usuario or settings.sei_usuario

        _env_senha = settings.sei_senha
        self._senha = cfg.sei_senha or _env_senha
        # Rastreia a fonte da senha para mensagem de erro acionável
        self._senha_source_hint = (
            "SEI_SENHA (variável de ambiente)"
            if (not cfg.sei_senha and _env_senha)
            else "senha configurada"
        )
        # Pre-compute keyring key so login() can do the actual lookup in a thread
        self._keyring_user: str | None = None
        if not self._senha and self._usuario:
            instance_url = (
                self.sei_root.replace("https://", "")
                .replace("http://", "")
                .strip()
                .rstrip("/")
                .lower()
            )
            self._keyring_user = (
                f"{self._usuario}@{instance_url}" if instance_url else self._usuario
            )
        # Cópia que NÃO é zerada após o lookup: permite reler o keyring quando a
        # senha cacheada é rejeitada (ex.: senha trocada externamente, sem restart).
        self._keyring_user_persist = self._keyring_user

        # SEI_ORGAO no .env é o id da REST (geralmente "0"). O selOrgao do SIP
        # é descoberto dinamicamente do <select> na página de login.
        self._sei_orgao = (
            cfg.sei_orgao
        )  # stored for API parity with SEIClient; not used by web flow
        self._sigla_orgao = cfg.sei_sigla_orgao or settings.sei_sigla_orgao
        self._sigla_sistema = cfg.sei_sigla_sistema or settings.sei_sigla_sistema
        # SEI_SIGLA_ORGAO_SISTEMA: parâmetro da URL do SIP login (ex: "RO" para Rondônia).
        # Quando não definido, usa SEI_SIGLA_ORGAO (mantém compatibilidade p/ instâncias
        # onde sigla_orgao_sistema == sigla do órgão no selOrgao, ex: ANTAQ).
        _sigla_orgao_sistema = (
            cfg.sei_sigla_orgao_sistema or settings.sei_sigla_orgao_sistema or self._sigla_orgao
        )

        _ca_bundle = cfg.sei_ca_bundle or settings.sei_ca_bundle
        _raw_verify: str | bool = (
            cfg.sei_verify_ssl if cfg.sei_verify_ssl is not None else settings.sei_verify_ssl
        )
        _verify: bool | str
        if _ca_bundle:
            _verify = _ca_bundle  # use explicit CA bundle path (preferred over boolean bypass)
        else:
            _verify = (
                _raw_verify.lower() != "false"
                if isinstance(_raw_verify, str)
                else bool(_raw_verify)
            )
        if _verify is False:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")

        self.login_url = (
            f"{self.sei_root}/sip/login.php"
            f"?sigla_orgao_sistema={_sigla_orgao_sistema}&sigla_sistema={self._sigla_sistema}"
        )

        self._http = httpx.AsyncClient(
            verify=_verify,
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=10.0, read=45.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
        )
        self._inbox_url: httpx.URL | None = None
        self._unidade_atual: dict[str, str] | None = None
        self._nome_usuario: str | None = None
        self._id_usuario: str | None = None
        self._orgao_usuario: str | None = None
        # cache do form principal de procedimento_controlar (action + hidden fields)
        self._form_action: str | None = None
        self._form_hidden: dict[str, str] = {}
        # cache de URLs de processos individuais (protocolo → href pré-assinado)
        self._trabalhar_links: dict[str, str] = {}
        # URL do form de pesquisa rápida (protocolo_pesquisa_rapida + infra_hash)
        self._pesquisa_rapida_action: str | None = None
        # cache curto da árvore (protocolo → (ts, (html, url))): evita refetch
        # quando várias ações usam a mesma árvore em sequência (ex: ler vários
        # documentos do mesmo processo, ou fallback interno→externo)
        self._arvore_cache: dict[str, tuple[float, tuple[str, str]]] = {}
        # serializa leituras/escritas nos caches mutáveis — previne check-then-set
        # concorrente entre coroutines chamando os mesmos métodos em paralelo
        self._cache_lock = asyncio.Lock()
        # lock separado para _arvore_cache (não mantido durante fetch HTTP)
        self._arvore_lock: asyncio.Lock = asyncio.Lock()
        # lock separado para _trabalhar_links e _form_hidden/_form_action
        self._form_lock: asyncio.Lock = asyncio.Lock()

    @property
    def nome_usuario(self) -> str:
        """Nome do usuário autenticado, vazio antes do login."""
        return self._nome_usuario or ""

    @property
    def id_usuario(self) -> str:
        """ID interno do usuário no SEI."""
        return self._id_usuario or self._usuario

    @property
    def orgao_usuario(self) -> str:
        """Sigla do órgão/unidade do usuário."""
        return self._orgao_usuario or ""

    @property
    def itens_painel(self) -> int:
        """Total de itens no painel (0 antes do primeiro listar_processos)."""
        raw = self._form_hidden.get("hdnDetalhadoNroItens", "0") or "0"
        return _safe_int(raw)

    @property
    def is_authenticated(self) -> bool:
        """True após login bem-sucedido (inbox_url capturada)."""
        return self._inbox_url is not None

    def _reset_session_state(self) -> None:
        """Limpa todo o estado de sessão para garantir retry limpo."""
        self._inbox_url = None
        self._form_action = None
        self._form_hidden = {}
        self._trabalhar_links = {}
        self._pesquisa_rapida_action = None
        self._arvore_cache = {}
        self._unidade_atual = None
        self._nome_usuario = None
        self._id_usuario = None
        self._orgao_usuario = None

    def limpar_senha(self) -> None:
        """Sobrescreve a senha em memória após uso."""
        self._senha = ""

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def ensure_authenticated(self) -> None:
        """Garante sessão SIP ativa; faz login automaticamente se necessário."""
        if self._inbox_url is None:
            await self.login()

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    async def _ler_senha_keyring(self, keyring_user: str) -> str | None:
        """Lê a senha do keyring (com timeout); None em caso de erro/ausência."""
        if _keyring is None:
            return None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_keyring.get_password, "todos-mcp", keyring_user),
                timeout=5.0,
            )
        except TimeoutError:
            logger.warning("Timeout ao buscar senha do keyring (>5s); use SEI_SENHA como fallback")
            return None
        except AttributeError as e:
            # Linux SecretService/dbus backend raises this on headless sessions — expected
            logger.info("_ler_senha_keyring: keyring indisponível: %s", e)
            return None
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("_ler_senha_keyring: erro ao buscar senha: %s", e)
            return None

    async def login(self, *, _retry_keyring: bool = True) -> None:
        """Faz login via formulário SIP e captura a inbox URL com infra_hash.

        Se a senha veio do keyring e for rejeitada, relê o keyring (a senha pode
        ter sido atualizada externamente) e refaz o login uma vez — sem precisar
        reiniciar o processo.
        """
        _senha_source = self._senha_source_hint
        if not self._senha and self._keyring_user:
            keyring_user = self._keyring_user
            self._keyring_user = None  # prevent concurrent / empty-string repeated lookups
            if _keyring is not None:
                try:
                    senha = await asyncio.wait_for(
                        asyncio.to_thread(_keyring.get_password, "todos-mcp", keyring_user),
                        timeout=5.0,
                    )
                    if senha:
                        self._senha = senha
                        _senha_source = f"keyring (chave: {keyring_user!r})"
                    else:
                        _senha_source = f"keyring (chave {keyring_user!r} não encontrada)"
                    # _keyring_user stays None: keyring answered definitively (found or not found)
                except TimeoutError:
                    self._keyring_user = keyring_user  # restore: transient timeout, allow retry
                    logger.warning(
                        "Timeout ao buscar senha do keyring (>5s); use SEI_SENHA como fallback"
                    )
                except (OSError, RuntimeError, ValueError, AttributeError) as e:
                    # AttributeError: Linux SecretService/dbus backend raises this on headless sessions
                    self._keyring_user = keyring_user  # restore: transient error, allow retry
                    logger.warning("Não foi possível obter a senha do keyring: %s", e)

        if not self.sei_root:
            msg = (
                "Nenhuma URL do SEI configurada. Defina SEI_URL (API REST "
                "mod-wssei) ou SEI_WEB_URL (raiz web, ex: https://sei.orgao.gov.br)."
            )
            raise RuntimeError(msg)
        resp = await self._http.get(self.login_url)
        _check(resp)

        html = resp.text
        # Verifica CAPTCHA: busca o elemento HTML real, não o seletor CSS
        # (o CSS inline sempre contém "#txtInfraCaptcha {...}" — falso positivo)
        if (
            "g-recaptcha" in html
            or "h-captcha" in html
            or "hcaptcha" in html
            or 'name="txtInfraCaptcha"' in html
            or 'id="txtInfraCaptcha"' in html
        ):
            msg = (
                "CAPTCHA presente no login — o scraper não resolve CAPTCHA. "
                "Acesse o SEI pelo navegador uma vez para liberar a sessão."
            )
            raise SEICaptchaError(msg)
        if 'name="txtCodigo2FA"' in html or 'id="txtCodigo2FA"' in html:
            msg = (
                "2FA solicitado no login — não suportado pelo scraper. "
                "Conclua o 2FA pelo navegador e tente novamente."
            )
            raise SEICaptchaError(msg)

        soup = BeautifulSoup(html, "html.parser")
        usuario_input = soup.find("input", attrs={"name": "txtUsuario"})
        if usuario_input is None:
            msg = "Campo txtUsuario não encontrado na página de login."
            raise SEIParseError(msg)
        login_form = usuario_input.find_parent("form")
        if login_form is None:
            msg = "<form> do login não encontrado."
            raise SEIParseError(msg)

        sel_orgao = self._descobrir_sel_orgao(login_form, soup)

        form: dict[str, str] = {
            "txtUsuario": self._usuario,
            "pwdSenha": self._senha,
            "selOrgao": sel_orgao,
        }
        for h in login_form.find_all("input", type="hidden"):
            name = _tag_str(h, "name")
            if name and h.get("value") is not None:
                form[name] = _tag_str(h, "value")

        # O PHP exige o par name=value do botão submit; sem ele ignora o POST.
        # Detecta o botão real do formulário (varia por instância:
        # sbmLogin=Acessar no ANTAQ, sbmAcessar=ACESSAR no RO, etc.)
        submit_btn = login_form.find("button", type="submit") or login_form.find(
            "input", type="submit"
        )
        if submit_btn is not None:
            btn_name = _tag_str(submit_btn, "name")
            if btn_name:
                btn_value = (
                    _tag_str(submit_btn, "value") or submit_btn.get_text(strip=True) or "Acessar"
                )
                form[btn_name] = btn_value
        else:
            # fallback para instâncias mais antigas
            form["sbmLogin"] = "Acessar"

        # Corrige hdnAcao: o JS seta o valor correto antes de submeter via
        # acaoLogin(N) no onsubmit. Ex: onsubmit="return acaoLogin(2);"
        # O HTML tem value="1" (padrão), mas ação=2 é o login com usuário/senha.
        onsubmit = _tag_str(login_form, "onsubmit")
        m_acao = re.search(r"acaoLogin\((\d+)\)", onsubmit)
        if m_acao and "hdnAcao" in form:
            form["hdnAcao"] = m_acao.group(1)
        sel_ctx = login_form.find("select", attrs={"name": "selContexto"})
        if sel_ctx is not None:
            ctx_val = ""
            for opt in sel_ctx.find_all("option"):
                if opt.get("selected") is not None:
                    ctx_val = _tag_str(opt, "value")
                    break
            form["selContexto"] = ctx_val

        action = _tag_str(login_form, "action") or self.login_url
        post_url = urljoin(self.login_url, action)
        _login_host = urlparse(self.login_url).netloc
        _action_host = urlparse(post_url).netloc
        if _action_host and _action_host != _login_host:
            logger.warning(
                "Ação de login redirecionaria para host diferente: %s (esperado %s) — usando URL de login padrão",
                _action_host,
                _login_host,
            )
            post_url = self.login_url
        post_resp = await self._http.post(
            post_url,
            data=form,
            headers={"Referer": self.login_url, "Origin": self.sei_root},
        )
        _check(post_resp)

        # após follow_redirects, resp.url é a URL final da cadeia
        # sip/login → sei/inicializar.php → sei/controlador.php?acao=procedimento_controlar
        final_url = post_resp.url
        qs = dict(
            parse_qsl(
                final_url.query.decode() if isinstance(final_url.query, bytes) else final_url.query
            )
        )
        if qs.get("acao") != "procedimento_controlar" or "infra_hash" not in qs:
            body = post_resp.text
            if 'name="txtUsuario"' in body or 'id="txtUsuario"' in body:
                # Senha cacheada rejeitada: se a fonte é o keyring, relê (pode ter
                # sido trocada externamente) e refaz o login uma vez — sem restart.
                if _retry_keyring and self._keyring_user_persist:
                    nova = await self._ler_senha_keyring(self._keyring_user_persist)
                    if nova and nova != self._senha:
                        logger.info("Senha do keyring mudou desde o último login; refazendo.")
                        self._senha = nova
                        self._reset_session_state()
                        await self.login(_retry_keyring=False)
                        return
                if not self._senha:
                    dica = (
                        "SEI_SENHA está vazia e nenhuma senha foi encontrada no keyring. "
                        "Execute `todos setup` para configurar as credenciais."
                    )
                elif "keyring" in _senha_source:
                    dica = (
                        f"A senha obtida do {_senha_source} foi recusada pelo SEI. "
                        f"Execute `todos setup` para regravar a senha correta no keyring, "
                        f"ou defina SEI_SENHA na configuração do MCP."
                    )
                else:
                    acao = (
                        "Verifique o valor de SEI_SENHA na configuração do MCP."
                        if "SEI_SENHA" in _senha_source
                        else "Verifique e atualize a senha nas configurações."
                    )
                    dica = f"A senha em {_senha_source} foi recusada pelo SEI. {acao}"
                msg = (
                    f"Credenciais rejeitadas pelo SEI "
                    f"(usuário: {self._usuario!r}, órgão selOrgao={form.get('selOrgao', '?')!r}). "
                    f"{dica}"
                )
                raise SEICredenciaisError(msg)
            logger.warning("SEI login: URL inesperada após redirecionamento: %s", final_url)
            msg = "URL inesperada após login — o servidor SEI não redirecionou para a caixa de entrada."
            raise SEIParseError(msg)

        _soup = BeautifulSoup(post_resp.text, "html.parser")
        async with self._cache_lock:
            self._inbox_url = final_url
            self._arvore_cache.clear()
            # popula cache do form principal e dos links de processos a partir
            # da própria resposta do post-login (já contém o HTML da inbox)
            self._extract_main_form(post_resp.text, _soup)
            self._extract_pesquisa_rapida(post_resp.text, _soup)
            self._populate_trabalhar_links(post_resp.text, _soup)
            self._extract_unidade_atual(post_resp.text, _soup)
        logger.info("SEI web login bem-sucedido — inbox capturada")

    def _descobrir_sel_orgao(self, login_form: Tag, soup: BeautifulSoup) -> str:
        """Descobre o value do <select selOrgao> que corresponde ao órgão.

        Estratégia: option já selecionado → option com texto contendo a sigla
        do órgão → primeiro option não-vazio.
        """
        sel = login_form.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            sel = soup.find("select", attrs={"name": "selOrgao"})
        if sel is None:
            msg = "<select name='selOrgao'> não encontrado"
            raise SEIParseError(msg)

        # 1) option já selecionado
        for opt in sel.find_all("option"):
            if opt.get("selected") is not None and opt.get("value") and opt.get("value") != "null":
                return str(opt["value"])
        # 2) option cujo texto contém a sigla do órgão (ex: ANTAQ)
        sigla_upper = self._sigla_orgao.upper()
        for opt in sel.find_all("option"):
            if (
                sigla_upper in opt.get_text(strip=True).upper()
                and opt.get("value")
                and opt.get("value") != "null"
            ):
                return str(opt["value"])
        # 3) primeiro option válido
        for opt in sel.find_all("option"):
            v = opt.get("value")
            if v and v != "null":
                return str(v)
        msg = "Nenhum <option> válido em selOrgao."
        raise SEIParseError(msg)

    def _extract_pesquisa_rapida(self, html: str, soup: BeautifulSoup | None = None) -> None:
        """Captura a action do form de pesquisa rápida (protocolo_pesquisa_rapida)."""
        if soup is None:
            soup = BeautifulSoup(html, "html.parser")
        for f in soup.find_all("form"):
            action = _tag_str(f, "action")
            if "protocolo_pesquisa_rapida" in action:
                self._pesquisa_rapida_action = action.replace("&amp;", "&")
                return

    def _extract_main_form(self, html: str, soup: BeautifulSoup | None = None) -> None:
        """Captura action + hidden fields do form principal de procedimento_controlar.

        Esse form tem seu próprio `infra_hash` (diferente da inbox URL) e é
        usado para alternar visualização (resumida↔detalhada) e paginação.
        """
        if soup is None:
            soup = BeautifulSoup(html, "html.parser")
        for f in soup.find_all("form"):
            action = _tag_str(f, "action")
            if "procedimento_controlar" in action:
                self._form_action = action.replace("&amp;", "&")
                self._form_hidden = {}
                for h in f.find_all("input", type="hidden"):
                    name = _tag_str(h, "name")
                    if name:
                        self._form_hidden[name] = _tag_str(h, "value")
                return

    def _populate_trabalhar_links(self, inbox_html: str, soup: BeautifulSoup | None = None) -> None:
        """Mapeia protocolo → URL pré-assinada de procedimento_trabalhar.

        Sem isso não conseguimos navegar para um processo específico —
        a infra_hash é gerada server-side e não pode ser reconstruída.
        """
        if soup is None:
            soup = BeautifulSoup(inbox_html, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"acao=procedimento_trabalhar")):
            txt = a.get_text(strip=True)
            href = _tag_str(a, "href").replace("&amp;", "&")
            if txt and href:
                self._trabalhar_links.setdefault(txt, href)

    def _extract_unidade_atual(self, html: str, soup: BeautifulSoup | None = None) -> None:
        """Extrai a unidade ativa do seletor exibido no cabecalho do SEI."""
        if soup is None:
            soup = BeautifulSoup(html, "html.parser")
        unit_link = soup.find(
            "a",
            id=re.compile(r"unidade", re.IGNORECASE),
            title=True,
        )
        if unit_link is None:
            return

        sigla = unit_link.get_text(" ", strip=True)
        nome = _tag_str(unit_link, "title").strip()
        if not sigla and not nome:
            return

        # Extrai nome, id e órgão do usuário via lnkUsuarioSistema
        # formato do title: "NOME COMPLETO (ID/SIGLA_ORGAO)"
        user_link = soup.find("a", id="lnkUsuarioSistema")
        if user_link is not None:
            title = _tag_str(user_link, "title").strip()
            m = re.match(r"^(.+?)\s+\((\d+)/(\w+)\)$", title)
            if m:
                self._nome_usuario, self._id_usuario, self._orgao_usuario = (
                    m.group(1),
                    m.group(2),
                    m.group(3),
                )
            else:
                logger.warning("Formato inesperado no título da página de login: %r", title)

        unidade: dict[str, str] = {"sigla": sigla, "nome": nome}
        if self._inbox_url is not None:
            query = dict(parse_qsl(str(self._inbox_url.query)))
            id_unidade = query.get("infra_unidade_atual", "")
            if id_unidade:
                unidade["id_unidade"] = id_unidade
        self._unidade_atual = unidade

    async def unidade_atual(self) -> dict[str, str]:
        """Retorna id, sigla e nome da unidade ativa na sessao web."""
        await self.ensure_authenticated()
        if self._unidade_atual is None:
            _, html = await self.fetch_inbox(detalhada=False)
            self._extract_unidade_atual(html)
        if self._unidade_atual is None:
            msg = "Nao foi possivel identificar a unidade ativa na pagina do SEI."
            raise SEIParseError(msg)
        return dict(self._unidade_atual)

    async def _fetch_unit_switch_form(self) -> tuple[str, Tag]:
        """Abre a tela de troca de unidade e retorna URL e formulario."""
        await self.ensure_authenticated()
        _, html = await self.fetch_inbox(detalhada=False)
        soup = BeautifulSoup(html, "html.parser")
        unit_link = soup.find("a", id="lnkInfraUnidade")
        if unit_link is None:
            msg = "Link de troca de unidade não encontrado"
            raise SEIParseError(msg)

        onclick = _tag_str(unit_link, "onclick")
        match = re.search(r"window\.location\.href='([^']+)'", onclick)
        if not match:
            msg = "URL de troca de unidade nao encontrada."
            raise SEIParseError(msg)

        switch_url = urljoin(str(self._inbox_url), match.group(1))
        response = await self._http.get(switch_url, headers={"Referer": str(self._inbox_url)})
        _check(response)

        switch_soup = BeautifulSoup(response.text, "html.parser")
        form = switch_soup.find("form", id="frmInfraSelecaoUnidade")
        if form is None:
            msg = "Formulário de troca de unidade não encontrado"
            raise SEIParseError(msg)
        return str(response.url), form

    @staticmethod
    def _units_from_form(form: Tag) -> list[dict[str, str]]:
        """Extrai a lista de unidades a partir do formulario de troca de unidade."""
        units: list[dict[str, str]] = []
        for radio in form.find_all("input", attrs={"name": "chkInfraItem"}):
            id_unidade = _tag_str(radio, "value")
            row = radio.find_parent("tr")
            if not id_unidade or row is None:
                continue
            cells = [" ".join(td.get_text(" ", strip=True).split()) for td in row.find_all("td")]
            values = [cell for cell in cells if cell]
            if len(values) < _UNIT_TABLE_MIN_CELLS:
                continue
            units.append({"id_unidade": id_unidade, "sigla": values[0], "nome": values[1]})
        return units

    async def listar_unidades(self) -> list[dict[str, str]]:
        """Lista unidades acessiveis ao usuario pela tela web de troca."""
        _, form = await self._fetch_unit_switch_form()
        return self._units_from_form(form)

    @staticmethod
    def _build_unit_post(form: Tag, target_id: str) -> dict[str, str]:
        """Constroi o payload POST para submeter o formulario de troca de unidade."""
        data: dict[str, str] = {}
        for field in form.find_all("input"):
            name = _tag_str(field, "name")
            if name and _tag_str(field, "type").lower() == "hidden":
                data[name] = _tag_str(field, "value")
        data["selInfraUnidades"] = target_id
        return data

    def _verificar_troca(self, current: dict[str, str], target: dict[str, str]) -> None:
        """Lanca RuntimeError se o SEI nao confirmou a troca de unidade."""
        current_id = current.get("id_unidade")
        if current_id:
            if current_id != target["id_unidade"]:
                msg = f"SEI nao confirmou a troca para {target['sigla']}."
                raise SEIParseError(msg)
        elif current.get("sigla", "").casefold() != target["sigla"].casefold():
            # Fallback: verify by sigla when id_unidade is absent from the redirect URL
            msg = f"SEI nao confirmou a troca para {target['sigla']}."
            raise SEIParseError(msg)

    async def trocar_unidade(self, referencia: str) -> dict[str, str]:
        """Troca a unidade ativa por ID ou sigla usando a interface web."""
        form_url, form = await self._fetch_unit_switch_form()
        units = self._units_from_form(form)

        ref = referencia.strip().casefold()
        matches = [
            u for u in units if u["id_unidade"].casefold() == ref or u["sigla"].casefold() == ref
        ]
        if not matches:
            msg = f"Unidade {referencia!r} nao encontrada entre as unidades acessiveis."
            raise SEIParseError(msg)

        target = matches[0]
        post_url = urljoin(form_url, _tag_str(form, "action"))
        data = self._build_unit_post(form, target["id_unidade"])
        response = await self._http.post(post_url, data=data, headers={"Referer": form_url})
        _check(response)

        _soup = BeautifulSoup(response.text, "html.parser")
        async with self._cache_lock:
            self._inbox_url = response.url
            self._form_action = None
            self._form_hidden = {}
            self._trabalhar_links.clear()
            self._pesquisa_rapida_action = None
            self._arvore_cache.clear()
            self._unidade_atual = None
            self._extract_main_form(response.text, _soup)
            self._extract_pesquisa_rapida(response.text, _soup)
            self._populate_trabalhar_links(response.text, _soup)
            self._extract_unidade_atual(response.text, _soup)

        current = await self.unidade_atual()
        self._verificar_troca(current, target)
        return current

    # ------------------------------------------------------------------
    # Listar processos (Controle de Processos / inbox)
    # ------------------------------------------------------------------

    async def fetch_inbox(
        self,
        pagina: int = 0,
        *,
        detalhada: bool = True,
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
        await self.ensure_authenticated()
        inbox_url = str(self._inbox_url)

        # Caso simples: GET inicial sem detalhada/filtros/paginação
        async with self._form_lock:
            _form_action_snapshot = self._form_action
        if not detalhada and pagina == 0 and not apenas_meus and _form_action_snapshot is None:
            resp = await self._http.get(
                inbox_url,
                headers={"Referer": inbox_url},
            )
            _check(resp)
            _soup = BeautifulSoup(resp.text, "html.parser")
            async with self._form_lock:
                self._extract_main_form(resp.text, _soup)
                self._populate_trabalhar_links(resp.text, _soup)
                self._extract_unidade_atual(resp.text, _soup)
            return len(resp.content), resp.text

        # Precisa do form action — fetch inicial se ainda não temos
        async with self._form_lock:
            _form_action_snapshot = self._form_action
        if _form_action_snapshot is None:
            seed = await self._http.get(
                inbox_url,
                headers={"Referer": inbox_url},
            )
            _check(seed)
            async with self._form_lock:
                self._extract_main_form(seed.text)
                _form_action_snapshot = self._form_action
            if _form_action_snapshot is None:
                msg = "Form principal de procedimento_controlar não encontrado"
                raise SEIParseError(msg)

        # POST para alternar visualização / aplicar filtros / navegar páginas
        async with self._form_lock:
            post_data = dict(self._form_hidden)
            post_url = urljoin(str(self._inbox_url), self._form_action)
        if detalhada:
            post_data["hdnTipoVisualizacao"] = "D"
        # apenas_meus: sempre seta explicitamente (M ou T) para não herdar
        # estado de chamadas anteriores. Valores em AtividadeRN.php:
        # T=TODAS, M=MINHAS, D=DEFINIDAS, E=ESPECIFICAS.
        post_data["hdnMeusProcessos"] = "M" if apenas_meus else "T"
        if pagina > 0:
            post_data["hdnInfraPaginaAtual"] = str(pagina)

        resp = await self._http.post(
            post_url,
            data=post_data,
            headers={"Referer": str(self._inbox_url)},
        )
        _check(resp)

        # detecta sessão expirada
        body = resp.text
        if 'name="txtUsuario"' in body or 'id="txtUsuario"' in body:
            logger.info("Sessão SEI expirou, re-logando")
            async with self._form_lock:
                self._form_action = None
                self._form_hidden = {}
            await self.login()
            return await self.fetch_inbox(
                detalhada=detalhada, pagina=pagina, apenas_meus=apenas_meus
            )

        # atualiza cache do form (action e hashCriterios podem mudam entre páginas)
        _soup = BeautifulSoup(body, "html.parser")
        async with self._form_lock:
            self._extract_main_form(body, _soup)
            self._extract_pesquisa_rapida(body, _soup)
            self._populate_trabalhar_links(body, _soup)
            self._extract_unidade_atual(body, _soup)
        return len(resp.content), body

    # ------------------------------------------------------------------
    # Consultar processo (página de detalhe)
    # ------------------------------------------------------------------

    async def pesquisar_processo(self, protocolo: str, *, _relogin: bool = True) -> None:
        """Busca um processo pelo protocolo via pesquisa rápida do SEI.

        Popula `_trabalhar_links` com a URL pré-assinada do processo encontrado,
        permitindo navegação posterior mesmo para processos fora da caixa atual.

        Raises RuntimeError se o processo não for encontrado.
        """
        await self.ensure_authenticated()

        if self._pesquisa_rapida_action is None:
            await self.fetch_inbox(detalhada=False)
            if self._pesquisa_rapida_action is None:
                msg = "Form de pesquisa rápida não encontrado no HTML da inbox"
                raise SEIParseError(msg)

        post_url = urljoin(str(self._inbox_url), self._pesquisa_rapida_action)
        r = await self._http.post(
            post_url,
            data={"txtPesquisaRapida": protocolo},
            headers={"Referer": str(self._inbox_url)},
        )
        _check(r)

        # detecta sessão expirada — a pesquisa rápida pode retornar o login
        # quando _pesquisa_rapida_action estava cacheado mas a sessão expirou
        if 'name="txtUsuario"' in r.text or 'id="txtUsuario"' in r.text:
            if not _relogin:
                msg = "Sessão SEI expirou após re-login na pesquisa rápida — falha de autenticação."
                raise SEIError(msg)
            logger.info("pesquisar_processo: sessão SEI expirou, re-logando via fetch_inbox")
            async with self._form_lock:
                self._pesquisa_rapida_action = None
            await self.fetch_inbox(detalhada=False)
            return await self.pesquisar_processo(protocolo, _relogin=False)

        final_url = str(r.url)
        sei_base = f"{self.sei_root}/sei/"

        if "procedimento_trabalhar" in final_url:
            # Redirecionou direto para o processo
            href = final_url.replace(sei_base, "") if final_url.startswith(sei_base) else final_url
            async with self._form_lock:
                self._trabalhar_links[protocolo] = href
            return None

        # Página de resultados (protocolo_pesquisar) — busca o link correto
        soup = BeautifulSoup(r.text, "html.parser")
        proto_norm = protocolo.replace(" ", "")
        for a in soup.find_all("a", href=re.compile(r"procedimento_trabalhar")):
            txt = a.get_text(strip=True).replace(" ", "")
            if proto_norm == txt:
                href = _tag_str(a, "href").replace("&amp;", "&")
                async with self._form_lock:
                    self._trabalhar_links[protocolo] = href
                return None

        # Tenta também via links com id_procedimento (tooltip ou linha da tabela)
        for a in soup.find_all("a", href=re.compile(r"procedimento_trabalhar")):
            href = _tag_str(a, "href").replace("&amp;", "&")
            async with self._form_lock:
                self._trabalhar_links[protocolo] = href
            return None

        msg = (
            f"Processo {protocolo!r} não encontrado na pesquisa. "
            "Verifique se o número está correto e se você tem acesso."
        )
        raise SEINotFoundError(msg)

    async def pesquisar_processos_web(
        self,
        q: str = "",
        descricao: str = "",
        data_inicio: str = "",
        data_fim: str = "",
        pagina: int = 0,
    ) -> dict[str, Any]:
        """Pesquisa processos via formulário web do SEI (sem mod-wssei).

        Parâmetros:
        - q: texto livre (busca no conteúdo dos documentos indexados)
        - descricao: texto na especificação/descrição do processo
        - data_inicio / data_fim: filtro de data de inclusão (DD/MM/AAAA)
        - pagina: página de resultados (0-indexed, 10 itens/página)

        Retorna lista de dicts com: protocoloFormatado, tipo, trecho, unidade, usuario, inclusao.

        Dicas de uso:
        - Use aspas para frase exata: q='"NOME COMPLETO" aposentadoria' é muito mais
          preciso do que palavras soltas — reduz falsos positivos drasticamente.
        - A busca varre todo o SEI (não filtrada por unidade do usuário).
        - Máximo de 10 resultados por página; use pagina=1, 2, ... para avançar.
        """
        await self.ensure_authenticated()

        if self._pesquisa_rapida_action is None:
            await self.fetch_inbox(detalhada=False)
            if self._pesquisa_rapida_action is None:
                msg = "Form de pesquisa rápida não encontrado"
                raise SEIParseError(msg)

        # Passo 1: POST vazio para obter hidden fields com infra_hash válido.
        # Tenta até 2 vezes em caso de sessão expirada.
        search_form = None
        r0 = None
        for attempt in range(2):
            r0 = await self._http.post(
                urljoin(str(self._inbox_url), self._pesquisa_rapida_action),
                data={"txtPesquisaRapida": ""},
                headers={"Referer": str(self._inbox_url)},
            )
            _check(r0)
            soup0 = BeautifulSoup(r0.text, "html.parser")
            for f in soup0.find_all("form"):
                if "acao_origem=protocolo_pesquisa_rapida" in _tag_str(f, "action"):
                    search_form = f
                    break
            if search_form is not None:
                break
            if attempt == 0:
                # Sessão expirada: invalida o cache de sessão para forçar re-login
                # (ensure_authenticated só re-login quando _inbox_url is None)
                self._inbox_url = None
                self._form_action = None
                self._pesquisa_rapida_action = None
                await self.ensure_authenticated()
                await self.fetch_inbox(detalhada=False)

        if search_form is None or r0 is None:
            msg = "Formulário de pesquisa avançada não encontrado"
            raise SEIParseError(msg)

        action = urljoin(
            str(r0.url),
            _tag_str(search_form, "action").replace("&amp;", "&").split("#")[0],
        )
        hidden = {
            _tag_str(h, "name"): _tag_str(h, "value")
            for h in search_form.find_all("input", type="hidden")
            if _tag_str(h, "name")
        }

        # Passo 2: submete a busca avançada (SEI exibe 10 resultados/página; hdnInicio = offset)
        post_data: dict[str, str] = {
            **hidden,
            "rdoPesquisarEm": "P",
            "chkSinConsiderarDocumentos": "S",
            "q": q,
            "txtDescricaoPesquisa": descricao,
            "txtDataInicio": data_inicio,
            "txtDataFim": data_fim,
            "hdnInicio": str(pagina * SEI_WEB_PAGE_SIZE),
        }

        r1 = await self._http.post(action, data=post_data, headers={"Referer": str(r0.url)})
        _check(r1)
        soup1 = BeautifulSoup(r1.text, "html.parser")

        # Passo 3: parse dos resultados.
        # Âncora: <a href="...procedimento_trabalhar..."> com texto = protocolo.
        # Para cada protocolo, a <tr> pai é a linha de resultado; os 2 próximos
        # <tr> irmãos contêm trecho e metadados (unidade/usuário/data).
        results: list[dict[str, str]] = []
        seen: set[str] = set()

        for a in soup1.find_all("a", href=re.compile(r"procedimento_trabalhar")):
            prot = a.get_text(strip=True)
            if not prot or prot in seen:
                continue
            seen.add(prot)

            row0 = a.find_parent("tr")
            if row0 is None:
                continue

            siblings: list[Tag] = []
            for sib in row0.find_next_siblings("tr"):
                if sib.find("a", href=re.compile(r"procedimento_trabalhar")):
                    break
                siblings.append(sib)
                if len(siblings) == _EXPECTED_SIBLING_COUNT:
                    break

            tipo_cell = row0.find("td")
            tipo_text = tipo_cell.get_text(" ", strip=True) if tipo_cell is not None else ""
            # tipo_text é "Tipo Nº protocolo" — extrai só o tipo (antes do Nº).
            # Limite de comprimento antes do regex evita ReDoS com input malformado.
            tipo = re.sub(r"\s+N[ºo°]?\s*\S+.*$", "", tipo_text[:200]).strip()

            trecho = siblings[0].get_text(" ", strip=True) if len(siblings) > 0 else ""
            meta = siblings[1].get_text(" ", strip=True) if len(siblings) > 1 else ""

            # campo meta: "Unidade: SIGLA Usuário: CPF Inclusão: DD/MM/AAAA"
            unidade_m = re.search(r"Unidade:\s*(.+?)(?=\s+Usuário:|\s+Inclusão:|$)", meta)
            usuario_m = re.search(r"Usuário:\s*(\S+)", meta)
            inclusao_m = re.search(r"Inclusão:\s*(\S+)", meta)

            results.append(
                {
                    "protocoloFormatado": prot,
                    "tipo": tipo,
                    "trecho": trecho,
                    "unidade": unidade_m.group(1).strip() if unidade_m else "",
                    "usuario": usuario_m.group(1) if usuario_m else "",
                    "inclusao": inclusao_m.group(1) if inclusao_m else "",
                }
            )

        total_itens: int | None = None
        try:
            pattern = re.compile(
                r"^\s*(?:Resultado\s+da\s+pesquisa:\s*)?(\d+)\s+"
                r"(?:processo(?:\(s\)|s)?\s+encontrado(?:\(s\)|s)?|resultados?)(?:\.|\s)*$",
                re.IGNORECASE,
            )
            for el in soup1.find_all(string=pattern):
                text_val = str(el).strip()
                m = pattern.match(text_val)
                if m:
                    total_itens = int(m.group(1))
                    break
        except (ValueError, IndexError, AttributeError):
            logger.warning(
                "Falha ao parsear total de itens da pesquisa — possível mudança de layout do SEI",
                exc_info=True,
            )

        return {"processos": results, "total_itens": total_itens}

    async def consultar_processo(self, protocolo_formatado: str, *, _relogin: bool = True) -> dict:
        """Busca dados de um processo navegando pela cadeia de páginas web.

        Fluxo:
        1. Garante que o protocolo está no cache `_trabalhar_links` (links
           pré-assinados extraídos da inbox). Se não, faz fetch_inbox uma vez
           para popular.
        2. Chama `_arvore_do_processo` para obter a árvore totalmente expandida.
        3. Parseia a árvore para extrair documentos e processos relacionados.
        """
        await self.ensure_authenticated()

        html_arvore, url_arvore = await self._arvore_do_processo(protocolo_formatado)

        m_id = re.search(r"id_procedimento=(\d+)", url_arvore)
        id_proc = m_id.group(1) if m_id else None

        nos = parse_arvore_nos(html_arvore)

        result: dict[str, Any] = {
            "id_procedimento": id_proc or "",
            "protocolo": protocolo_formatado,
            "url_arvore": url_arvore,
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
                    "acoes_html": n.get("acoes_html", ""),
                    "src": n.get("src", ""),
                }
                for n in nos[1:]
                if n.get("tipo_no") != "PASTA"
            ]
            result["documentos"] = docs
            result["total_documentos"] = len(docs)

        # processos relacionados (cards na sidebar do arvore_montar)
        soup_arv = BeautifulSoup(html_arvore, "html.parser")
        rels: list[str] = []
        for div_rel in soup_arv.find_all("div", class_=re.compile(r"cardRelacionado")):
            link_rel = div_rel.find("a")
            if link_rel:
                rels.append(link_rel.get_text(strip=True))
        if rels:
            result["relacionados"] = rels

        return result

    async def listar_documentos(self, protocolo_formatado: str) -> dict:
        """Lista documentos de um processo via web scraper (arvore_montar).

        Chama `consultar_processo()` internamente e parseia os labels dos nós
        para extrair tipo do documento, sigla da unidade e número SEI.

        Retorna:
            {
              "processo": {"protocolo": str, "id_procedimento": str, "tipo": str},
              "total_documentos": int,
              "documentos": [{ordem, id, nome_composto, tipo_documento, sigla_unidade,
                              numero_sei, tipo_no, icone}, ...],
            }

        ~10x mais rápido que a REST /documento/listar (9.7 s → ~1 s).
        """
        proc = await self.consultar_processo(protocolo_formatado)

        docs_raw = proc.get("documentos", [])
        docs = []
        for i, d in enumerate(docs_raw):
            label = d.get("label", "")
            parsed = _parse_doc_label(label)
            docs.append(
                {
                    "ordem": i + 1,
                    "id": d["id"],
                    "nome_composto": label,
                    **parsed,
                    "tipo_no": d.get("tipo_no", ""),
                    "icone": d.get("icone", ""),
                }
            )

        return {
            "processo": {
                "protocolo": protocolo_formatado,
                "id_procedimento": proc.get("id_procedimento", ""),
                "tipo": proc.get("tipo", ""),
            },
            "total_documentos": len(docs),
            "documentos": docs,
        }

    async def _garantir_link_trabalhar(self, protocolo: str) -> str:
        """Garante que _trabalhar_links[protocolo] existe e retorna o href."""
        async with self._form_lock:
            in_cache = protocolo in self._trabalhar_links
        if not in_cache:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            in_cache = protocolo in self._trabalhar_links
        if not in_cache:
            await self.pesquisar_processo(protocolo)
        async with self._form_lock:
            href = self._trabalhar_links.get(protocolo)
        if not href:
            msg = f"Processo {protocolo!r} não encontrado"
            raise SEINotFoundError(msg)
        return href

    async def _arvore_do_processo(self, protocolo: str) -> tuple[str, str]:
        """Navega trabalhar→frameset→arvore; retorna (html_arvore, url_arvore).

        Resultado cacheado por _ARVORE_CACHE_TTL segundos; ações que alteram
        o processo invalidam a entrada via _invalidar_arvore().

        Usa double-checked locking: verifica o cache sem o lock primeiro,
        depois adquire o lock para verificar novamente antes de escrever.
        O lock NÃO é mantido durante o fetch HTTP para não serializar requisições.
        """
        async with self._arvore_lock:
            em_cache = self._arvore_cache.get(protocolo)
            if em_cache is not None:
                ts, resultado = em_cache
                if time.monotonic() - ts <= _ARVORE_CACHE_TTL:
                    return resultado
                del self._arvore_cache[protocolo]

        href = await self._garantir_link_trabalhar(protocolo)
        trab_url = urljoin(str(self._inbox_url), href)

        r1 = await self._http.get(trab_url, headers={"Referer": str(self._inbox_url)})
        _check(r1)
        if 'name="txtUsuario"' in r1.text or 'id="txtUsuario"' in r1.text:
            async with self._form_lock:
                self._form_action = None
                self._form_hidden = {}
                self._trabalhar_links.pop(protocolo, None)
            await self.login()
            return await self._arvore_do_processo(protocolo)

        soup_fs = BeautifulSoup(r1.text, "html.parser")
        ifr = soup_fs.find("iframe", id="ifrArvore")
        if ifr is None:
            msg = "ifrArvore não encontrado no frameset"
            raise SEIParseError(msg)
        arvore_src = _tag_str(ifr, "src").replace("&amp;", "&")
        arvore_url = urljoin(str(r1.url), arvore_src)

        r2 = await self._http.get(arvore_url, headers={"Referer": trab_url})
        _check(r2)

        # Detecta sessão expirada na árvore (servidor retorna 200 com página de login)
        if "txtUsuario" in r2.text:
            _r2_soup = BeautifulSoup(r2.text, "html.parser")
            if _r2_soup.find("input", attrs={"name": "txtUsuario"}) is not None:
                async with self._form_lock:
                    self._form_action = None
                    self._form_hidden = {}
                    self._trabalhar_links.pop(protocolo, None)
                await self.login()
                return await self._arvore_do_processo(protocolo)

        arvore_html = r2.text
        arvore_url_str = str(r2.url)

        # 1. Parse initial nodes
        nos = parse_arvore_nos(arvore_html)
        # Próximo índice livre para Nos[N] em chunks expandidos; previne colisão
        # de acoes_map/src_map quando o HTML cacheado é re-parseado (cada chunk
        # tem seu próprio Nos[0..K] e indices duplicados sobrescreveriam o mapa).
        next_no_offset = _max_no_index(arvore_html) + 1

        # 2. Expand folders via POST (if any)
        # Look for the JS Pastas array in arvore_html
        pasta_matches = re.findall(
            r"Pastas\[(\d+)\]\s*=\s*\[\];.*?Pastas\[\1\]\['link'\]\s*=\s*['\"](.*?)['\"];.*?Pastas\[\1\]\['protocolos'\]\s*=\s*['\"](.*?)['\"];",
            arvore_html,
            re.DOTALL,
        )
        expanded_js_chunks = []
        if pasta_matches:
            seen_ids = {n["id"] for n in nos}
            for idx_str, link, protocols in pasta_matches:
                p_idx = int(idx_str)
                url_pasta = urljoin(arvore_url_str, link.replace("&amp;", "&").replace(" ", "%20"))
                data_pasta = {
                    "hdnArvore": "",
                    "hdnPastaAtual": f"PASTA{p_idx}",
                    "hdnProtocolos": protocols,
                }
                r_pasta = await self._http.post(
                    url_pasta, data=data_pasta, headers={"Referer": arvore_url_str}
                )
                if r_pasta.is_success:
                    chunk_renum, next_no_offset = _renumerar_nos_chunk(r_pasta.text, next_no_offset)
                    expanded_js_chunks.append(chunk_renum)
                    folder_nos = parse_arvore_nos(chunk_renum)
                    for n in folder_nos:
                        nid = n.get("id", "")
                        if nid and nid not in seen_ids:
                            seen_ids.add(nid)
                            nos.append(n)

        # 3. Resolve AGUARDE nodes via BFS
        pending_aguarde = [
            n
            for n in nos[1:]
            if n.get("tipo_no") == "AGUARDE" and not str(n.get("pai", "")).startswith("PASTA")
        ]
        if pending_aguarde:
            seen_ids = {n["id"] for n in nos}
            while pending_aguarde:
                aguarde = pending_aguarde.pop(0)
                link = aguarde.get("link", "").replace("&amp;", "&")
                if link:
                    page_url = urljoin(arvore_url_str, link)
                else:
                    m_page = re.search(r"\d+$", aguarde.get("id", ""))
                    if not m_page:
                        continue
                    sep = "&" if "?" in arvore_url_str else "?"
                    page_url = f"{arvore_url_str}{sep}pagina_arvore={m_page.group()}"
                r_pag = await self._http.get(page_url, headers={"Referer": arvore_url_str})
                if r_pag.is_success:
                    chunk_renum, next_no_offset = _renumerar_nos_chunk(r_pag.text, next_no_offset)
                    expanded_js_chunks.append(chunk_renum)
                    page_nos = parse_arvore_nos(chunk_renum)
                    for n in page_nos:
                        nid = n.get("id", "")
                        if nid and nid not in seen_ids:
                            seen_ids.add(nid)
                            if n.get("tipo_no") == "AGUARDE" and not str(
                                n.get("pai", "")
                            ).startswith("PASTA"):
                                pending_aguarde.append(n)
                            else:
                                nos.append(n)

        if expanded_js_chunks:
            arvore_html += "\n/* EXPANDED PASTAS AND PAGES */\n" + "\n".join(expanded_js_chunks)

        resultado = (arvore_html, arvore_url_str)
        async with self._arvore_lock:
            self._arvore_cache[protocolo] = (time.monotonic(), resultado)
        return resultado

    def _invalidar_arvore(self, protocolo: str) -> None:
        """Remove a árvore cacheada de um processo (após ação que a altera)."""
        self._arvore_cache.pop(protocolo, None)

    async def executar_acao_processo(
        self,
        protocolo: str,
        nome_acao: str,
        campos_extras: dict[str, str] | None = None,
    ) -> dict:
        """Executa uma ação simples em um processo via scraper web do SEI.

        Fluxo: trabalhar → arvore_montar → link(acao=nome_acao) → GET [→ POST form]

        Parâmetros:
        - protocolo: número SEI formatado (ex: "50300.018905/2018-67")
        - nome_acao: nome da ação no controlador (ex: "procedimento_concluir")
        - campos_extras: campos adicionais para o POST do form de confirmação

        Retorna dict com {"ok": True, "mensagem": str} ou levanta RuntimeError.
        """
        await self.ensure_authenticated()

        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        m = re.search(
            rf"(controlador\.php\?acao={re.escape(nome_acao)}[^\"'\s]*infra_hash=[a-f0-9]+)",
            html_arvore,
        )
        if not m:
            msg = (
                f"Ação '{nome_acao}' não encontrada no menu do processo. "
                "Verifique se você tem permissão para esta ação e se o "
                "processo está no estado correto."
            )
            raise SEINotFoundError(msg)

        acao_url = urljoin(sei_base, m.group(1).replace("&amp;", "&"))
        r = await self._http.get(acao_url, headers={"Referer": url_arvore})
        _check(r)

        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)

        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is not None:
            action = _tag_str(form, "action").replace("&amp;", "&")
            post_url = urljoin(str(r.url), action) if action else str(r.url)
            post_data = _coletar_estado_form(form)
            # O PHP do SEI ignora o POST silenciosamente sem o par name=value do
            # botão submit; muitos forms de ação usam <button type=submit>, que
            # não é capturado como campo do form.
            sbm = _extrair_submit_btn(form)
            if sbm:
                post_data[sbm[0]] = sbm[1]
            if campos_extras:
                post_data.update(campos_extras)
            # POST em ISO-8859-1 (charset do SEI); `data=` usaria UTF-8 (httpx
            # default) e gravaria texto acentuado como mojibake.
            r2 = await self._http.post(
                post_url,
                content=urlencode(post_data, encoding="iso-8859-1", errors="replace").encode(
                    "ascii"
                ),
                headers={
                    "Referer": str(r.url),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            _check(r2)
            body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
            erro2 = _extrair_erro_sei(body2)
            if erro2:
                raise SEIConnectionError(erro2)
        else:
            # Sem form: pode ser ação que executa direto via GET (ex: redirect imediato).
            # Valida que não há erro oculto e loga para facilitar debug.
            if _extrair_erro_sei(body):  # já checado acima mas re-verifica body completo
                msg = f"Ação '{nome_acao}' falhou sem form de confirmação."
                raise SEINotFoundError(msg)
            logger.debug(
                "executar_acao_processo: ação '%s' concluída via GET (sem form)", nome_acao
            )

        self._invalidar_arvore(protocolo)
        return {
            "ok": True,
            "mensagem": f"Ação '{nome_acao}' executada com sucesso.",
            "protocolo": protocolo,
        }

    async def obter_form_acao(
        self,
        protocolo: str,
        nome_acao: str,
    ) -> dict:
        """Retorna os campos e opções disponíveis no form de uma ação.

        Útil para descobrir os IDs válidos de selects (ex: selUsuario, selMarcador)
        antes de submeter o form com executar_acao_processo.

        Retorna dict com:
        - "campos": {name: value} dos hidden inputs pré-preenchidos
        - "selects": {name: [{value, texto}, ...]} dos campos select
        - "textareas": [name, ...] dos campos de texto livre
        """
        await self.ensure_authenticated()

        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        m = re.search(
            rf"(controlador\.php\?acao={re.escape(nome_acao)}[^\"'\s]*infra_hash=[a-f0-9]+)",
            html_arvore,
        )
        if not m:
            msg = f"Ação '{nome_acao}' não encontrada no menu do processo."
            raise SEINotFoundError(msg)

        acao_url = urljoin(sei_base, m.group(1).replace("&amp;", "&"))
        r = await self._http.get(acao_url, headers={"Referer": url_arvore})
        _check(r)

        body = _decode_response(r.content, r.headers.get("content-type", ""))
        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is None:
            return {"campos": {}, "selects": {}, "textareas": []}

        campos: dict[str, str] = {}
        for inp in form.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                campos[n] = _tag_str(inp, "value")

        selects: dict[str, list[dict]] = {}
        for sel in form.find_all("select"):
            n = _tag_str(sel, "name")
            if not n:
                continue
            opcoes = []
            for opt in sel.find_all("option"):
                v = _tag_str(opt, "value")
                t = opt.get_text(strip=True)
                if v:
                    opcoes.append({"value": v, "texto": t})
            selects[n] = opcoes

        textareas = []
        for ta in form.find_all("textarea"):
            n = _tag_str(ta, "name")
            if n:
                textareas.append(n)

        return {"campos": campos, "selects": selects, "textareas": textareas}

    async def remover_sobrestamento_web(self, protocolo: str) -> dict:
        """Remove o sobrestamento de um processo via a lista de sobrestados.

        A ação `procedimento_remover_sobrestamento` não tem link estático na
        árvore do processo (é acionada por JS a partir do menu). O caminho
        confiável é a tela `procedimento_sobrestado_listar`: ela traz o form
        `frmProcedimentoSobrestar` e, por linha, o id do processo. Setamos
        `hdnInfraItemId` com esse id e submetemos para a URL assinada de remoção.
        """
        await self.ensure_authenticated()
        listar_url = await self._obter_link_toolbar("procedimento_sobrestado_listar")
        r = await self._http.get(listar_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        alvo = protocolo.strip()
        m_row = re.search(
            rf"acaoRemoverSobrestamento\('(\d+)','{re.escape(alvo)}'\)",
            body,
        )
        if not m_row:
            msg = f"Processo {protocolo} não está na lista de sobrestados da unidade atual."
            raise SEINotFoundError(msg)
        id_proc = m_row.group(1)
        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form", id="frmProcedimentoSobrestar") or soup.find("form")
        if form is None:
            msg = "Form frmProcedimentoSobrestar não encontrado."
            raise SEIParseError(msg)
        m_url = re.search(
            r"controlador\.php\?acao=procedimento_remover_sobrestamento[^\"'\s)]*infra_hash=[a-f0-9]+",
            body,
        )
        if not m_url:
            msg = "URL assinada de procedimento_remover_sobrestamento não encontrada."
            raise SEIParseError(msg)
        post_url = urljoin(f"{self.sei_root}/sei/", m_url.group(0).replace("&amp;", "&"))
        dados = _coletar_estado_form(form)
        dados["hdnInfraItemId"] = id_proc
        r2 = await self._http.post(
            post_url,
            content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": str(r.url), "Content-Type": "application/x-www-form-urlencoded"},
        )
        _check(r2)
        erro = _extrair_erro_sei(_decode_response(r2.content, r2.headers.get("content-type", "")))
        if erro:
            raise SEIConnectionError(erro)
        self._invalidar_arvore(protocolo)
        return {"ok": True, "mensagem": "Sobrestamento removido.", "protocolo": protocolo}

    async def _pagina_visualizacao_processo(self, protocolo: str) -> tuple[str, str]:
        """Retorna (html, url) da página de visualização do nó raiz do processo.

        É a página carregada no frame de conteúdo (`ifrVisualizacao`) ao abrir o
        processo. Diferente da árvore (lado esquerdo), o HEAD dela declara as
        variáveis JS `link<Acao>` com as URLs ASSINADAS das ações acionadas por
        JS no frontend (reabrir, remover sobrestamento, excluir documento,
        assinar, dar ciência, etc.) — que não têm link estático na árvore.
        """
        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        m = re.search(
            r'Nos\[0\][^;]*?"(controlador\.php\?acao=arvore_visualizar[^"]+)"',
            html_arvore,
        )
        if not m:
            msg = "Link do nó raiz do processo não encontrado na árvore."
            raise SEIParseError(msg)
        url = urljoin(url_arvore, m.group(1).replace("&amp;", "&"))
        r = await self._http.get(url, headers={"Referer": url_arvore})
        _check(r)
        return _decode_response(r.content, r.headers.get("content-type", "")), str(r.url)

    async def _link_acao_visualizacao(self, protocolo: str, nome_var: str) -> str | None:
        """Extrai a URL assinada de uma ação JS (`var link<Acao> = '...'`).

        Retorna None se a variável não existir (ação indisponível no estado atual
        do processo — ex.: `linkReabrirProcesso` só aparece em concluídos).
        """
        body, base = await self._pagina_visualizacao_processo(protocolo)
        m = re.search(rf"var\s+{re.escape(nome_var)}\s*=\s*'([^']+)'", body)
        if not m:
            return None
        return urljoin(base, m.group(1).replace("&amp;", "&"))

    async def reabrir_processo_web(self, protocolo: str) -> dict:
        """Reabre um processo concluído na unidade atual.

        A reabertura no frontend é o JS `reabrirProcesso()`, que apenas navega
        para a URL assinada da variável `linkReabrirProcesso` (declarada no HEAD
        da página de visualização do processo). Replicamos: obtemos essa URL e
        fazemos o GET.
        """
        await self.ensure_authenticated()
        url = await self._link_acao_visualizacao(protocolo, "linkReabrirProcesso")
        if url is None:
            msg = (
                f"Reabertura indisponível para {protocolo}: o processo não está "
                "concluído na unidade atual (ou sem permissão para reabrir)."
            )
            raise SEINotFoundError(msg)
        r = await self._http.get(url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        erro = _extrair_erro_sei(_decode_response(r.content, r.headers.get("content-type", "")))
        if erro:
            raise SEIConnectionError(erro)
        self._invalidar_arvore(protocolo)
        return {"ok": True, "mensagem": "Processo reaberto.", "protocolo": protocolo}

    async def _pagina_marcador(self, protocolo: str) -> tuple[str, BeautifulSoup, str]:
        """Retorna (body, soup, referer) da tela gerenciar marcadores do processo.

        A tela `andamento_marcador_gerenciar` traz o form `frmGerenciarMarcador`,
        a tabela dos marcadores aplicados (cada `acaoRemover('<id>','<desc>')`) e
        a URL assinada de `andamento_marcador_remover`.
        """
        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        m = re.search(
            r"(controlador\.php\?acao=andamento_marcador_gerenciar[^\"'\s]*infra_hash=[a-f0-9]+)",
            html_arvore,
        )
        if not m:
            msg = "Ação de marcador não disponível para este processo."
            raise SEINotFoundError(msg)
        url = urljoin(f"{self.sei_root}/sei/", m.group(1).replace("&amp;", "&"))
        r = await self._http.get(url, headers={"Referer": url_arvore})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        return body, BeautifulSoup(body, "html.parser"), url_arvore

    @staticmethod
    def _split_marcador_desc(desc: str) -> tuple[str, str]:
        """Separa "Nome #rrggbb" em (nome, cor-hex)."""
        m_cor = re.search(r"#([0-9a-fA-F]{6})", desc)
        cor = m_cor.group(1) if m_cor else ""
        nome = desc.split("#", 1)[0].strip() if "#" in desc else desc.strip()
        return nome, cor

    async def consultar_marcador_processo_web(self, protocolo: str) -> dict:
        """Lista os marcadores atualmente aplicados a um processo (scraper web)."""
        await self.ensure_authenticated()
        body, _, _ = await self._pagina_marcador(protocolo)
        aplicados: list[dict[str, str]] = []
        for mid, desc in re.findall(r"acaoRemover\('(\d+)','([^']*)'\)", body):
            nome, cor = self._split_marcador_desc(desc)
            aplicados.append({"id": mid, "nome": nome, "cor": cor})
        return {"marcadores": aplicados, "total_itens": len(aplicados)}

    async def desmarcar_processo_web(self, protocolo: str, marcador: str = "") -> dict:
        """Remove marcador(es) de um processo via `andamento_marcador_remover`.

        `marcador` pode ser o id numérico ou parte do nome (case-insensitive).
        Vazio remove TODOS os marcadores aplicados. Como o hash do form muda a
        cada remoção, a tela é relida a cada iteração.
        """
        await self.ensure_authenticated()
        alvo = marcador.strip().lower()
        tentados: dict[str, str] = {}  # mid -> nome (cada mid é tentado uma vez)
        max_iter_marcador = 20
        for _iter in range(max_iter_marcador):  # trava de segurança
            body, soup, referer = await self._pagina_marcador(protocolo)
            aplicados = re.findall(r"acaoRemover\('(\d+)','([^']*)'\)", body)
            # Casa por id exato OU substring do NOME (sem a cor #rrggbb, que
            # senão poderia casar um filtro numérico/parcial no marcador errado).
            # Ignora ids já tentados para não re-postar num no-op em loop.
            prox = next(
                (
                    (mid, desc)
                    for mid, desc in aplicados
                    if mid not in tentados
                    and (
                        not alvo
                        or mid == marcador.strip()
                        or alvo in self._split_marcador_desc(desc)[0].lower()
                    )
                ),
                None,
            )
            if prox is None:
                break
            mid, desc = prox
            form = soup.find("form", id="frmGerenciarMarcador") or soup.find("form")
            m_url = re.search(
                r"controlador\.php\?acao=andamento_marcador_remover[^\"'\s)]*infra_hash=[a-f0-9]+",
                body,
            )
            if form is None or m_url is None:
                msg = "Mecanismo de remoção de marcador não encontrado."
                raise SEIParseError(msg)
            post_url = urljoin(f"{self.sei_root}/sei/", m_url.group(0).replace("&amp;", "&"))
            dados = _coletar_estado_form(form)
            dados["hdnInfraItemId"] = mid
            rr = await self._http.post(
                post_url,
                content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode("ascii"),
                headers={"Referer": referer, "Content-Type": "application/x-www-form-urlencoded"},
            )
            _check(rr)
            erro = _extrair_erro_sei(
                _decode_response(rr.content, rr.headers.get("content-type", ""))
            )
            if erro:
                raise SEIConnectionError(erro)
            tentados[mid] = self._split_marcador_desc(desc)[0]
            self._invalidar_arvore(protocolo)
        else:
            logger.warning(
                "Loop de remoção de marcador atingiu o limite de %d iterações para %s",
                max_iter_marcador,
                protocolo,
            )
            msg = (
                f"Remoção de marcador interrompida após {max_iter_marcador} iterações "
                f"para {protocolo}."
            )
            raise SEIConnectionError(msg)
        if not tentados:
            qual = f'"{marcador}" ' if marcador else ""
            msg = f"Marcador {qual}não está aplicado em {protocolo}."
            raise SEINotFoundError(msg)
        # Verifica de fato: relê e confere quais ids sumiram (POST pode ser no-op
        # silencioso). Conta como removido só o que realmente saiu.
        body_final, _, _ = await self._pagina_marcador(protocolo)
        ainda = set(re.findall(r"acaoRemover\('(\d+)'", body_final))
        removidos = [nome for mid, nome in tentados.items() if mid not in ainda]
        falhas = [nome for mid, nome in tentados.items() if mid in ainda]
        if falhas:
            msg = f"Marcador(es) ainda aplicado(s) após a remoção: {', '.join(falhas)}."
            raise SEIConnectionError(msg)
        return {"ok": True, "removidos": removidos, "protocolo": protocolo}

    async def remover_anotacao_web(self, protocolo: str) -> dict:
        """Remove a anotação (post-it) de um processo registrando texto vazio."""
        await self.ensure_authenticated()
        await self.executar_acao_processo(protocolo, "anotacao_registrar", {"txaDescricao": ""})
        return {"ok": True, "mensagem": "Anotação removida.", "protocolo": protocolo}

    # ------------------------------------------------------------------
    # Read scrapers — PR #4
    # ------------------------------------------------------------------

    async def _get_doc_signed_url(
        self, protocolo: str, id_documento: str, acao: str
    ) -> tuple[str, str]:
        """Retorna (signed_url, arvore_url) para uma ação de documento.

        Aceita tanto o id interno (id do nó da árvore) quanto o número SEI
        (extraído do label do nó, ex: "Despacho GPF 2874369") — web-only não
        tem Solr para resolver. Para `documento_consultar` usa Nos[].link;
        para outras ações busca a URL assinada por regex com o id resolvido.
        """
        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        # Resolve a referência para o nó da árvore: por id interno, depois
        # por número SEI no label
        nos = parse_arvore_nos(html_arvore)
        no_alvo: dict | None = None
        for no in nos[1:]:
            if no.get("id") == id_documento:
                no_alvo = no
                break
        if no_alvo is None:
            for no in nos[1:]:
                if _parse_doc_label(no.get("label", "")).get("numero_sei") == id_documento:
                    no_alvo = no
                    break
        id_interno = str(no_alvo["id"]) if no_alvo else id_documento

        # Para documento_consultar, o link está em Nos[].link
        if acao == "documento_consultar" and no_alvo and no_alvo.get("link"):
            raw = str(no_alvo["link"]).replace("&amp;", "&")
            return urljoin(sei_base, raw), url_arvore

        # Para documento_visualizar, prefere usar a propriedade src do nó
        if acao == "documento_visualizar" and no_alvo and no_alvo.get("src"):
            raw = str(no_alvo["src"]).replace("&amp;", "&")
            return urljoin(sei_base, raw), url_arvore

        # Busca genérica: qualquer URL com acao=X e id_documento=Y
        # (?=&|&amp;|["'\s]) âncora o fim do id para evitar match por prefixo
        # (ex: id=287 não deve casar com id=2874369)
        _id_anchor = r"(?=&(?:amp;)?|[\"'\s])"
        pattern = (
            rf"(controlador\.php\?acao={re.escape(acao)}"
            rf"[^\"'\s]*id_documento={re.escape(id_interno)}{_id_anchor}"
            rf"[^\"'\s]*infra_hash=[a-fA-F0-9]+)"
        )
        pattern2 = (
            rf"(controlador\.php\?acao={re.escape(acao)}"
            rf"[^\"'\s]*infra_hash=[a-fA-F0-9]+"
            rf"[^\"'\s]*id_documento={re.escape(id_interno)}{_id_anchor}"
            rf"[^\"'\s]*)"
        )

        # Tenta buscar primeiro nas ações específicas do próprio nó
        if no_alvo and no_alvo.get("acoes_html"):
            m = re.search(pattern, no_alvo["acoes_html"])
            if not m:
                m = re.search(pattern2, no_alvo["acoes_html"])
            if m:
                return urljoin(sei_base, m.group(1).replace("&amp;", "&")), url_arvore

        # Fallback para busca genérica no HTML inteiro da árvore
        m = re.search(pattern, html_arvore)
        if not m:
            m = re.search(pattern2, html_arvore)
        if not m:
            msg = (
                f"Ação '{acao}' não encontrada para o documento {id_documento} "
                f"na árvore do processo {protocolo}."
            )
            raise SEIParseError(msg)
        return urljoin(sei_base, m.group(1).replace("&amp;", "&")), url_arvore

    async def consultar_documento_web(self, protocolo: str, id_documento: str) -> dict:
        """Scrape dos metadados de documento_consultar (tipo, data, assinaturas, etc.)."""
        await self.ensure_authenticated()
        url, referer = await self._get_doc_signed_url(
            protocolo, id_documento, "documento_consultar"
        )
        r = await self._http.get(url, headers={"Referer": referer})
        _check(r)
        html = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(html)
        if erro:
            # SEI retorna 200 com página de erro (sessão expirada, sem permissão)
            msg = f"documento_consultar: {erro}"
            raise SEIConnectionError(msg)
        return _parse_documento_consultar(html, id_documento)

    async def listar_assinaturas_web(self, protocolo: str, id_documento: str) -> list[dict]:
        """Lista assinaturas de um documento via scrape de documento_consultar."""
        data = await self.consultar_documento_web(protocolo, id_documento)
        return data.get("assinaturas") or []

    async def listar_ciencias_web(self, protocolo: str, id_documento: str) -> list[dict]:
        """Lista ciências de um documento via scrape de documento_consultar."""
        data = await self.consultar_documento_web(protocolo, id_documento)
        return data.get("ciencias") or []

    async def visualizar_documento_interno_web(self, protocolo: str, id_documento: str) -> str:
        """Retorna HTML de um documento interno via documento_visualizar."""
        await self.ensure_authenticated()
        url, referer = await self._get_doc_signed_url(
            protocolo, id_documento, "documento_visualizar"
        )
        r = await self._http.get(url, headers={"Referer": referer})
        _check(r)
        html = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(html)
        if erro:
            # SEI retorna 200 com página de erro; sem este check o erro seria
            # devolvido como se fosse o conteúdo do documento (e quebraria a
            # auto-detecção interno→externo de sei_ler_documento)
            msg = f"documento_visualizar: {erro}"
            raise SEIConnectionError(msg)
        return html

    async def baixar_documento_externo_web(self, protocolo: str, id_documento: str) -> bytes:
        """Baixa bytes de um documento externo via documento_download_anexo."""
        await self.ensure_authenticated()
        url, referer = await self._get_doc_signed_url(
            protocolo, id_documento, "documento_download_anexo"
        )
        r = await self._http.get(url, headers={"Referer": referer})
        _check(r)
        if "text/html" in r.headers.get("content-type", "").lower():
            # Anexo não chega como text/html: é página de erro com status 200
            erro = _extrair_erro_sei(_decode_response(r.content, r.headers.get("content-type", "")))
            msg = f"documento_download_anexo: {erro or 'resposta HTML inesperada'}"
            raise SEIConnectionError(msg)
        return r.content

    async def listar_secoes_web(self, protocolo: str, id_documento: str) -> dict:
        """Lista seções editáveis de um documento interno via editor_montar."""
        await self.ensure_authenticated()
        editor_url, referer = await self._get_doc_signed_url(
            protocolo, id_documento, "editor_montar"
        )
        r = await self._http.get(editor_url, headers={"Referer": referer})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            msg = f"editor_montar: {erro}"
            raise SEIConnectionError(msg)
        soup = BeautifulSoup(body, "html.parser")
        textareas = soup.select("div#divEditores textarea")
        versao_inp = soup.find("input", {"name": lambda n: bool(n and "versao" in n.lower())})
        versao = _tag_str(versao_inp, "value")
        secoes = [
            {
                "id": _tag_str(ta, "name"),
                "idSecaoModelo": _tag_str(ta, "name"),
                "conteudo": ta.decode_contents(),
                "somenteLeitura": False,
            }
            for ta in textareas
            if ta.get("name")
        ]
        return {"secoes": secoes, "ultimaVersaoDocumento": versao}

    async def alterar_secoes_web(
        self, protocolo: str, id_documento: str, secoes: list[dict]
    ) -> dict:
        """Edita seções de um documento interno via editor_montar POST."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        editor_url, referer = await self._get_doc_signed_url(
            protocolo, id_documento, "editor_montar"
        )
        r = await self._http.get(editor_url, headers={"Referer": referer})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            msg = f"editor_montar (GET): {erro}"
            raise SEIConnectionError(msg)

        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form", id="frmEditor") or soup.find("form")
        if form is None:
            msg = "Formulário do editor não encontrado em editor_montar."
            raise SEIParseError(msg)

        action = _tag_str(form, "action").replace("&amp;", "&")
        save_url = urljoin(sei_base, action) if action else editor_url

        post_data: list[tuple[str, str]] = [
            (_tag_str(inp, "name"), _tag_str(inp, "value"))
            for inp in form.find_all("input", type="hidden")
            if _tag_str(inp, "name") and "unidade" not in _tag_str(inp, "name").lower()
        ]

        # Submit button obrigatório — PHP ignora POST sem ele silenciosamente
        sbm = _extrair_submit_btn(form)
        if sbm:
            post_data.append(sbm)

        # Substituir conteúdos das textareas; seções não alteradas são reenviadas intactas
        alteracoes = {s["idSecaoModelo"]: s["conteudo"] for s in secoes}
        for ta in soup.select("div#divEditores textarea"):
            nome = _tag_str(ta, "name")
            if nome:
                post_data.append((nome, alteracoes.get(nome, ta.decode_contents())))

        r2 = await self._http.post(
            save_url,
            content=urlencode(post_data, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": editor_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        _check(r2)
        resp_body = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro2 = _extrair_erro_sei(resp_body)
        if erro2:
            msg = f"editor_montar (POST): {erro2}"
            raise SEIConnectionError(msg)
        return {"status": "ok", "id_documento": id_documento}

    async def alterar_documento_interno_web(
        self,
        protocolo: str,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
    ) -> dict:
        """Altera metadados de um documento interno via documento_alterar."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        doc_url, referer = await self._get_doc_signed_url(
            protocolo, id_documento, "documento_alterar"
        )
        r = await self._http.get(doc_url, headers={"Referer": referer})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            msg = f"documento_alterar (GET): {erro}"
            raise SEIConnectionError(msg)

        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Formulário documento_alterar não encontrado."
            raise SEIParseError(msg)

        action = _tag_str(form, "action").replace("&amp;", "&")
        save_url = urljoin(sei_base, action) if action else doc_url

        post_data = [
            (_tag_str(inp, "name"), _tag_str(inp, "value"))
            for inp in form.find_all("input", type="hidden")
            if _tag_str(inp, "name")
        ]
        sbm = _extrair_submit_btn(form)
        if sbm:
            post_data.append(sbm)

        # Substituir apenas os campos informados; os demais permanecem do form original
        campos: dict[str, str] = {}
        if descricao:
            campos["txtDescricao"] = descricao
            campos["txaDescricao"] = descricao
        if nivel_acesso:
            campos["selNivelAcesso"] = nivel_acesso
        if hipotese_legal and nivel_acesso in ("1", "2"):
            campos["selHipoteseLegal"] = hipotese_legal

        # Substituir ou adicionar campos conforme necessário
        nomes_existentes = {name for name, _ in post_data}
        updated: list[tuple[str, str]] = []
        for name, value in post_data:
            updated.append((name, campos.pop(name, value)))
        for name, value in campos.items():
            if name not in nomes_existentes:
                updated.append((name, value))

        # Campos select não aparecem em input[hidden] — garantir que os valores corretos sejam enviados
        for sel in form.find_all("select"):
            sel_name = _tag_str(sel, "name")
            if not sel_name:
                continue
            if sel_name == "selNivelAcesso" and nivel_acesso:
                updated = [(n, v) for n, v in updated if n != sel_name]
                updated.append((sel_name, nivel_acesso))
            elif sel_name == "selHipoteseLegal" and hipotese_legal and nivel_acesso in ("1", "2"):
                updated = [(n, v) for n, v in updated if n != sel_name]
                updated.append((sel_name, hipotese_legal))
            elif sel_name not in {n for n, _ in updated}:
                # Enviar o valor selecionado atual para campos não alterados
                opt = sel.find("option", selected=True)
                if opt:
                    updated.append((sel_name, _tag_str(opt, "value")))

        r2 = await self._http.post(
            save_url,
            content=urlencode(updated, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": doc_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        _check(r2)
        resp_body = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro2 = _extrair_erro_sei(resp_body)
        if erro2:
            msg = f"documento_alterar (POST): {erro2}"
            raise SEIConnectionError(msg)
        return {"status": "ok", "id_documento": id_documento}

    async def consultar_processo_detalhe(self, protocolo: str) -> dict:
        """Scrape de procedimento_consultar: unidades, interessados, sobrestamento.

        Navega trabalhar → arvore → link procedimento_consultar → parse tabelas.
        """
        await self.ensure_authenticated()

        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        m = re.search(
            r"(controlador\.php\?acao=procedimento_consultar[^\"'\s]*infra_hash=[a-f0-9]+)",
            html_arvore,
        )
        if not m:
            msg = f"Link procedimento_consultar não encontrado na árvore de {protocolo}."
            raise SEIParseError(msg)
        consultar_url = urljoin(sei_base, m.group(1).replace("&amp;", "&"))

        r = await self._http.get(consultar_url, headers={"Referer": url_arvore})
        _check(r)

        html = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(html)
        if erro:
            msg = f"procedimento_consultar: {erro}"
            raise SEIConnectionError(msg)
        return _parse_procedimento_consultar(html, protocolo)

    async def _gerar_arquivo_processo(self, protocolo_formatado: str, acao: str) -> bytes:
        """Generate a PDF or ZIP archive for a process (shared by gerar_pdf/zip_processo).

        Five-step flow (identical for PDF and ZIP):
        1. procedimento_trabalhar → frameset com ifrArvore
        2. arvore_montar → busca link da ação (procedimento_gerar_pdf/zip)
        3. GET form de opções
        4. POST com hdnFlagGerar=1 → HTML com ifrDownload.src
        5. GET exibir_arquivo → bytes do arquivo
        """

        def _find_link(proto: str) -> str | None:
            proto_norm = proto.replace(" ", "")
            for k, v in self._trabalhar_links.items():
                if k == proto or k.replace(" ", "") == proto_norm:
                    return v
            return None

        async with self._form_lock:
            _trab_href = _find_link(protocolo_formatado)
        if _trab_href is None:
            await self.fetch_inbox(detalhada=False)
            async with self._form_lock:
                _trab_href = _find_link(protocolo_formatado)
        if _trab_href is None:
            await self.pesquisar_processo(protocolo_formatado)
            async with self._form_lock:
                _trab_href = _find_link(protocolo_formatado)

        trab_url = urljoin(str(self._inbox_url), _trab_href)

        r1 = await self._http.get(trab_url, headers={"Referer": str(self._inbox_url)})
        _check(r1)

        if 'name="txtUsuario"' in r1.text or 'id="txtUsuario"' in r1.text:
            async with self._form_lock:
                self._form_action = None
                self._form_hidden = {}
            await self.login()
            return await self._gerar_arquivo_processo(protocolo_formatado, acao)

        soup_fs = BeautifulSoup(r1.text, "html.parser")
        ifr = soup_fs.find("iframe", id="ifrArvore")
        if not ifr:
            msg = "ifrArvore não encontrado no frameset"
            raise SEIParseError(msg)
        arvore_url = urljoin(str(r1.url), _tag_str(ifr, "src").replace("&amp;", "&"))

        r2 = await self._http.get(arvore_url, headers={"Referer": trab_url})
        _check(r2)

        m_link = re.search(
            rf"(controlador\.php\?acao={re.escape(acao)}[^\"'\s]*infra_hash=[a-f0-9]+)",
            r2.text,
        )
        if not m_link:
            msg = f"Link {acao} não encontrado na árvore"
            raise SEIParseError(msg)

        sei_base = f"{self.sei_root}/sei/"
        form_url = urljoin(sei_base, m_link.group(1).replace("&amp;", "&"))

        r3 = await self._http.get(form_url, headers={"Referer": str(r2.url)})
        _check(r3)

        soup3 = BeautifulSoup(
            _decode_response(r3.content, r3.headers.get("content-type", "")), "html.parser"
        )
        form = soup3.find("form", id=re.compile(r"(?i)frmProcedimento(Pdf|Zip)"))
        if not form:
            msg = "Formulário frmProcedimento(Pdf|Zip) não encontrado"
            raise SEIParseError(msg)
        form_action = _tag_str(form, "action").replace("&amp;", "&")
        post_url = urljoin(str(r3.url), form_action)

        post_data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = _tag_str(inp, "name")
            if name:
                post_data[name] = _tag_str(inp, "value")
        post_data["rdoTipo"] = "T"
        post_data["hdnFlagGerar"] = "1"

        r4 = await self._http.post(
            post_url,
            data=post_data,
            headers={"Referer": str(r3.url)},
            timeout=httpx.Timeout(180.0, connect=10.0),
        )
        _check(r4)

        body4 = _decode_response(r4.content, r4.headers.get("content-type", ""))
        m_dl = re.search(
            r"getElementById\(['\"]ifrDownload['\"]\)\.src\s*=\s*'([^']+)'",
            body4,
        )
        if not m_dl:
            msg = (
                f"URL de download (ifrDownload.src) não encontrada após {acao}. "
                "O processo pode não ter documentos disponíveis."
            )
            raise SEIParseError(msg)

        download_url = urljoin(sei_base, m_dl.group(1).replace("&amp;", "&"))

        r5 = await self._http.get(download_url, headers={"Referer": str(r4.url)})
        _check(r5)

        return r5.content

    async def gerar_pdf_processo(self, protocolo_formatado: str) -> bytes:
        """Gera e baixa o PDF consolidado de um processo SEI.

        Usa o mesmo endpoint do botão "Gerar PDF" da interface web.
        Retorna os bytes brutos do PDF.
        """
        await self.ensure_authenticated()
        content = await self._gerar_arquivo_processo(protocolo_formatado, "procedimento_gerar_pdf")
        if not content.startswith(b"%PDF") and b"pdf" not in content[:32].lower():
            ct = "(desconhecido)"
            msg = f"Esperado PDF mas recebeu Content-Type: {ct}"
            raise SEIParseError(msg)
        return content

    async def gerar_zip_processo(self, protocolo_formatado: str) -> bytes:
        """Gera e baixa o ZIP com todos os documentos de um processo SEI.

        Usa o mesmo endpoint do botão "Gerar ZIP" da interface web.
        Retorna os bytes brutos do arquivo ZIP.
        """
        await self.ensure_authenticated()
        return await self._gerar_arquivo_processo(protocolo_formatado, "procedimento_gerar_zip")

    async def listar_atividades(self, protocolo_formatado: str, tipo_historico: str = "R") -> dict:
        """Lista andamentos/atividades de um processo via web scraper.

        Scrape de `procedimento_consultar_historico.php` (~370 ms, vs ~2.5 s REST).
        Precisa da URL assinada do histórico que está na árvore do processo.

        Retorna:
            {
              "processo": {"protocolo": str, "id_procedimento": str},
              "total_andamentos": int,
              "andamentos": [{data_hora, unidade, usuario, descricao}, ...],
            }
        """
        await self.ensure_authenticated()
        hist_url, id_proc, referer = await self._navegar_historico(protocolo_formatado)

        # 1. GET inicial
        r3 = await self._http.get(hist_url, headers={"Referer": referer})
        _check(r3)

        soup = BeautifulSoup(r3.text, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Form do histórico não encontrado."
            raise SEIParseError(msg)

        andamentos = []

        if tipo_historico == "R":
            # Adiciona andamentos da página 0 (GET)
            andamentos.extend(self._parse_tabela_historico(r3.text))

            # Verifica paginação
            pag_select = soup.find("select", attrs={"name": "selInfraPaginacaoSuperior"})
            if pag_select:
                options = pag_select.find_all("option")
                max_page = len(options)

                # Iterar pelas páginas de 1 até max_page - 1
                for p_idx in range(1, max_page):
                    dados = _coletar_estado_form(form)
                    dados["hdnInfraPaginaAtual"] = str(p_idx)
                    dados["selInfraPaginacaoSuperior"] = str(p_idx)
                    dados["selInfraPaginacaoInferior"] = str(p_idx)
                    dados["hdnTipoHistorico"] = tipo_historico

                    action = urljoin(hist_url, _tag_str(form, "action").replace("&amp;", "&"))
                    r_pag = await self._http.post(
                        action,
                        content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode(
                            "ascii"
                        ),
                        headers={
                            "Referer": hist_url,
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                    )
                    _check(r_pag)
                    andamentos.extend(self._parse_tabela_historico(r_pag.text))
        else:
            # Caso não seja "R", precisamos fazer o POST para a página 0 com o tipo solicitado
            dados = _coletar_estado_form(form)
            dados["hdnTipoHistorico"] = tipo_historico
            dados["hdnInfraPaginaAtual"] = "0"
            if soup.find("select", attrs={"name": "selInfraPaginacaoSuperior"}):
                dados["selInfraPaginacaoSuperior"] = "0"
                dados["selInfraPaginacaoInferior"] = "0"

            action = urljoin(hist_url, _tag_str(form, "action").replace("&amp;", "&"))
            r_pag0 = await self._http.post(
                action,
                content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode("ascii"),
                headers={"Referer": hist_url, "Content-Type": "application/x-www-form-urlencoded"},
            )
            _check(r_pag0)

            andamentos.extend(self._parse_tabela_historico(r_pag0.text))

            # Analisa paginação na resposta da página 0
            soup_pag0 = BeautifulSoup(r_pag0.text, "html.parser")
            form_pag0 = soup_pag0.find("form") or form
            pag_select = soup_pag0.find("select", attrs={"name": "selInfraPaginacaoSuperior"})
            if pag_select:
                options = pag_select.find_all("option")
                max_page = len(options)
                for p_idx in range(1, max_page):
                    dados = _coletar_estado_form(form_pag0)
                    dados["hdnInfraPaginaAtual"] = str(p_idx)
                    dados["selInfraPaginacaoSuperior"] = str(p_idx)
                    dados["selInfraPaginacaoInferior"] = str(p_idx)
                    dados["hdnTipoHistorico"] = tipo_historico

                    action = urljoin(hist_url, _tag_str(form_pag0, "action").replace("&amp;", "&"))
                    r_pag = await self._http.post(
                        action,
                        content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode(
                            "ascii"
                        ),
                        headers={
                            "Referer": hist_url,
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                    )
                    _check(r_pag)
                    andamentos.extend(self._parse_tabela_historico(r_pag.text))

        return {
            "processo": {
                "protocolo": protocolo_formatado,
                "id_procedimento": id_proc,
            },
            "total_andamentos": len(andamentos),
            "andamentos": andamentos,
        }

    async def _navegar_historico(self, protocolo_formatado: str) -> tuple[str, str, str]:
        """Navega até o histórico do processo; retorna (hist_url, id_proc, referer)."""
        html_arvore, url_arvore = await self._arvore_do_processo(protocolo_formatado)

        m_id = re.search(r"id_procedimento=(\d+)", url_arvore)
        id_proc = m_id.group(1) if m_id else ""

        m_hist = re.search(
            r"(controlador\.php\?acao=procedimento_consultar_historico[^\"']*infra_hash=[a-f0-9]+)",
            html_arvore,
        )
        if not m_hist:
            msg = "Link procedimento_consultar_historico não encontrado na árvore"
            raise SEINotFoundError(msg)
        hist_url = urljoin(url_arvore, m_hist.group(1).replace("&amp;", "&"))
        return hist_url, id_proc, url_arvore

    @staticmethod
    def _parse_tabela_historico(html: str) -> list[dict[str, str]]:
        """Extrai as linhas (data_hora, unidade, usuario, descricao) de tblHistorico."""
        tbl = BeautifulSoup(html, "html.parser").find("table", id="tblHistorico")
        linhas: list[dict[str, str]] = []
        if tbl:
            for tr in tbl.find_all("tr")[1:]:  # pula header
                tds = tr.find_all("td")
                if len(tds) >= _HISTORY_TABLE_COLS:
                    linhas.append(
                        {
                            "data_hora": tds[0].get_text(" ", strip=True),
                            "unidade": tds[1].get_text(" ", strip=True),
                            "usuario": tds[2].get_text(" ", strip=True),
                            "descricao": tds[3].get_text(" ", strip=True),
                        }
                    )
        return linhas

    async def listar_historico_atribuicoes_web(self, protocolo_formatado: str) -> dict:
        """Histórico de atribuições do processo (do histórico COMPLETO, tipo 'P').

        As atribuições não aparecem no histórico resumido; o completo registra
        "Processo atribuído para <login>" e "Removida atribuição do processo".
        Retorna eventos em ordem cronológica + derivações úteis:
        - `atribuidos`: logins distintos a quem o processo já foi atribuído
        - `atual`: login atualmente atribuído (vazio se a última ação foi remoção)
        - `anterior`: login atribuído imediatamente antes do atual
        """
        await self.ensure_authenticated()
        hist_url, id_proc, referer = await self._navegar_historico(protocolo_formatado)
        r = await self._http.get(hist_url, headers={"Referer": referer})
        _check(r)
        form = BeautifulSoup(r.text, "html.parser").find("form")
        if form is None:
            msg = "Form do histórico não encontrado."
            raise SEIParseError(msg)
        dados = _coletar_estado_form(form)
        dados["hdnTipoHistorico"] = "P"
        action = urljoin(hist_url, _tag_str(form, "action").replace("&amp;", "&"))
        r2 = await self._http.post(
            action,
            content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": hist_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        _check(r2)

        eventos: list[dict[str, str]] = []
        for linha in self._parse_tabela_historico(r2.text):
            desc = linha["descricao"]
            m_at = re.search(r"[Pp]rocesso atribu[ií]do para\s+(.+?)\.?\s*$", desc)
            if m_at:
                eventos.append(
                    {
                        "data_hora": linha["data_hora"],
                        "tipo": "atribuido",
                        "usuario": m_at.group(1).strip(),
                    }
                )
            elif re.search(r"[Rr]emovida atribui[çc][ãa]o", desc):
                eventos.append({"data_hora": linha["data_hora"], "tipo": "removido", "usuario": ""})

        eventos_cron = list(reversed(eventos))  # tabela vem do mais novo p/ o mais antigo
        atribuicoes = [e["usuario"] for e in eventos_cron if e["tipo"] == "atribuido"]
        distintos: list[str] = []
        for u in atribuicoes:
            if u not in distintos:
                distintos.append(u)
        atual = (
            eventos_cron[-1]["usuario"]
            if eventos_cron and eventos_cron[-1]["tipo"] == "atribuido"
            else ""
        )
        anterior = ""
        anteriores = atribuicoes[:-1] if atual else atribuicoes
        for u in reversed(anteriores):
            if u != atual:
                anterior = u
                break
        return {
            "processo": {"protocolo": protocolo_formatado, "id_procedimento": id_proc},
            "eventos": eventos_cron,
            "atribuidos": distintos,
            "atual": atual,
            "anterior": anterior,
            "total": len(eventos_cron),
        }

    # ------------------------------------------------------------------
    # Complex forms — PR #5
    # ------------------------------------------------------------------

    async def autocomplete_unidades(self, termo: str) -> list[dict]:
        """Resolve sigla/nome de unidade via AJAX autocomplete do SEI.

        Retorna lista de {"id": str, "sigla": str, "nome": str}.
        """
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        r = await self._http.get(
            f"{sei_base}controlador_ajax.php",
            params={"acao_ajax": "unidade_auto_completar", "termo": termo},
            headers={"Referer": str(self._inbox_url)},
        )
        if not r.is_success:
            logger.warning("autocomplete_unidades: HTTP %s para termo=%r", r.status_code, termo)
            return []
        try:
            raw = r.json()
        except ValueError:
            logger.warning(
                "autocomplete_unidades: resposta não-JSON (HTTP %s) para termo=%r",
                r.status_code,
                termo,
            )
            return []
        results = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "id": str(item.get("id", item.get("value", ""))),
                    "sigla": str(item.get("sigla", item.get("label", ""))),
                    "nome": str(item.get("nome", item.get("descricao", ""))),
                }
            )
        return results

    async def enviar_processo_web(
        self,
        protocolo: str,
        unidades_ids: list[str],
        opcoes: OpcoesTramitacaoWeb | None = None,
    ) -> dict:
        """Envia (tramita) um processo via scraper web do SEI.

        Fluxo: trabalhar → arvore → link(procedimento_tramitar) → GET form → POST.
        As `unidades_ids` devem ser IDs numéricos já resolvidos.
        """
        _op = opcoes or OpcoesTramitacaoWeb()
        await self.ensure_authenticated()

        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        m = re.search(
            r"(controlador\.php\?acao=procedimento_tramitar[^\"'\s]*infra_hash=[a-fA-F0-9]+)",
            html_arvore,
        )
        if not m:
            msg = (
                f"Ação 'procedimento_tramitar' não encontrada na árvore de {protocolo}. "
                "Verifique permissão de tramitação neste processo."
            )
            raise SEINotFoundError(msg)

        tramitar_url = urljoin(sei_base, m.group(1).replace("&amp;", "&"))
        r = await self._http.get(tramitar_url, headers={"Referer": url_arvore})
        _check(r)

        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)

        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Form procedimento_tramitar não encontrado."
            raise SEIParseError(msg)

        action = _tag_str(form, "action").replace("&amp;", "&")
        post_url = urljoin(sei_base, action) if action else tramitar_url

        # Coleta campos hidden do form
        post_data: list[tuple[str, str]] = []
        for inp in form.find_all("input", type="hidden"):
            name = _tag_str(inp, "name")
            if name:
                post_data.append((name, _tag_str(inp, "value")))

        # Botão submit obrigatório (PHP ignora POST sem ele silenciosamente)
        sbm = _extrair_submit_btn(form)
        if sbm:
            post_data.append(sbm)

        # Adiciona uma entrada hdnIdUnidadeEnvio por unidade destino
        post_data.extend(("hdnIdUnidadeEnvio", uid) for uid in unidades_ids)

        # Opções de tramitação — usa os nomes padrão do SEI
        if _op.manter_aberto.upper() == "S":
            post_data.append(("chkSinManterAberto", "S"))
        if _op.remover_anotacao.upper() == "S":
            post_data.append(("chkSinRemoverAnotacoes", "S"))
        if _op.enviar_email.upper() == "S":
            post_data.append(("chkSinEnviarEmailNotificacao", "S"))
        if _op.data_retorno:
            post_data.append(("dtaRetorno", _op.data_retorno))
        if _op.dias_retorno:
            post_data.append(("numDiasRetorno", _op.dias_retorno))

        r2 = await self._http.post(
            post_url,
            content=urlencode(post_data, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": tramitar_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        if r2.status_code not in (200, 302):
            msg = f"POST procedimento_tramitar falhou com status={r2.status_code}"
            raise SEIConnectionError(msg)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro2 = _extrair_erro_sei(body2)
        if erro2:
            raise SEIConnectionError(erro2)

        return {
            "ok": True,
            "mensagem": f"Processo {protocolo} enviado para {len(unidades_ids)} unidade(s).",
            "protocolo": protocolo,
            "unidades": unidades_ids,
        }

    async def _obter_link_toolbar(self, acao: str) -> str:
        """Retorna URL assinada (com infra_hash) de uma ação do toolbar da inbox.

        Busca o link da ação `acao` no HTML da inbox. Necessário para ações
        que não partem de um processo específico (ex: procedimento_cadastrar).
        """
        await self.ensure_authenticated()
        inbox_url = str(self._inbox_url)
        r = await self._http.get(
            inbox_url,
            headers={"Referer": inbox_url},
        )
        _check(r)
        html = _decode_response(r.content, r.headers.get("content-type", ""))
        m = re.search(
            rf"(controlador\.php\?acao={re.escape(acao)}[^\"'\s]*infra_hash=[a-fA-F0-9]+)",
            html,
        )
        if not m:
            msg = f"Ação '{acao}' não encontrada no toolbar da inbox."
            raise SEINotFoundError(msg)
        sei_base = f"{self.sei_root}/sei/"
        return urljoin(sei_base, m.group(1).replace("&amp;", "&"))

    async def pesquisar_tipos_processo_web(self, filtro: str = "") -> dict:
        """Extrai tipos de processo do select selTipoProcedimento em procedimento_cadastrar."""
        await self.ensure_authenticated()
        cadastrar_url = await self._obter_link_toolbar("procedimento_cadastrar")
        r = await self._http.get(cadastrar_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        soup = BeautifulSoup(body, "html.parser")
        sel = soup.find("select", {"name": re.compile(r"selTipoProcedimento", re.IGNORECASE)})
        if sel is None:
            sel = soup.find("select", id=re.compile(r"selTipoProcedimento", re.IGNORECASE))
        tipos: list[dict[str, str]] = []
        if sel is not None:
            for opt in sel.find_all("option"):
                v = _tag_str(opt, "value")
                t = opt.get_text(strip=True)
                if not v:
                    continue
                if filtro and filtro.lower() not in t.lower():
                    continue
                tipos.append({"id": v, "nome": t})
        return {"tipos": tipos, "total_itens": len(tipos)}

    async def listar_usuarios_web(
        self,
        filtro: str = "",
        *,
        apenas_unidade: bool = True,
    ) -> dict:
        """Lista usuários da unidade via scrape do form de atribuição.

        Requer ao menos um processo na inbox para acessar o form.
        O parâmetro `apenas_unidade` é ignorado — o form mostra apenas
        usuários da unidade atual (equivalente a apenas_unidade=True).
        """
        _ = apenas_unidade  # web form always returns unit users; kept for SEIClient API parity
        await self.ensure_authenticated()
        async with self._form_lock:
            _has_links = bool(self._trabalhar_links)
        if not _has_links:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            _has_links = bool(self._trabalhar_links)
        if not _has_links:
            return {
                "usuarios": [],
                "total_itens": 0,
                "_aviso": "Inbox vazia; não foi possível carregar usuários.",
            }
        async with self._form_lock:
            protocolo = next(iter(self._trabalhar_links))
        form_info = await self.obter_form_acao(protocolo, "procedimento_atribuicao_cadastrar")
        opcoes = form_info.get("selects", {}).get("selAtribuicao", [])
        usuarios: list[dict[str, str]] = []
        for opt in opcoes:
            texto = opt.get("texto", "")
            v = opt.get("value", "")
            if not v:
                continue
            m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", texto)
            if m:
                nome = m.group(1).strip()
                sigla = m.group(2).strip()
            else:
                nome = texto.strip()
                sigla = ""
            if (
                filtro
                and filtro.lower() not in nome.lower()
                and filtro.lower() not in sigla.lower()
            ):
                continue
            usuarios.append({"id_usuario": v, "nome": nome, "sigla": sigla})
        return {"usuarios": usuarios, "total_itens": len(usuarios)}

    async def pesquisar_blocos_assinatura_web(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista blocos de assinatura via scrape de bloco_assinatura_listar."""
        await self.ensure_authenticated()
        lista_url = await self._obter_link_toolbar("bloco_assinatura_listar")
        r = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        soup = BeautifulSoup(body, "html.parser")
        tbl = soup.find("table", id=re.compile(r"tblBlocos?", re.IGNORECASE))
        if tbl is None:
            tbl = soup.find("table", class_=re.compile(r"infraTable", re.IGNORECASE))
        blocos: list[dict[str, str]] = []
        if tbl is not None:
            for tr in tbl.find_all("tr")[1:]:
                tds = tr.find_all("td")
                if len(tds) < _BLOCK_TABLE_MIN_COLS:
                    continue
                descricao = tds[1].get_text(" ", strip=True)
                if filtro and filtro.lower() not in descricao.lower():
                    continue
                id_bloco = ""
                for a in tr.find_all("a", href=re.compile(r"id_bloco=\d+")):
                    mb = re.search(r"id_bloco=(\d+)", _tag_str(a, "href"))
                    if mb:
                        id_bloco = mb.group(1)
                        break
                estado = (
                    tds[2].get_text(" ", strip=True) if len(tds) > _BLOCK_TABLE_MIN_COLS else ""
                )
                blocos.append({"idBloco": id_bloco, "descricao": descricao, "estado": estado})
                if len(blocos) >= limit:
                    break
        return {
            "blocos": blocos,
            "pagina_atual": 0,
            "itens_pagina": len(blocos),
            "total_itens": len(blocos),
            "tem_proxima": len(blocos) >= limit,
        }

    async def pesquisar_outras_unidades_web(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa unidades via AJAX autocomplete (unidade_auto_completar).

        Requer filtro não-vazio — o endpoint AJAX não retorna resultados sem termo.
        """
        if not filtro:
            msg = (
                "Em modo web (sem mod-wssei), filtro é obrigatório para pesquisar unidades. "
                "Informe pelo menos 1 caractere (sigla ou nome da unidade)."
            )
            raise SEIValidationError(msg)
        resultados = await self.autocomplete_unidades(filtro)
        resultados = resultados[:limit]
        return {"unidades": resultados, "total_itens": len(resultados)}

    async def _obter_acao_bloco_url(self, id_bloco: str, nome_acao: str) -> str:
        """Busca URL assinada de uma ação em um bloco específico via bloco_assinatura_listar."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        lista_url = await self._obter_link_toolbar("bloco_assinatura_listar")
        r = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        pat = re.compile(
            rf"(controlador\.php\?[^\"'\s]*acao={re.escape(nome_acao)}[^\"'\s]*id_bloco={re.escape(id_bloco)}[^\"'\s]*infra_hash=[a-fA-F0-9]+|"
            rf"controlador\.php\?[^\"'\s]*id_bloco={re.escape(id_bloco)}[^\"'\s]*acao={re.escape(nome_acao)}[^\"'\s]*infra_hash=[a-fA-F0-9]+)"
        )
        m = pat.search(body)
        if not m:
            msg = (
                f"Ação '{nome_acao}' não encontrada para bloco {id_bloco}. "
                "Verifique se o bloco existe e está no estado correto."
            )
            raise SEINotFoundError(msg)
        return urljoin(sei_base, m.group(1).replace("&amp;", "&"))

    async def criar_bloco_assinatura_web(self, descricao: str) -> dict:
        """Cria um bloco de assinatura via scraper web."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        try:
            incluir_url = await self._obter_link_toolbar("bloco_assinatura_incluir")
        except SEINotFoundError:
            incluir_url = await self._obter_link_toolbar("bloco_assinatura_cadastrar")
        r = await self._http.get(incluir_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)
        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Form de criação de bloco não encontrado."
            raise SEIParseError(msg)
        action = _tag_str(form, "action").replace("&amp;", "&")
        post_url = urljoin(sei_base, action) if action else incluir_url
        post_data: list[tuple[str, str]] = []
        for inp in form.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                post_data.append((n, _tag_str(inp, "value")))
        sbm = _extrair_submit_btn(form)
        if sbm:
            post_data.append(sbm)
        post_data.append(("txtDescricao", descricao))
        r2 = await self._http.post(
            post_url,
            content=urlencode(post_data).encode("iso-8859-1"),
            headers={
                "Referer": incluir_url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if r2.status_code not in (200, 302):
            msg = f"POST bloco_assinatura_incluir status={r2.status_code}"
            raise SEIConnectionError(msg)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro2 = _extrair_erro_sei(body2)
        if erro2:
            raise SEIConnectionError(erro2)
        id_bloco = ""
        mb = re.search(r"id_bloco=(\d+)", str(r2.url))
        if mb:
            id_bloco = mb.group(1)
        if not id_bloco:
            mb = re.search(r"id_bloco[\"']?\s*[:=]\s*[\"']?(\d+)", body2)
            if mb:
                id_bloco = mb.group(1)
        return {"ok": True, "idBloco": id_bloco, "descricao": descricao}

    async def disponibilizar_bloco_assinatura_web(self, id_bloco: str) -> dict:
        """Disponibiliza um bloco de assinatura via scraper web."""
        acao_url = await self._obter_acao_bloco_url(id_bloco, "bloco_assinatura_disponibilizar")
        r = await self._http.get(acao_url, headers={"Referer": str(self._inbox_url)})
        if r.status_code not in (200, 302):
            msg = f"bloco_assinatura_disponibilizar status={r.status_code}"
            raise SEIConnectionError(msg)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)
        return {"ok": True, "idBloco": id_bloco, "mensagem": "Bloco disponibilizado com sucesso."}

    async def cancelar_disponibilizacao_bloco_assinatura_web(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura via scraper web."""
        try:
            acao_url = await self._obter_acao_bloco_url(
                id_bloco, "bloco_assinatura_cancelar_disponibilizacao"
            )
        except SEINotFoundError:
            acao_url = await self._obter_acao_bloco_url(id_bloco, "bloco_assinatura_cancelar")
        r = await self._http.get(acao_url, headers={"Referer": str(self._inbox_url)})
        if r.status_code not in (200, 302):
            msg = f"bloco_assinatura_cancelar status={r.status_code}"
            raise SEIConnectionError(msg)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)
        return {
            "ok": True,
            "idBloco": id_bloco,
            "mensagem": "Disponibilização cancelada com sucesso.",
        }

    async def _executar_acao_bloco(self, id_bloco: str, nome_acao: str, mensagem: str) -> dict:
        """Executa ação simples em bloco via URL assinada (GET sem form)."""
        acao_url = await self._obter_acao_bloco_url(id_bloco, nome_acao)
        r = await self._http.get(acao_url, headers={"Referer": str(self._inbox_url)})
        if r.status_code not in (200, 302):
            msg = f"{nome_acao} status={r.status_code}"
            raise SEIConnectionError(msg)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)
        return {"ok": True, "idBloco": id_bloco, "mensagem": mensagem}

    async def concluir_bloco_assinatura_web(self, id_bloco: str) -> dict:
        """Conclui bloco de assinatura via scraper web."""
        return await self._executar_acao_bloco(
            id_bloco, "bloco_assinatura_concluir", "Bloco concluído com sucesso."
        )

    async def reabrir_bloco_assinatura_web(self, id_bloco: str) -> dict:
        """Reabre bloco de assinatura concluído via scraper web."""
        return await self._executar_acao_bloco(
            id_bloco, "bloco_assinatura_reabrir", "Bloco reaberto com sucesso."
        )

    async def retornar_bloco_assinatura_web(self, id_bloco: str) -> dict:
        """Retorna bloco de assinatura para a unidade de origem via scraper web."""
        return await self._executar_acao_bloco(
            id_bloco, "bloco_assinatura_retornar", "Bloco retornado para a unidade de origem."
        )

    async def excluir_bloco_assinatura_web(self, id_bloco: str) -> dict:
        """Exclui bloco de assinatura via scraper web."""
        return await self._executar_acao_bloco(
            id_bloco, "bloco_assinatura_excluir", "Bloco excluído com sucesso."
        )

    async def listar_documentos_bloco_assinatura_web(self, id_bloco: str) -> list[dict]:
        """Lista documentos de um bloco de assinatura via scraper web."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        lista_url = await self._obter_link_toolbar("bloco_assinatura_listar")
        r = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        pat = re.compile(
            rf"controlador\.php\?[^\"'\s]*(?:acao=bloco_assinatura_alterar|bloco_assinatura_alterar)[^\"'\s]*id_bloco={re.escape(id_bloco)}[^\"'\s]*infra_hash=[a-fA-F0-9]+"
            rf"|controlador\.php\?[^\"'\s]*id_bloco={re.escape(id_bloco)}[^\"'\s]*acao=bloco_assinatura_alterar[^\"'\s]*infra_hash=[a-fA-F0-9]+"
        )
        m = pat.search(body)
        if not m:
            logger.warning(
                "listar_documentos_bloco_assinatura_web: link de detalhe não encontrado para bloco %s",
                id_bloco,
            )
            return []
        detail_url = urljoin(sei_base, m.group().replace("&amp;", "&"))
        r2 = await self._http.get(detail_url, headers={"Referer": lista_url})
        _check(r2)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        soup = BeautifulSoup(body2, "html.parser")
        tbl = soup.find("table", id=re.compile(r"tblDocumentos?", re.IGNORECASE))
        if tbl is None:
            tbl = soup.find("table", class_=re.compile(r"infraTable", re.IGNORECASE))
        docs: list[dict] = []
        if tbl is not None:
            for tr in tbl.find_all("tr")[1:]:
                tds = tr.find_all("td")
                if len(tds) < _BLOCK_TABLE_MIN_COLS:
                    continue
                tipo = tds[0].get_text(" ", strip=True)
                num = tds[1].get_text(" ", strip=True)
                id_doc = ""
                for a in tr.find_all("a", href=re.compile(r"id_documento=\d+")):
                    md = re.search(r"id_documento=(\d+)", _tag_str(a, "href"))
                    if md:
                        id_doc = md.group(1)
                        break
                docs.append({"idDocumento": id_doc, "tipo": tipo, "numero": num})
        return docs

    async def alterar_bloco_assinatura_web(self, id_bloco: str, descricao: str) -> dict:
        """Altera descrição de um bloco de assinatura via scraper web."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        lista_url = await self._obter_link_toolbar("bloco_assinatura_listar")
        r_list = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        _check(r_list)
        body_list = _decode_response(r_list.content, r_list.headers.get("content-type", ""))
        pat = re.compile(
            rf"controlador\.php\?[^\"'\s]*acao=bloco_assinatura_alterar[^\"'\s]*id_bloco={re.escape(id_bloco)}[^\"'\s]*infra_hash=[a-fA-F0-9]+"
            rf"|controlador\.php\?[^\"'\s]*id_bloco={re.escape(id_bloco)}[^\"'\s]*acao=bloco_assinatura_alterar[^\"'\s]*infra_hash=[a-fA-F0-9]+"
        )
        m = pat.search(body_list)
        if not m:
            msg = f"Link de edição não encontrado para bloco {id_bloco}."
            raise SEIParseError(msg)
        edit_url = urljoin(sei_base, m.group().replace("&amp;", "&"))
        r = await self._http.get(edit_url, headers={"Referer": lista_url})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Form de edição de bloco não encontrado."
            raise SEIParseError(msg)
        action = _tag_str(form, "action").replace("&amp;", "&")
        post_url = urljoin(sei_base, action) if action else edit_url
        post_data: list[tuple[str, str]] = []
        for inp in form.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                post_data.append((n, _tag_str(inp, "value")))
        sbm = _extrair_submit_btn(form)
        if sbm:
            post_data.append(sbm)
        post_data.append(("txtDescricao", descricao))
        r2 = await self._http.post(
            post_url,
            content=urlencode(post_data).encode("iso-8859-1"),
            headers={"Referer": edit_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        if r2.status_code not in (200, 302):
            msg = f"POST bloco_assinatura_alterar status={r2.status_code}"
            raise SEIConnectionError(msg)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body2)
        if erro:
            raise SEIConnectionError(erro)
        return {"ok": True, "idBloco": id_bloco, "descricao": descricao}

    async def _autocomplete_ajax(
        self, acao_ajax: str, termo: str, campo: str = "termo"
    ) -> list[dict]:
        """Chama endpoint AJAX genérico controlador_ajax.php e retorna lista de itens."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        r = await self._http.get(
            f"{sei_base}controlador_ajax.php",
            params={"acao_ajax": acao_ajax, campo: termo},
            headers={"Referer": str(self._inbox_url)},
        )
        if not r.is_success:
            logger.warning(
                "_autocomplete_ajax %r: HTTP %s para termo=%r", acao_ajax, r.status_code, termo
            )
            return []
        try:
            raw = r.json()
        except ValueError:
            logger.warning(
                "_autocomplete_ajax %r: resposta não-JSON (HTTP %s) para termo=%r",
                acao_ajax,
                r.status_code,
                termo,
            )
            return []
        return raw if isinstance(raw, list) else []

    async def pesquisar_assuntos_web(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa assuntos via AJAX assunto_auto_completar."""
        if not filtro:
            msg = (
                "Em modo web (sem mod-wssei), filtro é obrigatório para pesquisar assuntos. "
                "Informe pelo menos 1 caractere (nome ou código do assunto)."
            )
            raise SEIValidationError(msg)
        raw = await self._autocomplete_ajax("assunto_auto_completar", filtro)
        assuntos: list[dict[str, str]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            assuntos.append(
                {
                    "id": str(item.get("id", item.get("value", ""))),
                    "nome": str(item.get("nome", item.get("descricao", item.get("label", "")))),
                    "codigo": str(item.get("codigo", "")),
                }
            )
        return {"assuntos": assuntos, "total_itens": len(assuntos)}

    async def pesquisar_contatos_web(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa contatos via AJAX contato_auto_completar."""
        if not filtro:
            msg = (
                "Em modo web (sem mod-wssei), filtro é obrigatório para pesquisar contatos. "
                "Informe pelo menos 1 caractere (nome ou CPF/CNPJ)."
            )
            raise SEIValidationError(msg)
        raw = await self._autocomplete_ajax("contato_auto_completar", filtro)
        contatos: list[dict[str, str]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            contatos.append(
                {
                    "id": str(item.get("id", item.get("value", ""))),
                    "nome": str(item.get("nome", item.get("descricao", item.get("label", "")))),
                    "sigla": str(item.get("sigla", "")),
                }
            )
        return {"contatos": contatos, "total_itens": len(contatos)}

    async def pesquisar_textos_padrao_web(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa textos padrão via AJAX texto_padrao_auto_completar."""
        raw = await self._autocomplete_ajax(
            "texto_padrao_auto_completar", filtro or "", campo="str_texto_padrao"
        )
        if not raw:
            raw = await self._autocomplete_ajax("texto_padrao_pesquisar", filtro or "")
        textos: list[dict[str, str]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            textos.append(
                {
                    "id": str(item.get("id", item.get("value", ""))),
                    "nome": str(item.get("nome", item.get("descricao", item.get("label", "")))),
                }
            )
        return {"textos": textos, "total_itens": len(textos)}

    async def consultar_atribuicao_web(self, protocolo: str) -> dict:
        """Retorna o usuário atualmente atribuído ao processo.

        Lê a coluna "Atribuição" da visualização detalhada da caixa da unidade —
        diferente do form de atribuição (que serve para *definir*, não exibe o
        atual). Requer que o processo esteja aberto na unidade atual; varre as
        páginas da caixa até encontrá-lo.
        """
        await self.ensure_authenticated()
        alvo = re.sub(r"\s", "", protocolo)
        seen = 0
        max_paginas = 50  # trava de segurança contra loop infinito
        for pagina in range(max_paginas):
            _, html = await self.fetch_inbox(detalhada=True, pagina=pagina)
            _, rows = parse_inbox(html)
            for row in rows:
                proto = re.sub(r"\s", "", row.get("protocolo", "") or row.get("Processo", ""))
                if proto == alvo:
                    atrib = (row.get("Atribuição", "") or "").strip()
                    return {
                        "id_usuario": atrib,
                        "nome": atrib,
                        "atribuido": bool(atrib),
                    }
            seen += len(rows)
            _raw_total = self._form_hidden.get("hdnDetalhadoNroItens", "0") or "0"
            total = _safe_int(_raw_total)
            # `total == 0` quando o hidden não existe nesta instância — nesse caso
            # pagina até esvaziar (senão `seen >= 0` quebraria já na página 0).
            if not rows or (total > 0 and seen >= total):
                break
        return {
            "id_usuario": "",
            "nome": "",
            "atribuido": False,
            "_aviso": (
                "Processo não encontrado na caixa da unidade atual (pode estar "
                "concluído ou em outra unidade). A atribuição só é legível para "
                "processos abertos na unidade."
            ),
        }

    async def pesquisar_hipoteses_legais_web(self, filtro: str = "") -> dict:
        """Extrai hipóteses legais do select selHipoteseLegal em procedimento_cadastrar."""
        await self.ensure_authenticated()
        cadastrar_url = await self._obter_link_toolbar("procedimento_cadastrar")
        r = await self._http.get(cadastrar_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        soup = BeautifulSoup(body, "html.parser")
        sel = soup.find("select", {"name": re.compile(r"selHipoteseLegal", re.IGNORECASE)})
        if sel is None:
            sel = soup.find("select", id=re.compile(r"selHipoteseLegal", re.IGNORECASE))
        hipoteses: list[dict[str, str]] = []
        if sel is not None:
            for opt in sel.find_all("option"):
                v = _tag_str(opt, "value")
                t = opt.get_text(strip=True)
                if not v:
                    continue
                if filtro and filtro.lower() not in t.lower():
                    continue
                hipoteses.append({"id": v, "nome": t})
        return {"hipoteses": hipoteses, "total_itens": len(hipoteses)}

    async def pesquisar_marcadores_web(self, filtro: str = "") -> dict:
        """Extrai marcadores disponíveis via select selMarcador do form marcacao_salvar."""
        await self.ensure_authenticated()
        async with self._form_lock:
            _has_links = bool(self._trabalhar_links)
        if not _has_links:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            _links_snapshot = list(self._trabalhar_links)
        if not _links_snapshot:
            return {"marcadores": [], "total_itens": 0}
        opcoes: list[dict[str, str]] = []
        for protocolo in _links_snapshot:
            try:
                try:
                    form_info = await self.obter_form_acao(
                        protocolo, "andamento_marcador_cadastrar"
                    )
                    opcoes = form_info.get("selects", {}).get("selMarcador", [])
                except SEINotFoundError:
                    html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
                    sei_base = f"{self.sei_root}/sei/"
                    m = re.search(
                        r"(controlador\.php\?acao=andamento_marcador_gerenciar[^\"'\s]*infra_hash=[a-f0-9]+)",
                        html_arvore,
                    )
                    if not m:
                        continue
                    gerenciar_url = urljoin(sei_base, m.group(1).replace("&amp;", "&"))
                    r = await self._http.get(gerenciar_url, headers={"Referer": url_arvore})
                    _check(r)
                    soup = BeautifulSoup(r.text, "html.parser")
                    sel = soup.find("select", {"name": "selMarcador"})
                    if sel:
                        for opt in sel.find_all("option"):
                            v = _tag_str(opt, "value")
                            t = opt.get_text(strip=True)
                            if v:
                                opcoes.append({"value": v, "texto": t})
                    else:
                        btn = soup.find("button", id="btnAdicionar")
                        if btn:
                            onclick = _tag_str(btn, "onclick")
                            m_cad = re.search(r"location\.href='([^']+)'", onclick)
                            if m_cad:
                                cad_url = urljoin(sei_base, m_cad.group(1).replace("&amp;", "&"))
                                r_cad = await self._http.get(
                                    cad_url, headers={"Referer": gerenciar_url}
                                )
                                _check(r_cad)
                                soup_cad = BeautifulSoup(r_cad.text, "html.parser")
                                sel_cad = soup_cad.find("select", {"name": "selMarcador"})
                                if sel_cad:
                                    for opt in sel_cad.find_all("option"):
                                        v = _tag_str(opt, "value")
                                        t = opt.get_text(strip=True)
                                        if v:
                                            opcoes.append({"value": v, "texto": t})
                if opcoes:
                    break
            except (SEIParseError, SEINotFoundError, httpx.HTTPError, OSError) as exc:
                logger.debug("_obter_opcoes_marcador: falha em processo %r — %s", protocolo, exc)
                continue
        marcadores: list[dict[str, str]] = []
        for opt in opcoes:
            v = opt.get("value", "")
            t = opt.get("texto", "")
            if not v:
                continue
            if filtro and filtro.lower() not in t.lower():
                continue
            marcadores.append({"id": v, "nome": t})
        return {"marcadores": marcadores, "total_itens": len(marcadores)}

    async def _obter_soup_documento_receber(self, protocolo: str) -> BeautifulSoup:
        """Navega até o form documento_receber para um processo.

        Fluxo: arvore_montar → documento_escolher_tipo GET → POST hdnIdSerie=-1.
        Compartilhado entre pesquisar_tipos_documento_web e pesquisar_tipos_conferencia_web.
        """
        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        soup_arvore = BeautifulSoup(html_arvore, "html.parser")
        incluir_href: str | None = None
        for a in soup_arvore.find_all("a", href=re.compile(r"documento_escolher_tipo")):
            incluir_href = _tag_str(a, "href").replace("&amp;", "&")
            break
        if not incluir_href:
            msg = "Link documento_escolher_tipo não encontrado nas ações do processo."
            raise SEIParseError(msg)

        escolher_url = urljoin(sei_base, incluir_href)
        r3 = await self._http.get(escolher_url, headers={"Referer": url_arvore})
        _check(r3)
        body3 = _decode_response(r3.content, r3.headers.get("content-type", ""))
        soup3 = BeautifulSoup(body3, "html.parser")
        form3 = soup3.find("form", id="frmDocumentoEscolherTipo")
        if form3 is None:
            msg = "frmDocumentoEscolherTipo não encontrado"
            raise SEIParseError(msg)
        form3_action = _tag_str(form3, "action").replace("&amp;", "&")
        post3_url = urljoin(str(r3.url), form3_action)
        post3_data: dict[str, str] = {}
        for inp in form3.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                post3_data[n] = _tag_str(inp, "value")
        post3_data["hdnIdSerie"] = "-1"

        r4 = await self._http.post(post3_url, data=post3_data, headers={"Referer": str(r3.url)})
        _check(r4)
        return BeautifulSoup(
            _decode_response(r4.content, r4.headers.get("content-type", "")), "html.parser"
        )

    async def pesquisar_tipos_documento_web(self, filtro: str = "") -> dict:
        """Extrai tipos de documento (séries) via select selSerie em documento_receber."""
        await self.ensure_authenticated()
        async with self._form_lock:
            _has_links = bool(self._trabalhar_links)
        if not _has_links:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            _links_snapshot = list(self._trabalhar_links)
        if not _links_snapshot:
            return {"tipos": [], "total_itens": 0}
        soup: BeautifulSoup | None = None
        for protocolo in _links_snapshot:
            try:
                soup = await self._obter_soup_documento_receber(protocolo)
                break
            except SEIParseError as exc:
                logger.debug(
                    "pesquisar_tipos_documento_web: parse falhou para %r — %s", protocolo, exc
                )
                continue
        if soup is None:
            return {"tipos": [], "total_itens": 0}
        sel = soup.find("select", {"name": "selSerie"})
        tipos: list[dict[str, str]] = []
        if sel is not None:
            for opt in sel.find_all("option"):
                v = _tag_str(opt, "value")
                t = opt.get_text(strip=True)
                if not v or v == "-1":
                    continue
                if filtro and filtro.lower() not in t.lower():
                    continue
                tipos.append({"id": v, "nome": t})
        return {"tipos": tipos, "total_itens": len(tipos)}

    async def pesquisar_tipos_conferencia_web(self, filtro: str = "") -> dict:
        """Extrai tipos de conferência via select selTipoConferencia em documento_receber."""
        await self.ensure_authenticated()
        async with self._form_lock:
            _has_links = bool(self._trabalhar_links)
        if not _has_links:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            _links_snapshot = list(self._trabalhar_links)
        if not _links_snapshot:
            return {"tipos": [], "total_itens": 0}
        soup: BeautifulSoup | None = None
        for protocolo in _links_snapshot:
            try:
                soup = await self._obter_soup_documento_receber(protocolo)
                break
            except SEIParseError as exc:
                logger.debug(
                    "pesquisar_tipos_conferencia_web: parse falhou para %r — %s", protocolo, exc
                )
                continue
        if soup is None:
            return {"tipos": [], "total_itens": 0}
        sel = soup.find("select", {"name": re.compile(r"selTipoConferencia", re.IGNORECASE)})
        tipos: list[dict[str, str]] = []
        if sel is not None:
            for opt in sel.find_all("option"):
                v = _tag_str(opt, "value")
                t = opt.get_text(strip=True)
                if not v:
                    continue
                if filtro and filtro.lower() not in t.lower():
                    continue
                tipos.append({"id": v, "nome": t})
        return {"tipos": tipos, "total_itens": len(tipos)}

    async def _post_form_preservando(
        self, form: Tag, base_url: str, overrides: dict[str, str], referer: str
    ) -> httpx.Response:
        """Reenvia um form preservando seu estado atual, sobrescrevendo `overrides`.

        Codifica em ISO-8859-1 (charset do SEI) e força bytes ASCII para evitar
        double-encoding pelo httpx dos separadores `±`/`¥` dos campos multivalor.
        """
        action = _tag_str(form, "action").replace("&amp;", "&")
        post_url = urljoin(base_url, action) if action else base_url
        dados = _coletar_estado_form(form)
        dados.update(overrides)
        return await self._http.post(
            post_url,
            content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode("ascii"),
            headers={"Referer": referer, "Content-Type": "application/x-www-form-urlencoded"},
        )

    async def _abrir_form_cadastro_processo(self, tipo_processo: str) -> tuple[Tag, str]:
        """Navega até o form `frmProcedimentoCadastro` retornando (form, url_atual).

        Lida com os dois fluxos do SEI para iniciar processo:
        - `procedimento_escolher_tipo` (SEI moderno): escolhe o tipo primeiro
          (mostra todos os tipos via hdnFiltroTipoProcedimento='T', depois envia
          hdnIdTipoProcedimento) e então recebe o form de cadastro;
        - `procedimento_cadastrar` (instâncias antigas): abre o form direto.
        """
        try:
            escolher_url = await self._obter_link_toolbar("procedimento_escolher_tipo")
        except SEINotFoundError:
            escolher_url = None

        if escolher_url is not None:
            r = await self._http.get(escolher_url, headers={"Referer": str(self._inbox_url)})
            _check(r)
            soup = BeautifulSoup(
                _decode_response(r.content, r.headers.get("content-type", "")), "html.parser"
            )
            form = soup.find("form", id="frmProcedimentoEscolherTipo")
            if form is None:
                msg = "Form procedimento_escolher_tipo não encontrado."
                raise SEIParseError(msg)
            # Mostra todos os tipos (não só os favoritos da unidade).
            r = await self._post_form_preservando(
                form,
                str(r.url),
                {_FIELD_FILTRO_TIPO_PROC: "T", _FIELD_ID_TIPO_PROC: ""},
                str(r.url),
            )
            soup = BeautifulSoup(
                _decode_response(r.content, r.headers.get("content-type", "")), "html.parser"
            )
            form = soup.find("form", id="frmProcedimentoEscolherTipo")
            if form is None:
                msg = "Form de escolha de tipo não recarregou."
                raise SEIParseError(msg)
            # Seleciona o tipo desejado → recebe o form de cadastro.
            r = await self._post_form_preservando(
                form, str(r.url), {_FIELD_ID_TIPO_PROC: tipo_processo}, str(r.url)
            )
            soup = BeautifulSoup(
                _decode_response(r.content, r.headers.get("content-type", "")), "html.parser"
            )
            form = soup.find("form", id="frmProcedimentoCadastro")
            if form is None:
                erro = _extrair_erro_sei(
                    _decode_response(r.content, r.headers.get("content-type", ""))
                )
                raise SEIParseError(
                    erro or f"Form de cadastro não retornou para o tipo {tipo_processo}."
                )
            return form, str(r.url)

        cadastrar_url = await self._obter_link_toolbar("procedimento_cadastrar")
        r = await self._http.get(cadastrar_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)
        form = BeautifulSoup(body, "html.parser").find("form")
        if form is None:
            msg = "Form procedimento_cadastrar não encontrado."
            raise SEIParseError(msg)
        return form, str(r.url)

    @staticmethod
    def _serializar_assuntos(form: Tag, assuntos_ids: list[str]) -> str:
        """Serializa os assuntos no formato `hdnAssuntos` do SEI: `id±texto` por `¥`.

        Resolve cada id contra as opções pré-carregadas de `selAssuntos` (os
        assuntos sugeridos para o tipo). Quando `assuntos_ids` é vazio, usa todas
        as sugestões pré-carregadas — o mesmo default que o usuário recebe no form.
        """
        sel = form.find("select", {"name": "selAssuntos"})
        opcoes = {
            _tag_str(o, "value"): o.get_text(strip=True)
            for o in (sel.find_all("option") if isinstance(sel, Tag) else [])
            if _tag_str(o, "value")
        }
        if assuntos_ids:
            itens: list[tuple[str, str]] = []
            for aid in assuntos_ids:
                texto = opcoes.get(aid)
                if texto is None:
                    disponiveis = ", ".join(f"{v} ({t})" for v, t in opcoes.items()) or "nenhum"
                    msg = (
                        f"Assunto '{aid}' não está entre as sugestões do tipo de processo. "
                        f"Sugeridos: {disponiveis}. A criação web só aceita os assuntos "
                        "sugeridos para o tipo."
                    )
                    raise SEIValidationError(msg)
                itens.append((aid, texto))
        else:
            itens = list(opcoes.items())
        return "¥".join(f"{aid}±{texto}" for aid, texto in itens)

    async def criar_processo_web(self, dados: NovoProcessoWeb) -> dict:
        """Cria novo processo via scraper web do SEI.

        Fluxo: toolbar(escolher_tipo|cadastrar) → form de cadastro → POST salvar.

        Detalhes do form de cadastro que tornam o POST aceito pelo backend PHP:
        - `hdnFlagProcedimentoCadastro` deve ser '2' (o JS o vira de '1'→'2' antes
          de submeter; com '1' o servidor apenas re-exibe o form, sem salvar);
        - assuntos são obrigatórios e vão em `hdnAssuntos` no formato `id±texto`
          (ver `_serializar_assuntos`), não em `hdnIdAssunto`;
        - nível de acesso vai em `rdoNivelAcesso` (0=público, 1=restrito, 2=sigiloso);
        - `txtDescricao` é a especificação (máx. 100 caracteres).
        """
        await self.ensure_authenticated()

        form, url_atual = await self._abrir_form_cadastro_processo(dados.tipo_processo)

        overrides: dict[str, str] = {
            _FIELD_FLAG_PROC_CADASTRO: "2",
            _FIELD_NIVEL_ACESSO: dados.nivel_acesso,
            _FIELD_ASSUNTOS: self._serializar_assuntos(form, dados.assuntos_ids or []),
            # Comunica o tipo nos dois fluxos: no escolher_tipo o
            # _FIELD_ID_TIPO_PROC já vem setado; no form direto
            # (procedimento_cadastrar, instâncias antigas) o servidor lê
            # selTipoProcedimento. Setar ambos é inócuo no fluxo moderno.
            "selTipoProcedimento": dados.tipo_processo,
            _FIELD_ID_TIPO_PROC: dados.tipo_processo,
        }
        if dados.especificacao:
            overrides[_FIELD_DESCRICAO] = dados.especificacao
        if dados.hipotese_legal and dados.nivel_acesso in ("1", "2"):
            overrides["selHipoteseLegal"] = dados.hipotese_legal
        if dados.interessados_ids:
            # Interessados usam o mesmo infraLupaSelect dos assuntos
            # (selInteressadosProcedimento → _FIELD_INTERESSADOS): itens
            # `id±rótulo` separados por `¥`. O servidor vincula pelo id (o rótulo
            # é re-derivado), então usamos o id como rótulo. Um POST malformado é
            # detectado abaixo (form re-exibido) — nunca é silencioso.
            overrides[_FIELD_INTERESSADOS] = "¥".join(
                f"{iid}±{iid}" for iid in dados.interessados_ids
            )

        sbm = next((b for b in form.find_all("button") if _tag_str(b, "name") == "btnSalvar"), None)
        if sbm is not None:
            overrides["btnSalvar"] = _tag_str(sbm, "value") or "Salvar"
        else:
            par = _extrair_submit_btn(form)
            if par:
                overrides[par[0]] = par[1]

        r = await self._post_form_preservando(form, url_atual, overrides, url_atual)
        if r.status_code not in (200, 302):
            msg = f"POST de cadastro falhou com status={r.status_code}"
            raise SEIConnectionError(msg)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)

        soup = BeautifulSoup(body, "html.parser")
        # Sucesso: o servidor redireciona para procedimento_trabalhar (a árvore do
        # novo processo). Se ainda estamos no form de cadastro, o save foi rejeitado.
        if soup.find("form", id="frmProcedimentoCadastro") is not None:
            msg = (
                "Cadastro rejeitado pelo SEI (form re-exibido). Verifique tipo de "
                "processo, assuntos e nível de acesso."
            )
            raise SEIParseError(msg)

        protocolo = ""
        m_proto = re.search(r"(\d{4,5}\.\s?\d{6}/\d{4}-\d{2})", body)
        if m_proto:
            protocolo = m_proto.group(1).replace(" ", "")
        id_proc = ""
        m_url = re.search(r"id_procedimento=(\d+)", str(r.url))
        if m_url:
            id_proc = m_url.group(1)

        if not id_proc and not protocolo:
            msg = (
                "Processo aparentemente criado mas idProcedimento/protocolo não "
                "puderam ser extraídos da resposta."
            )
            raise SEIParseError(msg)

        return {
            "ok": True,
            "idProcedimento": id_proc,
            "protocoloFormatado": protocolo,
            "mensagem": "Processo criado com sucesso.",
        }

    async def alterar_processo_web(
        self,
        protocolo: str,
        especificacao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        observacao: str = "",
    ) -> dict:
        """Altera metadados de um processo via form procedimento_alterar (scraper web).

        Preserva TODOS os campos atuais do form (inputs/selects/textareas/hidden) e
        sobrescreve apenas os informados: especificação (txtDescricao), nível de
        acesso (rdoNivelAcesso), hipótese legal (selHipoteseLegal), observação
        (txaObservacoes). Campos vazios deixam o valor atual inalterado.
        """
        await self.ensure_authenticated()
        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"
        m = re.search(
            r"(controlador\.php\?acao=procedimento_alterar[^\"'\s]*infra_hash=[a-f0-9]+)",
            html_arvore,
        )
        if not m:
            msg = (
                "Ação 'procedimento_alterar' não encontrada no menu do processo. "
                "Verifique permissão e se o processo está aberto na unidade atual."
            )
            raise SEINotFoundError(msg)
        form_url = urljoin(sei_base, m.group(1).replace("&amp;", "&"))
        r = await self._http.get(form_url, headers={"Referer": url_arvore})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body)
        if erro:
            raise SEIConnectionError(erro)
        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Form procedimento_alterar não encontrado."
            raise SEIParseError(msg)

        # Sobrescreve apenas os campos informados; o resto é preservado por
        # _post_form_preservando (que coleta o estado atual do form).
        overrides: dict[str, str] = {
            # O JS do SEI vira este flag de '1'→'2' ao submeter; sem '2' o servidor
            # apenas re-exibe o form (a alteração não é salva).
            _FIELD_FLAG_PROC_CADASTRO: "2",
            # Assuntos são obrigatórios: re-serializa os já vinculados (senão o
            # servidor rejeita com "Informe os Assuntos").
            _FIELD_ASSUNTOS: self._serializar_assuntos(form, []),
        }
        if especificacao:
            overrides[_FIELD_DESCRICAO] = especificacao
        if nivel_acesso:
            overrides[_FIELD_NIVEL_ACESSO] = nivel_acesso
            # JS do SEI sincroniza este hidden ao mudar o rádio — replicamos.
            overrides[_FIELD_NIVEL_ACESSO_GLOBAL] = nivel_acesso
        if hipotese_legal:
            overrides["selHipoteseLegal"] = hipotese_legal
        if observacao:
            overrides["txaObservacoes"] = observacao

        sbm = _extrair_submit_btn(form)
        if sbm:
            overrides[sbm[0]] = sbm[1]

        r2 = await self._post_form_preservando(form, str(r.url), overrides, str(r.url))
        if r2.status_code not in {200, 302}:
            msg = f"POST procedimento_alterar status={r2.status_code}"
            raise SEIConnectionError(msg)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro2 = _extrair_erro_sei(body2)
        if erro2:
            raise SEIConnectionError(erro2)
        if (
            BeautifulSoup(body2, "html.parser").find("form", id="frmProcedimentoCadastro")
            is not None
        ):
            msg = (
                "Alteração rejeitada pelo SEI (form re-exibido). Verifique nível de "
                "acesso, hipótese legal e assuntos do processo."
            )
            raise SEIParseError(msg)
        self._invalidar_arvore(protocolo)
        return {"ok": True, "mensagem": "Processo alterado com sucesso.", "protocolo": protocolo}

    async def criar_documento_interno_web(
        self,
        protocolo: str,
        id_serie: str,
        descricao: str = "",
        nivel_acesso: str = "0",
        hipotese_legal: str = "",
    ) -> dict:
        """Cria documento interno em um processo via scraper web do SEI.

        Fluxo:
          1. arvore → link documento_escolher_tipo
          2. GET documento_escolher_tipo → busca link para id_serie
          3. GET editor_montar para o tipo escolhido
          4. POST documento_gerar com campos do editor (conteúdo vazio)

        O documento é criado vazio; use sei_editar_secao para inserir conteúdo.
        """
        await self.ensure_authenticated()

        html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
        sei_base = f"{self.sei_root}/sei/"

        # --- Step 1: encontrar link documento_escolher_tipo na árvore ---
        incluir_href: str | None = None
        soup_acoes = BeautifulSoup(html_arvore, "html.parser")
        for a in soup_acoes.find_all("a", href=re.compile(r"documento_escolher_tipo")):
            incluir_href = _tag_str(a, "href").replace("&amp;", "&")
            break
        if not incluir_href:
            for img in soup_acoes.find_all("img"):
                if "Incluir" in (img.get("title", "") or "") or "incluir" in (
                    img.get("src", "") or ""
                ):
                    pa = img.find_parent("a")
                    # Confirm the parent link points to documento_escolher_tipo,
                    # not "Incluir em Bloco" or other "incluir" toolbar actions.
                    if pa and "documento_escolher_tipo" in _tag_str(pa, "href"):
                        incluir_href = _tag_str(pa, "href").replace("&amp;", "&")
                        break

        if not incluir_href:
            msg = "Link 'Incluir Documento' não encontrado nas ações do processo."
            raise SEIParseError(msg)

        escolher_url = urljoin(sei_base, incluir_href)

        # --- Step 2: GET documento_escolher_tipo e encontrar link para id_serie ---
        r3 = await self._http.get(escolher_url, headers={"Referer": url_arvore})
        _check(r3)

        body3 = _decode_response(r3.content, r3.headers.get("content-type", ""))
        erro3 = _extrair_erro_sei(body3)
        if erro3:
            raise SEIConnectionError(erro3)

        # Se id_serie não fornecido — retorna lista de tipos disponíveis
        if not id_serie:
            soup3 = BeautifulSoup(body3, "html.parser")
            tipos = []
            for a in soup3.find_all("a", href=re.compile(r"id_serie=")):
                href = _tag_str(a, "href")
                m_s = re.search(r"id_serie=(\d+)", href)
                if m_s:
                    tipos.append({"id_serie": m_s.group(1), "nome": a.get_text(strip=True)})
            return {"tipos_disponiveis": tipos}

        # Encontra o link do editor para este id_serie
        m_editor = re.search(
            rf"(controlador\.php[^\"'\s]*acao=editor_montar[^\"'\s]*id_serie={re.escape(id_serie)}[^\"'\s]*infra_hash=[a-fA-F0-9]+)",
            body3,
        )
        if not m_editor:
            # Try reverse order: infra_hash before id_serie
            m_editor = re.search(
                rf"(controlador\.php[^\"'\s]*acao=editor_montar[^\"'\s]*infra_hash=[a-fA-F0-9]+[^\"'\s]*id_serie={re.escape(id_serie)}[^\"'\s]*)",
                body3,
            )
        if not m_editor:
            msg = (
                f"Link editor_montar para id_serie={id_serie} não encontrado. "
                "Use id_serie='' para listar os tipos disponíveis."
            )
            raise SEINotFoundError(msg)

        editor_url = urljoin(sei_base, m_editor.group(1).replace("&amp;", "&"))

        # --- Step 3: GET editor_montar ---
        r4 = await self._http.get(editor_url, headers={"Referer": str(r3.url)})
        _check(r4)

        body4 = _decode_response(r4.content, r4.headers.get("content-type", ""))
        erro4 = _extrair_erro_sei(body4)
        if erro4:
            raise SEIConnectionError(erro4)

        soup4 = BeautifulSoup(body4, "html.parser")
        form4 = soup4.find("form")
        if form4 is None:
            msg = "Form editor_montar não encontrado."
            raise SEINotFoundError(msg)

        action4 = _tag_str(form4, "action").replace("&amp;", "&")
        post_url4 = urljoin(sei_base, action4) if action4 else editor_url

        # --- Step 4: POST documento_gerar ---
        post_data4: list[tuple[str, str]] = []
        for inp in form4.find_all("input", type="hidden"):
            name = _tag_str(inp, "name")
            if name:
                post_data4.append((name, _tag_str(inp, "value")))

        # Botão submit obrigatório (PHP ignora POST sem ele silenciosamente)
        sbm4 = _extrair_submit_btn(form4)
        if sbm4:
            post_data4.append(sbm4)

        if descricao:
            post_data4.append(("txtDescricao", descricao))
        post_data4.append(("selNivelAcesso", nivel_acesso))
        if hipotese_legal and nivel_acesso in ("1", "2"):
            post_data4.append(("selHipoteseLegal", hipotese_legal))

        r5 = await self._http.post(
            post_url4,
            content=urlencode(post_data4).encode(),
            headers={"Referer": editor_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        if r5.status_code not in (200, 302):
            msg = f"POST documento_gerar falhou com status={r5.status_code}"
            raise SEIConnectionError(msg)
        body5 = _decode_response(r5.content, r5.headers.get("content-type", ""))
        erro5 = _extrair_erro_sei(body5)
        if erro5:
            raise SEIConnectionError(erro5)

        # Extrai id do documento criado da resposta / URL final
        id_doc = ""
        m_doc = re.search(r"id_documento=(\d+)", str(r5.url))
        if m_doc:
            id_doc = m_doc.group(1)
        if not id_doc:
            m_doc2 = re.search(r"IdDocumento[\"']?\s*[:=]\s*[\"']?(\d+)", body5)
            if m_doc2:
                id_doc = m_doc2.group(1)

        if not id_doc:
            msg = (
                "Documento aparentemente criado mas idDocumento não pôde ser extraído da resposta."
            )
            raise SEIParseError(msg)

        return {
            "ok": True,
            "idDocumento": id_doc,
            "protocolo": protocolo,
            "id_serie": id_serie,
            "mensagem": "Documento criado com sucesso.",
        }

    MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # limite de segurança (o SEI rejeita antes)

    async def incluir_documento_externo(
        self,
        protocolo_formatado: str,
        dados: DocumentoExternoInclusaoWeb | None = None,
    ) -> dict:
        """Inclui documento externo (upload de arquivo) em processo SEI via web.

        Fluxo:
        1. procedimento_trabalhar → frameset → arvore_montar
        2. Extrai Nos[0].acoes → link documento_escolher_tipo
        3. GET documento_escolher_tipo
        4. POST frmDocumentoEscolherTipo com hdnIdSerie=-1 → documento_receber
        5. Parse: upload URL, selSerie, user, unidade
        6. Se id_serie vazio → retorna tipos disponíveis
        7. POST multipart upload (filArquivo) → nome_upload#nome#data_hora#tamanho
        8. Build hdnAnexos + POST frmDocumentoCadastro

        Retorna:
            {"sucesso": True, "url_final": str}
            ou {"tipos_disponiveis": [{id, nome}, ...]} se id_serie=None
        """
        _d = dados or DocumentoExternoInclusaoWeb()
        arquivo_path = _d.arquivo_path
        nome_arquivo = _d.nome_arquivo
        id_serie = _d.id_serie
        data_elaboracao = _d.data_elaboracao
        nivel_acesso = _d.nivel_acesso
        hipotese_legal = _d.hipotese_legal
        conteudo = _d.conteudo

        await self.ensure_authenticated()

        async with self._form_lock:
            _in_links = protocolo_formatado in self._trabalhar_links
        if not _in_links:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            _in_links = protocolo_formatado in self._trabalhar_links
        if not _in_links:
            await self.pesquisar_processo(protocolo_formatado)

        async with self._form_lock:
            trab_url = urljoin(str(self._inbox_url), self._trabalhar_links[protocolo_formatado])

        # --- Step 1: trabalhar → frameset ---
        r1 = await self._http.get(trab_url, headers={"Referer": str(self._inbox_url)})
        _check(r1)
        if 'name="txtUsuario"' in r1.text or 'id="txtUsuario"' in r1.text:
            async with self._form_lock:
                self._form_action = None
            await self.login()
            return await self.incluir_documento_externo(protocolo_formatado, dados)

        soup_fs = BeautifulSoup(r1.text, "html.parser")
        ifr = soup_fs.find("iframe", id="ifrArvore")
        if not ifr:
            msg = "ifrArvore não encontrado no frameset"
            raise SEIParseError(msg)
        arvore_url = urljoin(str(r1.url), _tag_str(ifr, "src").replace("&amp;", "&"))

        # --- Step 2: arvore_montar → Nos[0].acoes ---
        r2 = await self._http.get(arvore_url, headers={"Referer": str(r1.url)})
        _check(r2)

        acoes_html = ""
        for pat in (
            r"(?s)Nos\[0\]\.acoes\s*=\s*'((?:[^'\\]|\\.)*)'",
            r'(?s)Nos\[0\]\.acoes\s*=\s*"((?:[^"\\]|\\.)*)"',
        ):
            m = re.search(pat, r2.text)
            if m:
                acoes_html = (
                    m.group(1).replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
                )
                break

        if not acoes_html:
            msg = (
                "Nos[0].acoes não encontrado — o processo pode estar concluído "
                "ou você não tem permissão para incluir documentos nele."
            )
            raise SEIParseError(msg)

        sei_base = f"{self.sei_root}/sei/"
        soup_acoes = BeautifulSoup(acoes_html, "html.parser")
        incluir_href: str | None = None
        for a in soup_acoes.find_all("a", href=re.compile(r"documento_escolher_tipo")):
            incluir_href = _tag_str(a, "href").replace("&amp;", "&")
            break
        if not incluir_href:
            for img in soup_acoes.find_all("img"):
                if "Incluir" in (img.get("title", "") or "") or "incluir" in (
                    img.get("src", "") or ""
                ):
                    pa = img.find_parent("a")
                    if pa:
                        incluir_href = _tag_str(pa, "href").replace("&amp;", "&")
                        break

        if not incluir_href:
            msg = (
                "Link 'Incluir Documento' não encontrado nas ações do processo. "
                "O processo pode estar concluído, sem tramitação para esta unidade, "
                "ou você não tem permissão. Tente reabrir o processo primeiro."
            )
            raise SEIParseError(msg)

        # --- Step 3: GET documento_escolher_tipo ---
        escolher_url = urljoin(sei_base, incluir_href)
        r3 = await self._http.get(escolher_url, headers={"Referer": str(r2.url)})
        _check(r3)

        body3 = _decode_response(r3.content, r3.headers.get("content-type", ""))
        soup3 = BeautifulSoup(body3, "html.parser")
        form3 = soup3.find("form", id="frmDocumentoEscolherTipo")
        if not form3:
            msg = "frmDocumentoEscolherTipo não encontrado"
            raise SEIParseError(msg)
        form3_action = _tag_str(form3, "action").replace("&amp;", "&")
        post3_url = urljoin(str(r3.url), form3_action)

        # --- Step 4: POST escolher com hdnIdSerie=-1 → documento_receber ---
        post3_data: dict[str, str] = {}
        for inp in form3.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                post3_data[n] = _tag_str(inp, "value")
        post3_data["hdnIdSerie"] = "-1"

        r4 = await self._http.post(post3_url, data=post3_data, headers={"Referer": str(r3.url)})
        _check(r4)

        body4 = _decode_response(r4.content, r4.headers.get("content-type", ""))

        # --- Step 5: Parse documento_receber ---
        # Validação de página: infraUpload deve estar presente no JS
        if "infraUpload" not in body4 and "frmDocumentoCadastro" not in body4:
            msg = "documento_receber não encontrado — verifique o processo e as permissões"
            raise SEIParseError(msg)

        # parse frmDocumentoCadastro
        soup4 = BeautifulSoup(body4, "html.parser")
        form4 = soup4.find("form", id="frmDocumentoCadastro")
        if not form4:
            msg = "frmDocumentoCadastro não encontrado em documento_receber"
            raise SEIParseError(msg)
        form4_action = _tag_str(form4, "action").replace("&amp;", "&")
        post4_url = urljoin(str(r4.url), form4_action)

        form4_data: dict[str, str] = {}
        for inp in form4.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                form4_data[n] = _tag_str(inp, "value")

        # selSerie options
        sel_serie = form4.find("select", attrs={"name": "selSerie"})
        tipos: list[dict] = []
        if sel_serie:
            for opt in sel_serie.find_all("option"):
                v = opt.get("value", "")
                t = opt.get_text(strip=True)
                if v and v not in ("-1", ""):
                    tipos.append({"id": v, "nome": t})

        # Se id_serie não informado, retorna tipos disponíveis
        if not id_serie:
            return {"tipos_disponiveis": tipos}

        # --- Step 6: Upload do arquivo ---
        if conteudo is not None:
            if not nome_arquivo:
                msg = "nome_arquivo é obrigatório quando conteudo é passado"
                raise ValueError(msg)
            nome = nome_arquivo
            file_bytes = conteudo
        else:
            if not arquivo_path:
                msg = "Informe arquivo_path ou conteudo"
                raise ValueError(msg)
            _path = Path(arquivo_path)
            if not await asyncio.to_thread(_path.is_file):
                msg = f"Arquivo não encontrado ou não é regular: {arquivo_path}"
                raise ValueError(msg)
            _stat = await asyncio.to_thread(_path.stat)
            if _stat.st_size > self.MAX_UPLOAD_BYTES:
                msg = f"Arquivo excede o limite de {self.MAX_UPLOAD_BYTES // 1024 // 1024} MB"
                raise ValueError(msg)
            nome = nome_arquivo or _path.name
            file_bytes = await asyncio.to_thread(_path.read_bytes)
        if len(file_bytes) > self.MAX_UPLOAD_BYTES:
            msg = f"Conteúdo excede o limite de {self.MAX_UPLOAD_BYTES // 1024 // 1024} MB"
            raise ValueError(msg)
        mime = mimetypes.guess_type(nome)[0] or "application/octet-stream"

        tam_int = len(file_bytes)
        if tam_int < _ONE_KB:
            tamanho_fmt = f"{tam_int} B"
        elif tam_int < _ONE_KB * _ONE_KB:
            tamanho_fmt = f"{tam_int / 1024:.1f} KB"
        else:
            tamanho_fmt = f"{tam_int / 1024 / 1024:.1f} MB"

        # Extrai URL de upload: new infraUpload('frmAnexos', 'URL')
        m_up = re.search(
            r"new infraUpload\(['\"][^'\"]*['\"],\s*['\"]([^'\"]*documento_upload_anexo[^'\"]*)['\"]",
            body4,
        )
        if not m_up:
            msg = "URL de upload (infraUpload) não encontrada em documento_receber"
            raise SEIParseError(msg)
        upload_url = urljoin(str(r4.url), m_up.group(1).replace("&amp;", "&"))

        r5 = await self._http.post(
            upload_url,
            files={"filArquivo": (nome, file_bytes, mime)},
            headers={"Referer": str(r4.url)},
        )
        _check(r5)

        # Upload response: pipe-separated fields — nome_upload, nome, mime, tamanho, data_hora
        up_parts = r5.text.strip().rstrip("#").split("#")
        if len(up_parts) < _UPLOAD_RESP_MIN_PARTS:
            msg = f"Resposta de upload inesperada: {r5.text!r}"
            raise SEIParseError(msg)
        nome_upload = up_parts[0]
        upload_dh = up_parts[_UPLOAD_RESP_IDX_DH] if len(up_parts) > _UPLOAD_RESP_IDX_DH else ""
        upload_tam = (
            up_parts[_UPLOAD_RESP_IDX_TAM] if len(up_parts) > _UPLOAD_RESP_IDX_TAM else str(tam_int)
        )

        # Extrai usuario e unidade da linha JS objTabelaAnexos.adicionar([..., 'CPF', 'SIGLA'])
        m_add = re.search(
            r"objTabelaAnexos\.adicionar\(\[.*?'([0-9]+)'\s*,\s*'([^']+)'\s*\]\)",
            body4,
            re.DOTALL,
        )
        usuario = m_add.group(1) if m_add else str(self._usuario)
        unidade = m_add.group(2) if m_add else ""

        # --- Step 7: POST frmDocumentoCadastro com hdnAnexos ---
        # SEI Pro extension usa ± (U+00B1) como separador, com encodeURIComponent
        # e remoção do byte alto UTF-8 (%C2) para manter %B1 (ISO-8859-1 ±).
        # O PHP servidor divide hdnAnexos em \xB1.
        _sep = "%B1"  # ± URL-encoded como ISO-8859-1 (PHP split target)

        def _qpart(s: str) -> str:
            # '+' fora do safe → vira %2B ('+' literal no nome não pode chegar
            # cru ao corpo x-www-form-urlencoded, onde decodifica como espaço);
            # espaço → %20 → '+' (convenção form-urlencoded)
            return _quote(s, safe="-.!~*'()_").replace("%20", "+")

        hdn_anexos = _sep.join(
            [
                _qpart(nome_upload),
                _qpart(nome),
                _qpart(upload_dh),
                _qpart(upload_tam),
                _qpart(tamanho_fmt),
                _qpart(usuario),
                _qpart(unidade),
            ]
        )

        # Monta body URL-encoded manualmente para hdnAnexos não ser duplo-codificado
        form4_data["hdnAnexos"] = ""  # placeholder — substituído abaixo
        form4_data["hdnIdSerie"] = id_serie
        form4_data["selSerie"] = id_serie
        form4_data["txtDataElaboracao"] = data_elaboracao or datetime.now(
            tz=UTC
        ).astimezone().date().strftime("%d/%m/%Y")
        form4_data[_FIELD_NIVEL_ACESSO_LOCAL] = nivel_acesso
        form4_data[_FIELD_NIVEL_ACESSO] = nivel_acesso
        if hipotese_legal and nivel_acesso in ("1", "2"):
            form4_data["selHipoteseLegal"] = hipotese_legal
        form4_data["rdoFormato"] = "N"  # nato-digital
        # JS submeter() altera de '1' → '2' antes do form.submit()
        form4_data[_FIELD_FLAG_DOC_CADASTRO] = "2"

        # Codifica todos os campos exceto hdnAnexos, depois concatena manualmente
        other_fields = {k: v for k, v in form4_data.items() if k != "hdnAnexos"}
        raw_body = urlencode(other_fields) + "&hdnAnexos=" + hdn_anexos

        r6 = await self._http.post(
            post4_url,
            content=raw_body.encode("ascii"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": str(r4.url),
            },
        )
        _check(r6)

        body6 = _decode_response(r6.content, r6.headers.get("content-type", ""))
        final_url = str(r6.url)
        sucesso = "arvore_visualizar" in final_url

        if not sucesso:
            soup6 = BeautifulSoup(body6, "html.parser")
            erros = []
            for cls in ["infraMsg", "infraMsgErro", "errMsg"]:
                el = soup6.find(class_=cls)
                if el:
                    erros.append(el.get_text(strip=True)[:300])
            scripts6 = re.findall(r"<script[^>]*>(.*?)</script>", body6, re.DOTALL | re.IGNORECASE)
            for sc in scripts6:
                if "alert(" in sc:
                    m_alert = re.search(r"alert\(['\"]([^'\"]+)['\"]", sc)
                    if m_alert:
                        erros.append(m_alert.group(1))
            msg = "; ".join(erros) if erros else f"URL final inesperada: {final_url}"
            msg = f"Falha ao incluir documento: {msg}"
            raise SEIParseError(msg)

        m_id = re.search(r"id_documento=(\d+)", final_url)
        id_doc = m_id.group(1) if m_id else ""
        self._invalidar_arvore(protocolo_formatado)
        return {
            "sucesso": True,
            "id_documento": id_doc,
            "url_final": final_url,
            "nome_arquivo": nome,
            "tamanho": tamanho_fmt,
        }

    async def listar_processos(
        self,
        pagina: int = 0,
        tipo: str = "",
        filtro: str = "",
        *,
        detalhada: bool = True,
        apenas_meus: bool = False,
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
            total_servidor = _safe_int(self._form_hidden.get("hdnDetalhadoNroItens", "0") or "0")
        else:
            total_servidor = _safe_int(
                self._form_hidden.get("hdnRecebidosNroItens", "0") or "0"
            ) + _safe_int(self._form_hidden.get("hdnGeradosNroItens", "0") or "0")
        if total_servidor == 0:
            total_servidor = len(rows)

        # Filtros client-side: aplicados após o parse, sobre os rows.
        rows_filtrados = rows
        if tipo:
            tipo_lower = tipo.lower()
            rows_filtrados = [
                r for r in rows_filtrados if tipo_lower in (r.get("Tipo", "") or "").lower()
            ]
        if filtro:
            filtro_lower = filtro.lower()
            rows_filtrados = [
                r
                for r in rows_filtrados
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
            # hdnDetalhadoNroItens/hdnRecebidosNroItens refletem o cap da página (500),
            # não o total real. Página cheia = provavelmente tem mais.
            "tem_proxima": len(rows) >= _INBOX_PAGE_CAP,
            "layout": layout,
        }

    async def pesquisar_usuarios_web(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa usuários no órgão via AJAX usuario_auto_completar."""
        if not filtro:
            return {
                "usuarios": [],
                "total_itens": 0,
                "_aviso": "Em modo web, filtro é obrigatório (mínimo 1 caractere).",
            }
        raw = await self._autocomplete_ajax("usuario_auto_completar", filtro)
        usuarios: list[dict[str, str]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            usuarios.append(
                {
                    "id_usuario": str(item.get("id", item.get("value", ""))),
                    "nome": str(item.get("nome", item.get("descricao", item.get("label", "")))),
                    "sigla": str(item.get("sigla", "")),
                }
            )
        return {"usuarios": usuarios, "total_itens": len(usuarios)}

    async def pesquisar_tipos_documento_externo_web(self, filtro: str = "") -> dict:
        """Extrai tipos de documento externo (séries) via form documento_receber."""
        return await self.pesquisar_tipos_documento_web(filtro=filtro)

    async def verificar_acesso_web(self, protocolo: str) -> dict:
        """Verifica se o usuário tem acesso a um processo via scraper web."""
        try:
            await self._garantir_link_trabalhar(protocolo)
        except (SEIError, httpx.HTTPError):
            return {"temAcesso": False, "protocolo": protocolo}
        return {"temAcesso": True, "protocolo": protocolo}

    async def _obter_soup_acompanhamentos(self) -> BeautifulSoup:
        """Obtém página acompanhamento_listar como BeautifulSoup."""
        await self.ensure_authenticated()
        lista_url = await self._obter_link_toolbar("acompanhamento_listar")
        r = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        return BeautifulSoup(
            _decode_response(r.content, r.headers.get("content-type", "")), "html.parser"
        )

    @staticmethod
    def _parse_acompanhamento_tabela(tbl: Tag | None, limit: int) -> list[dict]:
        """Extrai lista de processos de uma tabela da página acompanhamento_listar."""
        processos: list[dict] = []
        if tbl is None:
            return processos
        for tr in tbl.find_all("tr")[1:]:
            if len(processos) >= limit:
                break
            tds = tr.find_all("td")
            if not tds:
                continue
            entrada: dict[str, str] = {}
            # Primeira coluna: link com protocolo
            a = tds[0].find("a")
            if a is not None:
                txt = a.get_text(" ", strip=True)
                href = _tag_str(a, "href")
                mi = re.search(r"id_procedimento=(\d+)", href)
                if mi:
                    entrada["idProcedimento"] = mi.group(1)
                if txt:
                    entrada["protocoloFormatado"] = txt
            else:
                txt = tds[0].get_text(" ", strip=True)
                if txt:
                    entrada["protocoloFormatado"] = txt
            if len(tds) >= _ENTRY_TABLE_MIN_COLS:
                entrada["tipo"] = tds[1].get_text(" ", strip=True)
            if len(tds) >= _ENTRY_TABLE_OBS_COL:
                entrada["observacao"] = tds[2].get_text(" ", strip=True)
            if entrada:
                processos.append(entrada)
        return processos

    async def listar_meus_acompanhamentos_web(self, limit: int = 50) -> dict:
        """Lista processos com acompanhamento especial do usuário via scraper web."""
        soup = await self._obter_soup_acompanhamentos()
        tbls = soup.find_all("table", class_=re.compile(r"infraTable", re.IGNORECASE))
        tbl = tbls[0] if tbls else soup.find("table")
        processos = self._parse_acompanhamento_tabela(tbl, limit)
        return {"acompanhamentos": processos, "total_itens": len(processos)}

    async def listar_acompanhamentos_unidade_web(self, limit: int = 50) -> dict:
        """Lista processos com acompanhamento especial da unidade via scraper web."""
        soup = await self._obter_soup_acompanhamentos()
        tbls = soup.find_all("table", class_=re.compile(r"infraTable", re.IGNORECASE))
        tbl = tbls[1] if len(tbls) > 1 else (tbls[0] if tbls else soup.find("table"))
        processos = self._parse_acompanhamento_tabela(tbl, limit)
        return {"acompanhamentos": processos, "total_itens": len(processos)}

    async def listar_grupos_acompanhamento_web(self, filtro: str = "") -> dict:
        """Extrai grupos de acompanhamento do select selGrupoAcompanhamento (acompanhamento_gerenciar)."""
        await self.ensure_authenticated()
        async with self._form_lock:
            _has_links = bool(self._trabalhar_links)
        if not _has_links:
            await self.fetch_inbox(detalhada=False)
        async with self._form_lock:
            _links_snapshot = list(self._trabalhar_links)
        if not _links_snapshot:
            return {"grupos": [], "total_itens": 0}
        opcoes: list[dict[str, str]] = []
        for protocolo in _links_snapshot:
            try:
                form_info = await self.obter_form_acao(protocolo, "acompanhamento_gerenciar")
                opcoes = form_info.get("selects", {}).get("selGrupoAcompanhamento", [])
                break
            except (SEIParseError, SEINotFoundError):
                continue
        grupos: list[dict[str, str]] = []
        for opt in opcoes:
            v = opt.get("value", "")
            t = opt.get("texto", "")
            if not v:
                continue
            if filtro and filtro.lower() not in t.lower():
                continue
            grupos.append({"id": v, "nome": t})
        return {"grupos": grupos, "total_itens": len(grupos)}

    async def alterar_acompanhamento_web(
        self, protocolo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Altera acompanhamento especial de um processo via form acompanhamento_gerenciar."""
        campos: dict[str, str] = {}
        if grupo:
            campos["selGrupoAcompanhamento"] = grupo
        if observacao:
            campos["txaObservacao"] = observacao
        await self.executar_acao_processo(protocolo, "acompanhamento_gerenciar", campos)
        return {
            "ok": True,
            "protocolo": protocolo,
            "mensagem": "Acompanhamento alterado com sucesso.",
        }

    async def remover_acompanhamento_web(self, protocolo: str) -> dict:
        """Remove o(s) acompanhamento(s) especial(is) de um processo.

        Lê a tela `acompanhamento_gerenciar`, encontra cada `acaoExcluir('<id>')`,
        seta `hdnInfraItemId` e submete `frmGerenciarAcompanhamento` para a URL
        assinada de `acompanhamento_excluir`. Relê a cada remoção (o hash muda).
        """
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        removidos = 0
        max_iter_acompanhamento = 20
        for _iter in range(max_iter_acompanhamento):  # trava de segurança
            html_arvore, url_arvore = await self._arvore_do_processo(protocolo)
            m = re.search(
                r"(controlador\.php\?acao=acompanhamento_gerenciar[^\"'\s]*infra_hash=[a-f0-9]+)",
                html_arvore,
            )
            if not m:
                msg = "Ação de acompanhamento não disponível para este processo."
                raise SEINotFoundError(msg)
            r = await self._http.get(
                urljoin(sei_base, m.group(1).replace("&amp;", "&")), headers={"Referer": url_arvore}
            )
            _check(r)
            body = _decode_response(r.content, r.headers.get("content-type", ""))
            ids = re.findall(r"acaoExcluir\('(\d+)'", body)
            if not ids:
                break
            soup = BeautifulSoup(body, "html.parser")
            form = soup.find("form", id="frmGerenciarAcompanhamento") or soup.find("form")
            m_url = re.search(
                r"controlador\.php\?acao=acompanhamento_excluir[^\"'\s)]*infra_hash=[a-f0-9]+",
                body,
            )
            if form is None or m_url is None:
                msg = "Mecanismo de remoção de acompanhamento não encontrado."
                raise SEIParseError(msg)
            post_url = urljoin(sei_base, m_url.group(0).replace("&amp;", "&"))
            dados = _coletar_estado_form(form)
            dados["hdnInfraItemId"] = ids[0]
            rr = await self._http.post(
                post_url,
                content=urlencode(dados, encoding="iso-8859-1", errors="replace").encode("ascii"),
                headers={
                    "Referer": str(r.url),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            _check(rr)
            erro = _extrair_erro_sei(
                _decode_response(rr.content, rr.headers.get("content-type", ""))
            )
            if erro:
                raise SEIConnectionError(erro)
            removidos += 1
            self._invalidar_arvore(protocolo)
        else:
            msg = (
                f"Remoção de acompanhamento interrompida após {max_iter_acompanhamento} iterações "
                f"para {protocolo}. {removidos} removido(s); verifique se há mais."
            )
            raise SEIConnectionError(msg)
        if removidos == 0:
            msg = f"Nenhum acompanhamento especial aplicado em {protocolo}."
            raise SEINotFoundError(msg)
        return {"ok": True, "removidos": removidos, "protocolo": protocolo}

    async def listar_grupos_modelos_web(self, filtro: str = "") -> dict:
        """Lista grupos de modelos de documento via scraper web."""
        await self.ensure_authenticated()
        lista_url: str | None = None
        for nome_acao in ("grupo_modelos_listar", "modelos_grupos_listar"):
            try:
                lista_url = await self._obter_link_toolbar(nome_acao)
                break
            except SEINotFoundError:
                continue
        if not lista_url:
            return {
                "grupos": [],
                "total_itens": 0,
                "_aviso": "Página de grupos de modelos não encontrada.",
            }
        r = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        if not r.is_success:
            logger.warning("listar_grupos_modelos_web: HTTP %s de %s", r.status_code, lista_url)
            return {"grupos": [], "total_itens": 0}
        soup = BeautifulSoup(
            _decode_response(r.content, r.headers.get("content-type", "")), "html.parser"
        )
        grupos: list[dict[str, str]] = []
        for tbl in soup.find_all("table", class_=re.compile(r"infraTable", re.IGNORECASE)):
            for tr in tbl.find_all("tr")[1:]:
                tds = tr.find_all("td")
                if not tds:
                    continue
                nome = tds[0].get_text(" ", strip=True)
                if not nome or (filtro and filtro.lower() not in nome.lower()):
                    continue
                id_grupo = ""
                for a in tr.find_all("a", href=re.compile(r"id_grupo=\d+")):
                    mg = re.search(r"id_grupo=(\d+)", _tag_str(a, "href"))
                    if mg:
                        id_grupo = mg.group(1)
                        break
                grupos.append({"id": id_grupo, "nome": nome})
        return {"grupos": grupos, "total_itens": len(grupos)}

    async def listar_modelos_web(self, filtro: str = "", id_grupo: str = "") -> dict:
        """Lista modelos de documento via scraper web."""
        await self.ensure_authenticated()
        lista_url: str | None = None
        for nome_acao in ("modelos_listar", "modelo_listar"):
            try:
                lista_url = await self._obter_link_toolbar(nome_acao)
                break
            except SEINotFoundError:
                continue
        if not lista_url:
            return {
                "modelos": [],
                "total_itens": 0,
                "_aviso": "Página de modelos não encontrada.",
            }
        r = await self._http.get(lista_url, headers={"Referer": str(self._inbox_url)})
        if not r.is_success:
            logger.warning("listar_modelos_web: HTTP %s de %s", r.status_code, lista_url)
            return {"modelos": [], "total_itens": 0}
        soup = BeautifulSoup(
            _decode_response(r.content, r.headers.get("content-type", "")), "html.parser"
        )
        modelos: list[dict[str, str]] = []
        for tbl in soup.find_all("table", class_=re.compile(r"infraTable", re.IGNORECASE)):
            for tr in tbl.find_all("tr")[1:]:
                tds = tr.find_all("td")
                if not tds:
                    continue
                nome = tds[0].get_text(" ", strip=True)
                if not nome:
                    continue
                id_modelo = ""
                grp_id = ""
                for a in tr.find_all("a", href=re.compile(r"id_modelo=\d+")):
                    href = _tag_str(a, "href")
                    mm = re.search(r"id_modelo=(\d+)", href)
                    if mm:
                        id_modelo = mm.group(1)
                    mg = re.search(r"id_grupo=(\d+)", href)
                    if mg:
                        grp_id = mg.group(1)
                    break
                if id_grupo and grp_id and grp_id != id_grupo:
                    continue
                if filtro and filtro.lower() not in nome.lower():
                    continue
                entry: dict[str, str] = {"id": id_modelo, "nome": nome}
                if grp_id:
                    entry["id_grupo"] = grp_id
                modelos.append(entry)
        return {"modelos": modelos, "total_itens": len(modelos)}

    async def retirar_documento_bloco_assinatura_web(
        self, id_bloco: str, id_documento: str
    ) -> dict:
        """Retira documento de bloco de assinatura via scraper web."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        acao_url = await self._obter_acao_bloco_url(id_bloco, "bloco_assinatura_alterar")
        r = await self._http.get(acao_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        pat = re.compile(
            rf"controlador\.php\?[^\"'\s]*acao=bloco_assinatura_retirar_documento[^\"'\s]*id_documento={re.escape(id_documento)}[^\"'\s]*infra_hash=[a-fA-F0-9]+"
            rf"|controlador\.php\?[^\"'\s]*id_documento={re.escape(id_documento)}[^\"'\s]*acao=bloco_assinatura_retirar_documento[^\"'\s]*infra_hash=[a-fA-F0-9]+"
        )
        m = pat.search(body)
        if not m:
            msg = f"Link retirar documento {id_documento} não encontrado no bloco {id_bloco}."
            raise SEIParseError(msg)
        retirar_url = urljoin(sei_base, m.group().replace("&amp;", "&"))
        r2 = await self._http.get(retirar_url, headers={"Referer": acao_url})
        if r2.status_code not in (200, 302):
            msg = f"bloco_assinatura_retirar_documento status={r2.status_code}"
            raise SEIConnectionError(msg)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body2)
        if erro:
            raise SEIConnectionError(erro)
        return {
            "ok": True,
            "idBloco": id_bloco,
            "idDocumento": id_documento,
            "mensagem": "Documento retirado do bloco com sucesso.",
        }

    async def anotar_documento_bloco_assinatura_web(
        self, id_bloco: str, id_documento: str, descricao: str
    ) -> dict:
        """Cria ou altera anotação em documento de bloco via scraper web."""
        await self.ensure_authenticated()
        sei_base = f"{self.sei_root}/sei/"
        acao_url = await self._obter_acao_bloco_url(id_bloco, "bloco_assinatura_alterar")
        r = await self._http.get(acao_url, headers={"Referer": str(self._inbox_url)})
        _check(r)
        body = _decode_response(r.content, r.headers.get("content-type", ""))
        pat = re.compile(
            rf"controlador\.php\?[^\"'\s]*acao=bloco_assinatura_anotar_documento[^\"'\s]*id_documento={re.escape(id_documento)}[^\"'\s]*infra_hash=[a-fA-F0-9]+"
            rf"|controlador\.php\?[^\"'\s]*id_documento={re.escape(id_documento)}[^\"'\s]*acao=bloco_assinatura_anotar_documento[^\"'\s]*infra_hash=[a-fA-F0-9]+"
        )
        m = pat.search(body)
        if not m:
            msg = f"Link anotação documento {id_documento} não encontrado no bloco {id_bloco}."
            raise SEIParseError(msg)
        anotar_url = urljoin(sei_base, m.group().replace("&amp;", "&"))
        r2 = await self._http.get(anotar_url, headers={"Referer": acao_url})
        _check(r2)
        body2 = _decode_response(r2.content, r2.headers.get("content-type", ""))
        soup = BeautifulSoup(body2, "html.parser")
        form = soup.find("form")
        if form is None:
            msg = "Form de anotação não encontrado."
            raise SEIParseError(msg)
        action = _tag_str(form, "action").replace("&amp;", "&")
        post_url = urljoin(sei_base, action) if action else anotar_url
        post_data: list[tuple[str, str]] = []
        for inp in form.find_all("input", type="hidden"):
            n = _tag_str(inp, "name")
            if n:
                post_data.append((n, _tag_str(inp, "value")))
        sbm = _extrair_submit_btn(form)
        if sbm:
            post_data.append(sbm)
        post_data.append(("txaDescricao", descricao))
        r3 = await self._http.post(
            post_url,
            content=urlencode(post_data).encode("iso-8859-1"),
            headers={"Referer": anotar_url, "Content-Type": "application/x-www-form-urlencoded"},
        )
        if r3.status_code not in (200, 302):
            msg = f"POST anotação bloco status={r3.status_code}"
            raise SEIConnectionError(msg)
        body3 = _decode_response(r3.content, r3.headers.get("content-type", ""))
        erro = _extrair_erro_sei(body3)
        if erro:
            raise SEIConnectionError(erro)
        return {
            "ok": True,
            "idBloco": id_bloco,
            "idDocumento": id_documento,
            "mensagem": "Anotação salva com sucesso.",
        }


# ---------------------------------------------------------------------------
# Parsers de HTML (independentes de instância)
# ---------------------------------------------------------------------------


def _max_no_index(html: str) -> int:
    """Retorna o maior índice N em ``Nos[N]`` no HTML; -1 se não houver."""
    indices = [int(m.group(1)) for m in re.finditer(r"Nos\[(\d+)\]", html)]
    return max(indices) if indices else -1


def _renumerar_nos_chunk(chunk_html: str, offset: int) -> tuple[str, int]:
    """Soma ``offset`` a todos os índices ``Nos[N]`` do chunk.

    Evita que chunks expandidos (pasta, paginação) colidam com o array Nos[]
    do HTML principal quando concatenados. Retorna o HTML reescrito e o
    próximo offset livre.
    """
    max_idx = _max_no_index(chunk_html)
    if max_idx < 0:
        return chunk_html, offset
    new_html = re.sub(
        r"Nos\[(\d+)\]",
        lambda m: f"Nos[{int(m.group(1)) + offset}]",
        chunk_html,
    )
    return new_html, offset + max_idx + 1


def parse_arvore_nos(html: str) -> list[dict]:
    """Extrai o array `Nos[]` do JS de arvore_montar.php.

    Cada nó é construído como `Nos[i] = new infraArvoreNo(tipo, id, pai, link,
    target, label, tooltip, icone, ...)`. Retorna lista de dicts com as
    primeiras 8 posições nomeadas. O primeiro elemento (Nos[0]) é a raiz —
    o próprio processo.
    """
    acoes_map: dict[str, str] = {}
    src_map: dict[str, str] = {}

    for m_ac in re.finditer(r"Nos\[(\d+)\]\.acoes\s*=\s*(['\"])(.*?)\2;", html):
        idx = m_ac.group(1)
        val = m_ac.group(3)
        val = val.replace(r"\'", "'").replace(r"\"", '"')
        acoes_map[idx] = val

    for m_src in re.finditer(r"Nos\[(\d+)\]\.src\s*=\s*(['\"])(.*?)\2;", html):
        idx = m_src.group(1)
        val = m_src.group(3)
        val = val.replace(r"\'", "'").replace(r"\"", '"')
        src_map[idx] = val

    out: list[dict] = []
    for m in re.finditer(
        r"(?s)Nos\[(\d+)\]\s*=\s*new infraArvoreNo\(([^;]*?)\);",
        html,
    ):
        idx_str = m.group(1)
        args_str = m.group(2)
        # tokenizer simples: separa por vírgula respeitando aspas e contra-barras
        args: list[str] = []
        cur = ""
        in_str = False
        quote_char = None
        is_escaped = False
        for ch in args_str:
            if in_str:
                cur += ch
                if is_escaped:
                    is_escaped = False
                elif ch == "\\":
                    is_escaped = True
                elif ch == quote_char:
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

        if len(args) >= _DOC_LINK_ARGS_MIN:
            out.append(
                {
                    "tipo_no": unquote(args[0]),
                    "id": unquote(args[1]),
                    "pai": unquote(args[2]),
                    "link": unquote(args[3]),
                    "target": unquote(args[4]),
                    "label": unquote(args[5]),
                    "tooltip": unquote(args[6]),
                    "icone": unquote(args[_DOC_LINK_ARGS_MIN])
                    if len(args) > _DOC_LINK_ARGS_MIN
                    else "",
                    "acoes_html": acoes_map.get(idx_str, ""),
                    "src": src_map.get(idx_str, ""),
                }
            )
    return out


_RE_PARENS = re.compile(r"^\s*\(\s*|\s*\)\s*$")
# Parseia label de documento: "Despacho GPF 2874369" ou "Relatório (2869849)"
# Três alternações com grupos nomeados por ramo para facilitar detecção de qual casou:
#   ramo "interno":  Tipo SIGLA NUMERO
#   ramo "externo":  Tipo (NUMERO)  — corpo pode conter sigla; extraída por _RE_DOC_LABEL_SIGLA
#   ramo "fallback": qualquer coisa com número ≥5 dígitos no final
_RE_DOC_LABEL = re.compile(
    r"^(?P<int_tipo>.+?)\s+(?P<int_sigla>[A-Z][A-Z0-9/_-]+)\s+(?P<int_num>\d+)$"  # interno
    r"|^(?P<ext_corpo>.+?)\s+\((?P<ext_num>\d+)\)$"  # externo
    r"|(?P<fb_num>\d{5,})$"  # fallback: número longo no final
)
_RE_DOC_LABEL_SIGLA = re.compile(r"^(?P<tipo>.+?)\s+(?P<sigla>[A-Z][A-Z0-9/_-]+)\s+\d*$")


_RE_TOOLTIP = re.compile(r"infraTooltipMostrar\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")

# Colunas sem header textual na Visualização Detalhada do painel de processos do SEI.
# A ordem é invariante: checkbox / ícones de status / link do processo / atribuição.
# Colunas além do índice 3 recebem nome genérico ("col4", "col5"…) e dependem da
# configuração do painel de cada usuário.
_COLUNAS_DETALHADA: dict[int, str] = {
    0: "_check",
    1: "icones",
    2: "_processo",
    3: "atribuicao",
}


def _parse_doc_label(label: str) -> dict:
    """Parseia o label de um nó DOCUMENTO da árvore do SEI.

    Formatos conhecidos:
    - Interno: "Despacho GPF 2874369"  → tipo=Despacho, sigla=GPF, numero=2874369
    - Externo: "Relatório Geral (2869849)" → tipo=Relatório Geral, numero=2869849
    - Misto:   "Comprovante de envio e-CGU - SA 4 (2869849)"

    Retorna dict com chaves opcionais: tipo_documento, sigla_unidade, numero_sei.
    """
    result: dict[str, str] = {}
    if not label:
        return result

    m = _RE_DOC_LABEL.search(label)
    if m is None:
        result["tipo_documento"] = label
        return result

    if m.group("int_tipo") is not None:
        # Ramo interno: "Tipo SIGLA NUMERO"
        result["tipo_documento"] = m.group("int_tipo").strip()
        result["sigla_unidade"] = m.group("int_sigla")
        result["numero_sei"] = m.group("int_num")
    elif m.group("ext_corpo") is not None:
        # Ramo externo: "Tipo (NUMERO)" — corpo pode conter sigla opcional
        corpo = m.group("ext_corpo").strip()
        result["numero_sei"] = m.group("ext_num")
        m2 = _RE_DOC_LABEL_SIGLA.match(corpo)
        if m2:
            result["tipo_documento"] = m2.group("tipo").strip()
            result["sigla_unidade"] = m2.group("sigla")
        else:
            result["tipo_documento"] = corpo
    else:
        # Ramo fallback: número longo encontrado no final
        result["numero_sei"] = m.group("fb_num")
        result["tipo_documento"] = label[: m.start()].strip()

    return result


def _extract_tooltip(link_tag: Tag, row: dict) -> None:
    """Extrai especificacao e tipo do onmouseover do link do processo.

    O SEI renderiza um tooltip JS em TODOS os links de processo da inbox:
        onmouseover="return infraTooltipMostrar('Especificação','Tipo Processual')"

    Esse tooltip contém a especificação INDEPENDENTE de a coluna estar
    habilitada no painel — é sempre renderizado.
    """
    mouseover = str(link_tag.get("onmouseover", ""))
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
                col_names.append(_COLUNAS_DETALHADA.get(i, f"col{i}"))

        for tr in tbl.find_all("tr", id=re.compile(r"^P\d+$")):
            tds = tr.find_all("td", recursive=False)
            row: dict[str, Any] = {"id_procedimento": tr["id"][1:]}
            link = tr.find("a", href=re.compile(r"acao=procedimento_trabalhar"))
            if link is not None:
                row["protocolo"] = link.get_text(" ", strip=True)
                # Especificação + tipo estão no tooltip do link do processo:
                # Tooltip do link: onmouseover com infraTooltipMostrar(Especificação, Tipo).
                # Disponível INDEPENDENTE de a coluna estar habilitada no painel.
                _extract_tooltip(link, row)
            if len(tds) >= _INBOX_TABLE_MIN_COLS:
                icones = []
                for img in tds[1].find_all("img"):
                    title = _tag_str(img, "title") or _tag_str(img, "alt")
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
                        if name == "atribuicao":
                            val = _RE_PARENS.sub("", val).strip()
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
            if len(tds) >= _INBOX_TABLE_MIN_COLS:
                icones = []
                for img in tds[1].find_all("img"):
                    title = _tag_str(img, "title") or _tag_str(img, "alt")
                    if title:
                        icones.append(title.strip())
                if icones:
                    row["icones"] = icones
            if len(tds) >= _INBOX_ATRIB_COL:
                atrib_text = _RE_PARENS.sub("", tds[-1].get_text(" ", strip=True)).strip()
                if atrib_text:
                    row["atribuicao"] = atrib_text
            rows.append(row)

    if found_any:
        return ("resumida", rows)
    return ("desconhecido", [])


# Tabelas de listas conhecidas das páginas de consulta — excluídas da
# extração genérica de pares label/valor de metadados.
# Nota: tblSobrestamento é intencionalmente excluída deste conjunto porque
# documento_consultar usa-a para pares chave/valor de metadados.
_TABELAS_LISTA = frozenset(
    {
        "tblAssinaturas",
        "tblCiencias",
        "tblUnidadesProcesso",
        "tblAndamento",
        "tblInteressados",
        "tblHistorico",
        "tblDocumentos",
    }
)


def _extrair_metadados_tabelas(soup: BeautifulSoup, result: dict[str, object]) -> None:
    """Extrai pares label/valor (th + td) das tabelas de metadados da página.

    Ignora as tabelas de listas conhecidas (assinaturas, ciências, etc.) e
    linhas de cabeçalho (duas células <th>), que não são pares label/valor.
    """
    for tbl in soup.find_all("table"):
        if tbl is None:
            continue
        if _tag_str(tbl, "id") in _TABELAS_LISTA:
            continue
        for tr in tbl.find_all("tr"):
            cels = tr.find_all(["th", "td"])
            if len(cels) != _META_TABLE_PAIR:
                continue
            if cels[0].name == "th" and cels[1].name == "th":
                continue  # linha de cabeçalho, não par label/valor
            k = cels[0].get_text(" ", strip=True).rstrip(":").lower()
            v = cels[1].get_text(" ", strip=True)
            if k and v and len(k) < _META_KEY_MAX_LEN:
                result[k.replace(" ", "_").replace("/", "_")] = v


def _parse_documento_consultar(html: str, id_documento: str) -> dict:
    """Extrai metadados, assinaturas e ciências de documento_consultar."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, object] = {"id_documento": id_documento}

    _extrair_metadados_tabelas(soup, result)

    # -- assinaturas: tblAssinaturas --
    assinaturas: list[dict] = []
    tbl_ass = soup.find("table", id="tblAssinaturas")
    if tbl_ass is not None:
        for tr in tbl_ass.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) >= _SIG_TABLE_COLS:
                assinaturas.append(
                    {
                        "assinante": tds[0].get_text(" ", strip=True),
                        "cargo": tds[1].get_text(" ", strip=True),
                        "data_hora": tds[2].get_text(" ", strip=True),
                    }
                )
    result["assinaturas"] = assinaturas

    # -- ciências: tblCiencias --
    ciencias: list[dict] = []
    tbl_cien = soup.find("table", id="tblCiencias")
    if tbl_cien is not None:
        for tr in tbl_cien.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) >= _SIG_TABLE_COLS:
                ciencias.append(
                    {
                        "usuario": tds[0].get_text(" ", strip=True),
                        "cargo": tds[1].get_text(" ", strip=True),
                        "data_hora": tds[2].get_text(" ", strip=True),
                    }
                )
    result["ciencias"] = ciencias

    return result


def _parse_procedimento_consultar(html: str, protocolo: str) -> dict:
    """Extrai unidades abertas, interessados e sobrestamento de procedimento_consultar."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, object] = {"protocolo": protocolo}

    _extrair_metadados_tabelas(soup, result)

    # -- unidades abertas: tblUnidadesProcesso --
    # (tblAndamento NÃO serve de fallback: é histórico com layout
    # data/unidade/usuário/descrição, não lista de unidades abertas)
    unidades: list[dict] = []
    tbl_un = soup.find("table", id="tblUnidadesProcesso")
    if tbl_un is not None:
        for tr in tbl_un.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if tds:
                entry: dict[str, str] = {"unidade": tds[0].get_text(" ", strip=True)}
                if len(tds) >= _STATUS_TABLE_MIN_COLS:
                    entry["situacao"] = tds[1].get_text(" ", strip=True)
                unidades.append(entry)
    # Fallback: procura qualquer link de unidade
    if not unidades:
        for a in soup.find_all("a", href=re.compile(r"acao=unidade_visualizar")):
            txt = a.get_text(" ", strip=True)
            if txt:
                unidades.append({"unidade": txt})
    result["unidades_abertas"] = unidades

    # -- interessados: busca por label ou tabela --
    interessados: list[str] = []
    tbl_int = soup.find("table", id="tblInteressados")
    if tbl_int is not None:
        for tr in tbl_int.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if tds:
                v = tds[0].get_text(" ", strip=True)
                if v:
                    interessados.append(v)
    if not interessados and "interessados" in result:
        interessados = [str(result.pop("interessados"))]
    result["interessados"] = interessados

    # -- sobrestamento: campo "Sobrestado" ou tabela tblSobrestamento --
    sobrestamentos: list[dict] = []
    tbl_sob = soup.find("table", id="tblSobrestamento")
    if tbl_sob is not None:
        for tr in tbl_sob.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) >= _STATUS_TABLE_MIN_COLS:
                sobrestamentos.append(
                    {
                        "motivo": tds[0].get_text(" ", strip=True),
                        "data": tds[1].get_text(" ", strip=True),
                    }
                )
    result["sobrestamentos"] = sobrestamentos

    return result
