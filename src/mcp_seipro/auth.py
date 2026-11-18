"""OAuth 2.1 provider para MCP SEI Pro.

As credenciais do SEI (url, usuario, senha, orgao) são informadas pelo
usuário na tela de login OAuth. O servidor as CRIPTOGRAFA (AES-256-GCM) dentro
do token e não as guarda em disco nem em banco. A cada request MCP o servidor
abre o token para obter as credenciais.

Tokens (access e refresh) são opacos: `s2.` + base64(nonce ‖ AES-GCM(payload)).
Sem a chave, o conteúdo é ilegível e qualquer alteração invalida o token.
Antes (até a v0.6) eram base64 + HMAC — a senha ficava LEGÍVEL para quem
obtivesse o token; tokens desse formato não são mais aceitos.

Ciclo de vida:
  - access token curto (OAUTH_ACCESS_TTL, padrão 1 h);
  - refresh token com ROTAÇÃO: cada uso emite um novo e queima o anterior.
    Reuso de um refresh já queimado revoga a sessão inteira (sinal de roubo);
  - teto absoluto da sessão (OAUTH_SESSION_MAX, padrão 30 dias) desde o login
    com senha — depois disso, só logando de novo;
  - revogação (/revoke) derruba a sessão inteira (access + refresh).

O estado de revogação/rotação fica EM MEMÓRIA: um restart o esquece. Isso não
reabre tokens expirados, mas um refresh já usado volta a valer uma vez após o
restart. Para derrubar todas as sessões de uma vez, troque JWT_SECRET.

O POST /login confere a senha com UMA chamada ao /autenticar do SEI (sem retry)
antes de emitir o código: recusa → formulário de novo (400); limite de recusas
por usuário estourado → 429 sem tocar no SEI (limite_login.py); borda/rede/SEI
fora → 503, sem código. Senha errada repetida bloqueia a conta no SEI.

Os clientes OAuth (registro dinâmico) também são selados: o client_id carrega
os metadados do cliente criptografados, então o registro sobrevive a restart
sem banco — sem isso, o refresh falharia após cada deploy.

Variáveis de ambiente:
  JWT_SECRET  — chave mestra, OBRIGATÓRIA em modo HTTP, >= 32 bytes.
                Gere com: python -c "import secrets; print(secrets.token_urlsafe(48))"
  BASE_URL    — URL pública do servidor (ex: https://seipro.ai)
  OAUTH_ALLOWED_REDIRECT_HOSTS — hosts https aceitos como redirect_uri no
                registro de clientes (vírgula; `*.dominio` para sufixo; `*`
                libera qualquer https). Padrão: claude.ai, claude.com e os
                callbacks do Google (Gemini web e Enterprise) e do ChatGPT.
                Loopback http (localhost/127.0.0.1/[::1]) é sempre aceito.
  OAUTH_ACCESS_TTL / OAUTH_REFRESH_TTL / OAUTH_SESSION_MAX — em segundos.
"""

import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time
import zlib
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from starlette.requests import Request
from starlette.responses import HTMLResponse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .seguranca import validar_url_sei

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

ACCESS_TTL = int(os.environ.get("OAUTH_ACCESS_TTL", 3600))  # 1 h
REFRESH_TTL = int(os.environ.get("OAUTH_REFRESH_TTL", 86400 * 14))  # 14 dias
SESSION_MAX = int(os.environ.get("OAUTH_SESSION_MAX", 86400 * 30))  # 30 dias
PENDING_TTL = 600  # tempo para preencher o formulário de login
REUSO_TOLERADO = 30  # s: dois refresh concorrentes do mesmo cliente não derrubam a sessão

_MIN_SEGREDO = 32

# ---------------------------------------------------------------------------
# Criptografia (AES-256-GCM, chave derivada de JWT_SECRET via HKDF)
# ---------------------------------------------------------------------------

_chaves: dict[str, AESGCM] = {}


def _aead() -> AESGCM:
    segredo = os.environ.get("JWT_SECRET", "")
    if len(segredo.encode()) < _MIN_SEGREDO:
        raise RuntimeError(
            f"JWT_SECRET ausente ou curto (mínimo {_MIN_SEGREDO} bytes). Sem ele "
            "qualquer um forja tokens. Gere com: python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\""
        )
    aead = _chaves.get(segredo)
    if aead is None:
        chave = HKDF(
            algorithm=SHA256(), length=32, salt=None, info=b"mcp-seipro/oauth/v2"
        ).derive(segredo.encode())
        aead = _chaves[segredo] = AESGCM(chave)
    return aead


def exigir_segredo() -> None:
    """Falha na subida do servidor se JWT_SECRET não servir."""
    _aead()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _selar(payload: dict, finalidade: str, prefixo: str) -> str:
    nonce = secrets.token_bytes(12)
    bruto = zlib.compress(json.dumps(payload, separators=(",", ":")).encode())
    return prefixo + _b64e(nonce + _aead().encrypt(nonce, bruto, finalidade.encode()))


def _abrir(token: str, finalidade: str, prefixo: str) -> dict | None:
    if not token or not token.startswith(prefixo):
        return None
    try:
        blob = _b64d(token[len(prefixo):])
        bruto = _aead().decrypt(blob[:12], blob[12:], finalidade.encode())
        return json.loads(zlib.decompress(bruto))
    except Exception:
        return None


def _abrir_token(token: str) -> dict | None:
    payload = _abrir(token, "token", "s2.")
    if not payload or payload.get("exp", 0) < time.time():
        return None
    if payload.get("sid") in _sessoes_revogadas:
        return None
    return payload


# ---------------------------------------------------------------------------
# Estado em memória (efêmero — ver docstring do módulo)
# ---------------------------------------------------------------------------

_clients: dict[str, OAuthClientInformationFull] = {}
_auth_codes: dict[str, dict] = {}  # "pending:<id>" / "code:<code>" -> dados
_sessoes_revogadas: dict[str, float] = {}  # sid -> até quando lembrar
_refresh_usados: dict[str, float] = {}  # jti -> instante do uso


def _podar() -> None:
    agora = time.time()
    for chave in [k for k, v in _auth_codes.items() if v.get("expires_at", 0) < agora]:
        _auth_codes.pop(chave, None)
    for sid in [k for k, v in _sessoes_revogadas.items() if v < agora]:
        _sessoes_revogadas.pop(sid, None)
    for jti in [k for k, v in _refresh_usados.items() if v + REFRESH_TTL < agora]:
        _refresh_usados.pop(jti, None)


def _revogar_sessao(sid: str) -> None:
    if sid:
        _sessoes_revogadas[sid] = time.time() + SESSION_MAX
        from .sessao_cache import CACHE

        CACHE.invalidar(sid)  # fecha o SEIClient da sessão (httpx/browser)


# ---------------------------------------------------------------------------
# Redirect URIs permitidas
# ---------------------------------------------------------------------------

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


# Gemini web registra https://oauth-redirect.googleusercontent.com/r/user_bound_
# custom-mcp-<id>-<host do MCP>; Gemini Enterprise usa o fixo
# https://vertexaisearch.cloud.google.com/oauth-redirect. Ambos são hosts de
# callback do próprio Google, como claude.ai é o da Anthropic. ChatGPT usa
# https://chatgpt.com/connector/oauth/{callback_id} (servidor sem `iss` na
# resposta de autorização, que é o nosso caso) ou o estável
# https://chatgpt.com/connector_platform_oauth_redirect.
_HOSTS_REDIRECT_PADRAO = (
    "claude.ai,claude.com,"
    "oauth-redirect.googleusercontent.com,vertexaisearch.cloud.google.com,"
    "chatgpt.com"
)


def _hosts_redirect() -> list[str]:
    bruto = os.environ.get("OAUTH_ALLOWED_REDIRECT_HOSTS", _HOSTS_REDIRECT_PADRAO)
    return [h.strip().lower() for h in bruto.split(",") if h.strip()]


def redirect_permitido(uri: str) -> bool:
    try:
        u = urlparse(str(uri))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if not host:
        return False
    if u.scheme == "http":
        return host in _LOOPBACK  # apps nativos (RFC 8252)
    if u.scheme != "https":
        return False  # javascript:, data:, esquemas customizados...
    for p in _hosts_redirect():
        if p == "*":
            return True
        if p.startswith("*."):
            if host.endswith(p[1:]):
                return True
        elif host == p:
            return True
    return False


def _forma_registro(corpo: bytes) -> dict:
    """Metadados do pedido de registro que explicam uma recusa, sem dado pessoal.

    Das redirect_uris só vai esquema + host: o caminho do Gemini leva o id do
    usuário Google.
    """
    try:
        meta = json.loads(corpo or b"{}")
    except ValueError:
        return {"corpo": "não é JSON", "bytes": len(corpo)}
    if not isinstance(meta, dict):
        return {"corpo": type(meta).__name__}
    destinos = []
    for uri in meta.get("redirect_uris") or []:
        try:
            u = urlparse(str(uri))
            destinos.append(f"{u.scheme}://{u.hostname}")
        except ValueError:
            destinos.append("?")
    return {
        "client_name": str(meta.get("client_name", ""))[:80],
        "redirect_hosts": destinos,
        "grant_types": meta.get("grant_types"),
        "response_types": meta.get("response_types"),
        "token_endpoint_auth_method": meta.get("token_endpoint_auth_method"),
        "scope": meta.get("scope"),
    }


class LogRecusaRegistro:
    """ASGI: loga POR QUE um POST /register foi recusado.

    A validação do SDK (grant_types, auth method, scope) roda antes do provider,
    e o log de acesso só mostra "400". Sem isto, um cliente novo que falha no
    registro (ex.: Gemini) não deixa rastro do campo que causou a recusa.
    """

    _TETO = 16384

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or scope.get("path") != "/register"):
            await self.app(scope, receive, send)
            return
        corpo = bytearray()
        resposta = bytearray()
        status = 0

        async def _receive():
            msg = await receive()
            if msg.get("type") == "http.request" and len(corpo) < self._TETO:
                corpo.extend(msg.get("body", b"")[: self._TETO - len(corpo)])
            return msg

        async def _send(msg):
            nonlocal status
            if msg.get("type") == "http.response.start":
                status = msg.get("status", 0)
            elif msg.get("type") == "http.response.body" and status >= 400 \
                    and len(resposta) < 1024:
                resposta.extend(msg.get("body", b"")[: 1024 - len(resposta)])
            await send(msg)

        await self.app(scope, _receive, _send)
        if status >= 400:
            ua = dict(scope.get("headers") or []).get(b"user-agent", b"")
            logger.warning(
                "OAuth /register recusado (%s): %s | ua=%s | resposta=%s",
                status, json.dumps(_forma_registro(bytes(corpo)), ensure_ascii=False),
                ua.decode("latin-1")[:120], resposta.decode("utf-8", "replace")[:500],
            )


# ---------------------------------------------------------------------------
# OAuth Provider
# ---------------------------------------------------------------------------

_CAMPOS_CLIENTE = (
    "client_secret", "client_id_issued_at", "client_secret_expires_at",
    "redirect_uris", "token_endpoint_auth_method", "grant_types",
    "response_types", "client_name", "scope",
)


class SEIProOAuthProvider:
    """OAuth 2.1 provider que criptografa as credenciais SEI no token."""

    def __init__(self) -> None:
        exigir_segredo()

    # -- Client registration (Dynamic Client Registration) --

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id in _clients:
            return _clients[client_id]
        meta = _abrir(client_id, "client", "c2.")
        if meta is None:
            return None
        try:
            client = OAuthClientInformationFull(client_id=client_id, **meta)
        except Exception:
            return None
        _clients[client_id] = client
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not redirect_permitido(str(uri)):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=(
                        f"redirect_uri não permitida: {uri}. Aceitas: https em "
                        f"{', '.join(_hosts_redirect())} ou http em loopback."
                    ),
                )
        meta = client_info.model_dump(mode="json", include=set(_CAMPOS_CLIENTE))
        # O SDK devolve este mesmo objeto ao cliente: trocar o id aqui é o que
        # faz o client_id retornado ser o selado.
        client_info.client_id = _selar(meta, "client", "c2.")
        _clients[client_info.client_id] = client_info

    # -- Authorization --

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        _podar()
        temp_id = secrets.token_urlsafe(32)
        _auth_codes[f"pending:{temp_id}"] = {
            "client_id": client.client_id,
            "client_name": client.client_name or "",
            "params": params.model_dump(mode="json"),
            "expires_at": time.time() + PENDING_TTL,
        }
        return f"{_BASE_URL}/login?session={temp_id}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        data = _auth_codes.get(f"code:{authorization_code}")
        if not data or data["client_id"] != client.client_id:
            return None
        p = data["params"]
        return AuthorizationCode(
            code=authorization_code,
            scopes=p.get("scopes") or [],
            expires_at=data["expires_at"],
            client_id=data["client_id"],
            code_challenge=p["code_challenge"],
            redirect_uri=p["redirect_uri"],
            redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
            resource=p.get("resource"),
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        data = _auth_codes.pop(f"code:{authorization_code.code}", None)
        if not data:
            raise TokenError(error="invalid_grant", error_description="Code not found")
        return _emitir(
            sei=data["sei_creds"],
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            sid=secrets.token_urlsafe(16),
            auth_time=time.time(),
        )

    # -- Refresh --

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        _podar()
        payload = _abrir_token(refresh_token)
        if not payload or payload.get("typ") != "refresh":
            return None
        if payload.get("client_id") != client.client_id:
            return None
        usado_em = _refresh_usados.get(payload["jti"])
        if usado_em is not None:
            # Refresh já trocado sendo reapresentado: quem o tem não é (só) o
            # cliente legítimo. Fora da janela de concorrência, derruba a sessão.
            if time.time() - usado_em > REUSO_TOLERADO:
                _revogar_sessao(payload.get("sid", ""))
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=payload["client_id"],
            scopes=payload.get("scopes", []),
            expires_at=int(payload.get("exp", 0)),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        payload = _abrir_token(refresh_token.token)
        if not payload or payload.get("typ") != "refresh" or payload["jti"] in _refresh_usados:
            raise TokenError(error="invalid_grant", error_description="Invalid refresh token")
        _refresh_usados[payload["jti"]] = time.time()
        auth_time = float(payload.get("auth_time", 0))
        if auth_time + SESSION_MAX <= time.time():
            raise TokenError(
                error="invalid_grant",
                error_description="Sessão expirou. Reconecte o SEI Pro.",
            )
        return _emitir(
            sei=payload["sei"],
            client_id=client.client_id,
            scopes=scopes or payload.get("scopes", []),
            sid=payload["sid"],
            auth_time=auth_time,
        )

    # -- Token verification --

    async def load_access_token(self, token: str) -> AccessToken | None:
        payload = _abrir_token(token)
        if not payload or payload.get("typ") != "access":
            return None
        return AccessToken(
            token=token,
            client_id=payload.get("client_id", ""),
            scopes=payload.get("scopes", []),
            expires_at=int(payload.get("exp", 0)),
        )

    # -- Revocation: derruba a sessão inteira (access + refresh) --

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        payload = _abrir(token.token, "token", "s2.")
        if payload:
            _revogar_sessao(payload.get("sid", ""))


def _emitir(sei: dict, client_id: str, scopes: list[str], sid: str, auth_time: float) -> OAuthToken:
    agora = time.time()
    base = {
        "sub": sei.get("sei_usuario", ""),
        "sei": sei,
        "client_id": client_id,
        "scopes": scopes,
        "sid": sid,
        "auth_time": auth_time,
        "iat": agora,
    }
    fim_sessao = auth_time + SESSION_MAX
    access_exp = min(agora + ACCESS_TTL, fim_sessao)
    access = _selar({**base, "typ": "access", "jti": secrets.token_urlsafe(12),
                     "exp": access_exp}, "token", "s2.")
    refresh = _selar({**base, "typ": "refresh", "jti": secrets.token_urlsafe(12),
                      "exp": min(agora + REFRESH_TTL, fim_sessao)}, "token", "s2.")
    return OAuthToken(
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        expires_in=max(1, int(access_exp - agora)),
    )


# ---------------------------------------------------------------------------
# Rotas extras (login page + callback)
# ---------------------------------------------------------------------------

# Páginas sem script: CSP bloqueia JS inline/externo (inclusive javascript: em
# href), impede framing (clickjacking) e só deixa o form postar para cá.
_HEADERS_PAGINA = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _e(valor) -> str:
    return html.escape(str(valor or ""), quote=True)


def _preencher(modelo: str, campos: dict[str, str]) -> str:
    """Substitui {marcadores} numa passada só: valor inserido nunca é relido."""
    return re.sub(r"\{(\w+)\}", lambda m: campos.get(m.group(1), m.group(0)), modelo)


def _pagina(corpo: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(corpo, status_code=status, headers=_HEADERS_PAGINA)


def _pagina_erro(msg: str, status: int = 400) -> HTMLResponse:
    return _pagina(
        f"<!DOCTYPE html><html lang=\"pt-BR\"><meta charset=\"utf-8\">"
        f"<title>SEI Pro</title><h1>{_e(msg)}</h1></html>",
        status,
    )


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEI Pro — Login</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #0f172a;
         color: #e2e8f0; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; }
  .card { background: #1e293b; border-radius: 12px; padding: 2rem; width: 100%;
          max-width: 420px; box-shadow: 0 4px 24px rgba(0,0,0,.4); }
  h1 { font-size: 1.5rem; margin-bottom: .5rem; text-align: center; }
  p.sub { color: #94a3b8; font-size: .85rem; text-align: center; margin-bottom: 1rem; }
  .cliente { background: #0f172a; border: 1px solid #334155; border-radius: 6px;
             padding: .6rem .75rem; font-size: .8rem; color: #cbd5e1; margin-bottom: 1.25rem;
             line-height: 1.5; word-break: break-all; }
  .cliente strong { color: #f8fafc; }
  .erro { background: #7f1d1d; color: #fecaca; border-radius: 6px; padding: .6rem .75rem;
          font-size: .85rem; margin-bottom: 1rem; }
  label { display: block; font-size: .85rem; color: #94a3b8; margin-bottom: .25rem; }
  input { width: 100%; padding: .6rem .75rem; border: 1px solid #334155;
          border-radius: 6px; background: #0f172a; color: #e2e8f0; font-size: .95rem;
          margin-bottom: 1rem; }
  input:focus { outline: none; border-color: #3b82f6; }
  button { width: 100%; padding: .7rem; border: none; border-radius: 6px;
           background: #3b82f6; color: #fff; font-size: 1rem; cursor: pointer;
           font-weight: 600; }
  button:hover { background: #2563eb; }
  .help { color: #64748b; font-size: .75rem; text-align: center; margin-top: 1rem; }
  .help a { color: #94a3b8; }
</style>
</head>
<body>
<form class="card" method="POST" action="/login">
  <input type="hidden" name="session" value="{session}">
  <h1>SEI Pro</h1>
  <p class="sub">Conecte sua conta do SEI</p>
  <div class="cliente">Aplicativo solicitante: <strong>{cliente}</strong><br>
    Após o login você será enviado para: <strong>{destino}</strong><br>
    Se você não reconhece esse endereço, feche esta página.</div>
  {erro}
  <label for="sei_url">URL da API do SEI</label>
  <input id="sei_url" name="sei_url" type="url" required value="{sei_url}"
         placeholder="https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2">
  <label for="sei_usuario">Usu&#225;rio</label>
  <input id="sei_usuario" name="sei_usuario" required placeholder="seu.usuario" value="{sei_usuario}">
  <label for="sei_senha">Senha</label>
  <input id="sei_senha" name="sei_senha" type="password" required>
  <label for="sei_orgao">&#211;rg&#227;o (padr&#227;o: 0)</label>
  <input id="sei_orgao" name="sei_orgao" value="{sei_orgao}">
  <label style="display:flex; align-items:center; gap:.5rem; margin-bottom:1rem; cursor:pointer;">
    <input type="checkbox" name="sei_verify_ssl" value="false" style="width:auto; margin:0;">
    <span>Desabilitar verifica&#231;&#227;o SSL (certificado autoassinado)</span>
  </label>
  <button type="submit">Conectar</button>
  <p class="help">Suas credenciais s&#227;o criptografadas (AES-256-GCM) dentro do token de acesso. O servidor n&#227;o as grava em disco nem em banco.</p>
  <p class="help"><a href="https://github.com/SEI-Pro/mcp-seipro/blob/main/PRIVACY.md" target="_blank" rel="noopener noreferrer">Pol&#237;tica de Privacidade</a></p>
</form>
</body>
</html>"""


def _render_login(session: str, pending: dict, erro: str = "", valores: dict | None = None,
                  status: int = 200) -> HTMLResponse:
    valores = valores or {}
    destino = urlparse(pending["params"].get("redirect_uri", "")).netloc or "?"
    return _pagina(_preencher(_LOGIN_HTML, {
        "session": _e(session),
        "cliente": _e(pending.get("client_name") or "(sem nome)"),
        "destino": _e(destino),
        "erro": f'<div class="erro">{_e(erro)}</div>' if erro else "",
        "sei_url": _e(valores.get("sei_url", "")),
        "sei_usuario": _e(valores.get("sei_usuario", "")),
        "sei_orgao": _e(valores.get("sei_orgao", "0")),
    }), status)


_CONFERENCIA_TIMEOUT_S = float(os.environ.get("SEI_LOGIN_CONFERENCIA_TIMEOUT_S", "90"))


def _motivo_indisponivel(e: BaseException) -> str:
    import httpx

    from .sei_client import SEICloudflareBlocked

    if isinstance(e, SEICloudflareBlocked):
        return "a borda (Cloudflare) do órgão barrou a conexão antes de chegar ao SEI"
    if isinstance(e, (asyncio.TimeoutError, httpx.TimeoutException)):
        return f"o SEI não respondeu em {int(_CONFERENCIA_TIMEOUT_S)} s"
    if isinstance(e, httpx.HTTPStatusError):
        return f"o SEI respondeu HTTP {e.response.status_code} ao login"
    if isinstance(e, httpx.TransportError):
        return "não foi possível conectar ao endereço do SEI informado"
    return f"erro inesperado ({type(e).__name__})"


async def _conferir_senha(sei_creds: dict) -> tuple[int, str] | None:
    """Uma requisição ao /autenticar do SEI, sem retry. None = o SEI aceitou.

    Senão, (status HTTP da página, mensagem para o formulário):
      400 — o SEI recusou o login;
      429 — o limite de recusas do usuário está estourado (nada foi ao SEI);
      503 — não deu para conferir agora (borda, rede, SEI fora do ar).
    Nenhum código de acesso é emitido em caso algum de falha.
    """
    from .limite_login import LIMITE, guarda_para
    from .sei_client import SEIClient, SEICredencialRecusada, SEILoginSuspenso

    guarda = guarda_para(sei_creds)
    cliente = None
    try:
        cliente = SEIClient(**sei_creds, permitir_arquivo_local=False, guarda_login=guarda)
        await asyncio.wait_for(cliente.autenticar(), timeout=_CONFERENCIA_TIMEOUT_S)
        return None
    except SEILoginSuspenso as e:
        minutos = max(1, int(-(-e.segundos_restantes // 60)))
        return 429, (
            f"Login suspenso pelo SEI Pro: o SEI recusou {LIMITE.max_falhas} logins "
            f"deste usuário nos últimos {int(LIMITE.janela_s // 60)} min. Para não "
            "bloquear a conta, nenhuma tentativa será enviada ao SEI pelos próximos "
            f"~{minutos} min."
        )
    except SEICredencialRecusada as e:
        restam = max(0, LIMITE.max_falhas - len(LIMITE._podar(guarda.chave)))
        aviso = (
            f" Mais {restam} recusa(s) e o SEI Pro suspende novas tentativas por "
            f"{int(LIMITE.janela_s // 60)} min." if restam else
            f" Novas tentativas ficam suspensas por {int(LIMITE.janela_s // 60)} min."
        )
        return 400, (
            f"O SEI recusou o login: {e.mensagem_sei}. Senha errada repetida "
            "bloqueia a conta no SEI." + aviso
        )
    except Exception as e:  # noqa: BLE001
        # Nunca loga credenciais: só o tipo e o motivo (URL do SEI, status).
        logger.warning("Conferência de senha no /login falhou: %s: %s", type(e).__name__, e)
        return 503, (
            f"Não foi possível conferir a senha agora: {_motivo_indisponivel(e)}. "
            "Nenhum acesso foi liberado. Tente de novo em alguns minutos."
        )
    finally:
        if cliente is not None:
            try:
                await cliente.close()
            except Exception:  # noqa: BLE001
                pass


def _pending_valido(session_id: str) -> dict | None:
    pending = _auth_codes.get(f"pending:{session_id}")
    if not pending or pending.get("expires_at", 0) < time.time():
        return None
    return pending


async def login_page(request: Request) -> HTMLResponse:
    """GET /login — renderiza formulário de credenciais SEI."""
    session = request.query_params.get("session", "")
    pending = _pending_valido(session)
    if not pending:
        return _pagina_erro("Sessão de login expirada ou inválida. Reconecte pelo aplicativo.")
    return _render_login(session, pending)


async def login_submit(request: Request):
    """POST /login — recebe credenciais, gera auth code, redireciona de volta ao cliente."""
    form = await request.form()
    session_id = str(form.get("session", ""))
    pending = _pending_valido(session_id)
    if not pending:
        return _pagina_erro("Sessão expirada. Tente novamente.")

    params = pending["params"]
    if not redirect_permitido(params.get("redirect_uri", "")):
        _auth_codes.pop(f"pending:{session_id}", None)
        return _pagina_erro("Endereço de retorno do aplicativo não é permitido.")

    # Checkbox marcado envia "false"; desmarcado não envia nada (= "true")
    verify_ssl = "false" if form.get("sei_verify_ssl") == "false" else "true"
    sei_creds = {
        "sei_url": str(form.get("sei_url", "")).strip(),
        "sei_usuario": str(form.get("sei_usuario", "")).strip(),
        "sei_senha": str(form.get("sei_senha", "")),
        "sei_orgao": str(form.get("sei_orgao", "0")).strip() or "0",
        "sei_verify_ssl": verify_ssl,
    }

    erro = await validar_url_sei(sei_creds["sei_url"])
    if erro:
        # Mantém a sessão pendente: o usuário corrige a URL e reenvia.
        return _render_login(session_id, pending, erro, sei_creds, status=400)

    # Um POST por vez por sessão pendente: duplo clique não vira dois logins.
    if pending.get("conferindo"):
        return _render_login(session_id, pending,
                             "A senha ainda está sendo conferida com o SEI. Aguarde.",
                             sei_creds, status=409)
    pending["conferindo"] = True
    try:
        falha = await _conferir_senha(sei_creds)
    finally:
        pending.pop("conferindo", None)
    if falha:
        status, msg = falha
        # Mantém o pending (pode corrigir e reenviar). A senha nunca volta ao
        # formulário: _render_login não tem campo para ela.
        return _render_login(session_id, pending, msg, sei_creds, status=status)

    _auth_codes.pop(f"pending:{session_id}", None)
    code = secrets.token_urlsafe(32)
    _auth_codes[f"code:{code}"] = {
        "client_id": pending["client_id"],
        "params": params,
        "sei_creds": sei_creds,
        "expires_at": time.time() + 600,
    }

    redirect_uri = construct_redirect_uri(
        params["redirect_uri"],
        code=code,
        state=params.get("state"),
    )

    return _pagina(_preencher(_SUCCESS_HTML, {
        "redirect_uri": _e(redirect_uri),
        "destino": _e(urlparse(str(redirect_uri)).netloc),
        "usuario": _e(sei_creds["sei_usuario"]),
    }))


_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEI Pro &#8212; Configurado!</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #0f172a;
         color: #e2e8f0; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; }
  .card { background: #1e293b; border-radius: 12px; padding: 2rem; width: 100%;
          max-width: 460px; box-shadow: 0 4px 24px rgba(0,0,0,.4); text-align: center; }
  .check { font-size: 3rem; margin-bottom: .75rem; }
  h1 { font-size: 1.4rem; margin-bottom: .25rem; }
  .user { color: #3b82f6; font-weight: 600; }
  p { color: #94a3b8; font-size: .9rem; line-height: 1.5; margin-top: .75rem; }
  .steps { text-align: left; background: #0f172a; border-radius: 8px; padding: 1rem 1.25rem;
           margin-top: 1rem; }
  .steps li { color: #cbd5e1; font-size: .85rem; margin-bottom: .5rem; list-style: none; }
  .steps li::before { content: attr(data-n); display: inline-flex; align-items: center;
           justify-content: center; width: 1.4rem; height: 1.4rem; border-radius: 50%;
           background: #3b82f6; color: #fff; font-size: .7rem; font-weight: 700;
           margin-right: .5rem; }
  a.btn { display: inline-block; margin-top: 1.25rem; padding: .7rem 2rem; border-radius: 6px;
          background: #3b82f6; color: #fff; text-decoration: none; font-weight: 600;
          font-size: 1rem; }
  a.btn:hover { background: #2563eb; }
  .help { color: #64748b; font-size: .75rem; margin-top: 1rem; }
</style>
</head>
<body>
<div class="card">
  <div class="check">&#10003;</div>
  <h1>SEI Pro configurado!</h1>
  <p>Login de <span class="user">{usuario}</span> conferido com o SEI.</p>
  <ul class="steps">
    <li data-n="1">Clique em <strong>Continuar</strong> para voltar a <strong>{destino}</strong></li>
    <li data-n="2">A sess&#227;o com o SEI &#233; aberta na sua primeira pergunta e reaproveitada nas seguintes</li>
    <li data-n="3">Comece com: <em>&#8220;Liste as unidades do SEI&#8221;</em></li>
  </ul>
  <a class="btn" href="{redirect_uri}">Continuar</a>
  <p class="help">Suas credenciais s&#227;o criptografadas (AES-256-GCM) dentro do token de acesso. O servidor n&#227;o as grava em disco nem em banco.</p>
</div>
</body>
</html>"""


def get_sei_credentials_from_token(token: str) -> dict | None:
    """Extrai credenciais SEI de um access token. Usado pelo server.py."""
    sessao = get_sessao_from_token(token)
    return sessao[1] if sessao else None


def get_sessao_from_token(token: str) -> tuple[str, dict] | None:
    """(chave da sessão OAuth, credenciais SEI) de um access token válido.

    A chave é o `sid` — o mesmo em todos os tokens da sessão (o refresh o
    preserva) e distinto entre sessões do mesmo usuário. Token sem `sid` (não
    emitido por esta versão) cai no hash do próprio token: nunca no usuário.
    """
    payload = _abrir_token(token)
    if not payload or payload.get("typ") != "access" or not payload.get("sei"):
        return None
    sid = payload.get("sid")
    chave = sid if sid else "tok:" + hashlib.sha256(token.encode()).hexdigest()
    return chave, payload["sei"]
