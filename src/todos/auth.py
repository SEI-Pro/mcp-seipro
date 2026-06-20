"""OAuth 2.1 provider para todos (MCP SEI).

As credenciais do SEI (url, usuario, orgao) são informadas pelo
usuário na tela de login OAuth. O servidor encripta essas credenciais
dentro do access token (JWT) e nunca as armazena. A cada request MCP,
o servidor descriptografa o token para obter as credenciais.

A senha do SEI é obtida em tempo de execução pela variável de ambiente
SEI_SENHA — ela não é incluída no payload do token para evitar que
qualquer pessoa com o token consiga ler a credencial em claro.

Variáveis de ambiente necessárias:
  JWT_SECRET  — chave para assinar/encriptar os tokens (obrigatória em modo HTTP)
  SEI_SENHA   — senha do SEI (nunca incluída no token)
  BASE_URL    — URL pública do servidor (ex: https://seipro.ai)
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from html import escape as _html_escape
from typing import cast

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse

from todos.catalog_cache import get_catalog_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Crypto helpers (HMAC-SHA256 para assinatura, sem dep externa)
# ---------------------------------------------------------------------------

_JWT_SECRET_MIN_LEN = 32

_JWT_SECRET = os.environ.get("JWT_SECRET", "")

_JWT_CONFIG_ERR = (
    "JWT_SECRET não configurado ou muito curto — "
    "defina JWT_SECRET com pelo menos 32 caracteres antes de iniciar o servidor HTTP."
)
TOKEN_TTL = 86400 * 30  # 30 dias
_JWT_PARTS = 2  # JWT tokens have exactly 2 parts: payload.signature
_BEARER_SCHEME = "Bearer"  # RFC 6750 §6.1.1 token type string


def validate_jwt_secret() -> None:
    """Raise RuntimeError if JWT_SECRET is absent or shorter than the minimum.

    Call this at HTTP server startup (run_remote) for fail-fast behaviour.
    Not called at import time so the module stays importable in test environments.
    """
    if len(_JWT_SECRET) < _JWT_SECRET_MIN_LEN:
        raise RuntimeError(_JWT_CONFIG_ERR)


# ---------------------------------------------------------------------------
# Auth code persistence (§31.3 — persiste no SQLite para sobreviver restarts)
# ---------------------------------------------------------------------------

_AUTH_CODE_TTL = 300  # segundos — tempo de vida dos auth codes


async def _store_auth_code(code: str, data: dict) -> None:
    """Store an auth code in SQLite with embedded TTL.

    Write-through: grava em _auth_codes (memória) e no CatalogCache (disco).
    """
    entry = {**data, "_expires": time.time() + _AUTH_CODE_TTL}
    _auth_codes[code] = entry
    cache = get_catalog_cache()
    await cache.set({"module": "auth"}, f"code:{code}", entry)


async def _load_auth_code(code: str) -> dict | None:
    """Lê um auth code da memória (hit) ou do SQLite (miss após restart).

    Retorna None se não encontrado ou expirado; remove a entrada expirada do SQLite.
    """
    # Memória primeiro (hit frequente)
    entry = _auth_codes.get(code)
    if entry is None:
        # Miss — tenta disco (sobrevivência após restart)
        cache = get_catalog_cache()
        entry = cast("dict | None", await cache.get({"module": "auth"}, f"code:{code}"))
    if entry is None:
        return None
    if time.time() > entry.get("_expires", 0):
        # Expirado — limpa disco
        cache = get_catalog_cache()
        await cache.set({"module": "auth"}, f"code:{code}", None)
        return None
    return {k: v for k, v in entry.items() if k != "_expires"}


async def _delete_auth_code(code: str) -> None:
    """Remove um auth code da memória e do SQLite (uso único)."""
    _auth_codes.pop(code, None)
    cache = get_catalog_cache()
    await cache.delete({"module": "auth"}, f"code:{code}")


def _sign(payload: dict) -> str:
    """Cria um token JWT-like: base64(payload).base64(signature)."""
    if len(_JWT_SECRET) < _JWT_SECRET_MIN_LEN:
        raise RuntimeError(_JWT_CONFIG_ERR)
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_JWT_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify(token: str) -> dict | None:
    """Verifica e decodifica um token. Retorna None se invalido."""
    if len(_JWT_SECRET) < _JWT_SECRET_MIN_LEN:
        raise RuntimeError(_JWT_CONFIG_ERR)
    parts = token.split(".")
    if len(parts) != _JWT_PARTS:
        return None
    raw, sig = parts
    expected = hmac.new(_JWT_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError):
        return None
    if "exp" not in payload:
        logger.warning("Token sem campo 'exp' — tratado como expirado.")
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ---------------------------------------------------------------------------
# Storage in-memory (auth codes e clients são efêmeros)
# ---------------------------------------------------------------------------

_clients: dict[str, OAuthClientInformationFull] = {}
_auth_codes: dict[str, dict] = {}  # code -> {params, sei_creds, ..., _expires}

# §31.4 — Lock para garantir atomicidade do check+pop dos auth codes
_auth_code_lock: asyncio.Lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# OAuth Provider
# ---------------------------------------------------------------------------


class SEIProOAuthProvider(OAuthProvider):
    """OAuth 2.1 provider que encripta credenciais SEI no access token."""

    def __init__(self, base_url: str) -> None:
        """Configura os endpoints OAuth para a URL pública do servidor."""
        super().__init__(
            base_url=base_url,
            resource_base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.public_base_url = base_url.rstrip("/")

    # -- Client registration (Dynamic Client Registration) --

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Look up a registered OAuth client by ID."""
        return _clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Persist a dynamically-registered OAuth client in memory."""
        if client_info.client_id:
            _clients[client_info.client_id] = client_info

    # -- Authorization --

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Start the authorization flow; return the URL of the login page."""
        # Salva os params e redireciona para a página de login
        temp_id = secrets.token_urlsafe(32)
        await _store_auth_code(
            f"pending:{temp_id}",
            {
                "client_id": client.client_id,
                "params": params.model_dump(mode="json"),
            },
        )
        return f"{self.public_base_url}/login?session={temp_id}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Retrieve a pending authorization code; returns None if not found or client mismatch."""
        data = await _load_auth_code(f"code:{authorization_code}")
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
        """Consume an auth code and return a signed access + refresh token pair."""
        # §31.4 — Lock atômico: impede que dois POSTs simultâneos consumam o mesmo code
        async with _auth_code_lock:
            data = _auth_codes.pop(f"code:{authorization_code.code}", None)
            if data is None:
                # Miss em memória — tenta disco (restart entre emissão e troca)
                data = await _load_auth_code(f"code:{authorization_code.code}")
            if data is None:
                raise TokenError(error="invalid_grant", error_description="Code not found")
            # Remove do disco para evitar replay
            await _delete_auth_code(f"code:{authorization_code.code}")

        sei_creds = data["sei_creds"]
        now = time.time()

        # §31.1 — sei_senha NÃO é incluída no payload do token.
        # A senha é obtida em runtime pela variável de ambiente SEI_SENHA.
        # Somente campos não-secretos (usuario, orgao, urls, ssl) vão no token.
        sei_public = {k: v for k, v in sei_creds.items() if k != "sei_senha"}

        access_payload = {
            "sub": sei_creds["sei_usuario"],
            "sei": sei_public,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "exp": now + TOKEN_TTL,
            "iat": now,
            "type": "access",
        }
        access_token = _sign(access_payload)

        refresh_payload = {
            "sub": sei_creds["sei_usuario"],
            "sei": sei_public,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "exp": now + TOKEN_TTL * 2,
            "iat": now,
            "type": "refresh",
        }
        refresh_token = _sign(refresh_payload)

        return OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=_BEARER_SCHEME,
            expires_in=int(TOKEN_TTL),
        )

    # -- Refresh --

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Verify and decode a refresh token; returns None if invalid or expired."""
        payload = _verify(refresh_token)
        if not payload or payload.get("type") != "refresh":
            return None
        if payload.get("client_id") != client.client_id:
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
        """Issue a new access + refresh token pair from a valid refresh token."""
        payload = _verify(refresh_token.token)
        if not payload:
            raise TokenError(error="invalid_grant", error_description="Invalid refresh token")

        sei_creds = payload["sei"]
        now = time.time()

        # §31.1 — sei_senha já não está no payload (nunca foi incluída desde a correção).
        # sei_creds contém apenas campos públicos: usuario, orgao, urls, ssl.
        access_payload = {
            "sub": sei_creds["sei_usuario"],
            "sei": sei_creds,
            "client_id": client.client_id,
            "scopes": scopes or payload.get("scopes", []),
            "exp": now + TOKEN_TTL,
            "iat": now,
            "type": "access",
        }
        new_access = _sign(access_payload)

        refresh_payload = {
            "sub": sei_creds["sei_usuario"],
            "sei": sei_creds,
            "client_id": client.client_id,
            "scopes": scopes or payload.get("scopes", []),
            "exp": now + TOKEN_TTL * 2,
            "iat": now,
            "type": "refresh",
        }
        new_refresh = _sign(refresh_payload)

        return OAuthToken(
            access_token=new_access,
            refresh_token=new_refresh,
            token_type=_BEARER_SCHEME,
            expires_in=int(TOKEN_TTL),
        )

    # -- Token verification --

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Verify and decode an access token; returns None if invalid or expired."""
        payload = _verify(token)
        if not payload or payload.get("type") != "access":
            return None
        return AccessToken(
            token=token,
            client_id=payload.get("client_id", ""),
            scopes=payload.get("scopes", []),
            expires_at=int(payload.get("exp", 0)),
        )

    # -- Revocation (no-op, tokens são stateless) --

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """No-op: tokens are stateless and expire naturally."""


# ---------------------------------------------------------------------------
# Rotas extras (login page + callback)
# ---------------------------------------------------------------------------

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>todos — Login</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #0f172a;
         color: #e2e8f0; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; }
  .card { background: #1e293b; border-radius: 12px; padding: 2rem; width: 100%;
          max-width: 420px; box-shadow: 0 4px 24px rgba(0,0,0,.4); }
  h1 { font-size: 1.5rem; margin-bottom: .5rem; text-align: center; }
  p.sub { color: #94a3b8; font-size: .85rem; text-align: center; margin-bottom: 1.5rem; }
  label { display: block; font-size: .85rem; color: #94a3b8; margin-bottom: .25rem; }
  input { width: 100%; padding: .6rem .75rem; border: 1px solid #334155;
          border-radius: 6px; background: #0f172a; color: #e2e8f0; font-size: .95rem;
          margin-bottom: 1rem; }
  input:focus { outline: none; border-color: #3b82f6; }
  button { width: 100%; padding: .7rem; border: none; border-radius: 6px;
           background: #3b82f6; color: #fff; font-size: 1rem; cursor: pointer;
           font-weight: 600; }
  button:hover { background: #2563eb; }
  .logo { text-align: center; margin-bottom: 1rem; }
  .logo img { width: 48px; height: 48px; border-radius: 8px; }
  .help { color: #64748b; font-size: .75rem; text-align: center; margin-top: 1rem; }
</style>
</head>
<body>
<form class="card" method="POST" action="/login">
  <input type="hidden" name="session" value="{session}">
  <h1>todos</h1>
  <p class="sub">Conecte sua conta do SEI ao Claude</p>
  <label for="sei_url">URL da API do SEI (opcional &#8212; deixe em branco se sem mod-wssei)</label>
  <input id="sei_url" name="sei_url" type="url"
         placeholder="https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2">
  <label for="sei_web_url">URL base do SEI (obrigat&#243;ria se a URL da API ficar em branco)</label>
  <input id="sei_web_url" name="sei_web_url" type="url"
         placeholder="https://sei.orgao.gov.br">
  <label for="sei_usuario">Usu&#225;rio</label>
  <input id="sei_usuario" name="sei_usuario" required placeholder="seu.usuario">
  <label for="sei_senha">Senha</label>
  <input id="sei_senha" name="sei_senha" type="password" required>
  <label for="sei_orgao">&#211;rg&#227;o (padr&#227;o: 0)</label>
  <input id="sei_orgao" name="sei_orgao" value="0">
  <label style="display:flex; align-items:center; gap:.5rem; margin-bottom:1rem; cursor:pointer;">
    <input type="checkbox" name="sei_verify_ssl" value="false" style="width:auto; margin:0;">
    <span>Desabilitar verifica&#231;&#227;o SSL (certificado autoassinado)</span>
  </label>
  <button type="submit">Conectar</button>
  <p class="help">Suas credenciais s&#227;o encriptadas no token e nunca armazenadas no servidor.</p>
</form>
</body>
</html>"""


async def login_page(request: Request) -> HTMLResponse:
    """GET /login — renderiza formulário de credenciais SEI."""
    session = request.query_params.get("session", "")
    return HTMLResponse(_LOGIN_HTML.replace("{session}", session))


async def login_submit(request: Request) -> HTMLResponse:
    """POST /login — recebe credenciais, gera auth code, redireciona de volta ao Claude."""
    form = await request.form()
    session_id = str(form.get("session", ""))
    pending = await _load_auth_code(f"pending:{session_id}")
    if not pending:
        return HTMLResponse("<h1>Sessao expirada. Tente novamente.</h1>", status_code=400)

    # Checkbox marcado envia "false"; desmarcado não envia nada (= "true")
    verify_ssl = "false" if form.get("sei_verify_ssl") == "false" else "true"
    sei_url = str(form.get("sei_url", "")).strip()
    sei_web_url = str(form.get("sei_web_url", "")).strip()
    if not sei_url and not sei_web_url:
        # Não consome a sessão pendente: o usuário pode voltar e corrigir
        return HTMLResponse(
            "<h1>Informe a URL da API do SEI ou a URL base do SEI (web).</h1>",
            status_code=400,
        )

    # Validação ok — consome a sessão pendente (uso único)
    await _delete_auth_code(f"pending:{session_id}")

    # §31.1 — sei_senha não vai para o token; é lida de SEI_SENHA em runtime.
    # Armazenamos apenas no auth code (vida útil de 5 min, no SQLite) para que
    # exchange_authorization_code possa repassar ao SEIClient na criação da sessão.
    sei_creds = {
        "sei_url": sei_url,
        "sei_web_url": sei_web_url,
        "sei_usuario": str(form.get("sei_usuario", "")),
        "sei_senha": str(form.get("sei_senha", "")),
        "sei_orgao": str(form.get("sei_orgao", "0")),
        "sei_verify_ssl": verify_ssl,
    }

    code = secrets.token_urlsafe(32)
    params = pending["params"]
    await _store_auth_code(
        f"code:{code}",
        {
            "client_id": pending["client_id"],
            "params": params,
            "sei_creds": sei_creds,
            "expires_at": time.time() + _AUTH_CODE_TTL,
        },
    )

    redirect_uri = construct_redirect_uri(
        params["redirect_uri"],
        code=code,
        state=params.get("state"),
    )

    usuario = sei_creds["sei_usuario"]
    page = _SUCCESS_HTML.replace("{redirect_uri}", str(redirect_uri)).replace(
        "{usuario}", _html_escape(usuario)
    )
    return HTMLResponse(page)


_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>todos &#8212; Configurado!</title>
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
  .back { color: #94a3b8; text-decoration: none; font-size: .85rem;
          display: inline-flex; align-items: center; gap: .3rem; margin-bottom: 1rem; }
  .back:hover { color: #e2e8f0; }
  .help { color: #64748b; font-size: .75rem; margin-top: 1rem; }
</style>
</head>
<body>
<div class="card">
  <a class="back" href="javascript:history.back()">&larr; Voltar</a>
  <div class="check">&#10003;</div>
  <h1>todos configurado!</h1>
  <p>Credenciais de <span class="user">{usuario}</span> salvas com seguran&#231;a.</p>
  <ul class="steps">
    <li data-n="1">Clique em <strong>Continuar</strong> para voltar ao Claude</li>
    <li data-n="2">O Claude vai se conectar ao SEI quando voc&#234; fizer sua primeira pergunta</li>
    <li data-n="3">Comece com: <em>&#8220;Liste as unidades do SEI&#8221;</em></li>
  </ul>
  <a class="btn" href="{redirect_uri}">Continuar para o Claude</a>
  <p class="help">Suas credenciais s&#227;o encriptadas no token e n&#227;o ficam armazenadas no servidor.</p>
</div>
</body>
</html>"""


def get_sei_credentials_from_token(token: str) -> dict | None:
    """Extrai credenciais SEI de um access token. Usado pelo server.py.

    Design: servidor pessoal (single-user). A senha do SEI não é armazenada
    no token — ela é lida da variável de ambiente SEI_SENHA em cada request.
    Em deployments multi-usuário esta abordagem não funciona: cada usuário
    precisaria do seu próprio processo com SEI_SENHA configurado individualmente.

    §31.1 — O token não contém sei_senha. A senha é injetada aqui a partir
    da variável de ambiente SEI_SENHA para que SEIClient/SEIWebClient possam
    autenticar sem que a credencial trafegue no token.
    """
    payload = _verify(token)
    if not payload or payload.get("type") != "access":
        return None
    sei = payload.get("sei")
    if sei is None:
        return None
    # Injeta sei_senha a partir do ambiente — nunca do token
    senha = os.environ.get("SEI_SENHA", "")
    if not senha:
        _err = (
            "SEI_SENHA não configurado no servidor. "
            "Defina a variável de ambiente SEI_SENHA com a senha do SEI antes de iniciar o servidor."
        )
        raise RuntimeError(_err)
    return {**sei, "sei_senha": senha}
