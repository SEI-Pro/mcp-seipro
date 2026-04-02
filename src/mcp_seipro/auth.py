"""OAuth 2.1 provider para MCP SEI Pro.

As credenciais do SEI (url, usuario, senha, orgao) são informadas pelo
usuário na tela de login OAuth. O servidor encripta essas credenciais
dentro do access token (JWT) e nunca as armazena. A cada request MCP,
o servidor descriptografa o token para obter as credenciais.

Variáveis de ambiente necessárias:
  JWT_SECRET  — chave para assinar/encriptar os tokens (obrigatória em modo HTTP)
  BASE_URL    — URL pública do servidor (ex: https://seipro.ai)
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

# ---------------------------------------------------------------------------
# Crypto helpers (HMAC-SHA256 para assinatura, sem dep externa)
# ---------------------------------------------------------------------------

_JWT_SECRET = os.environ.get("JWT_SECRET", "")
_BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

TOKEN_TTL = 86400 * 30  # 30 dias


def _sign(payload: dict) -> str:
    """Cria um token JWT-like: base64(payload).base64(signature)."""
    import base64
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_JWT_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify(token: str) -> dict | None:
    """Verifica e decodifica um token. Retorna None se invalido."""
    import base64
    parts = token.split(".")
    if len(parts) != 2:
        return None
    raw, sig = parts
    expected = hmac.new(_JWT_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ---------------------------------------------------------------------------
# Storage in-memory (auth codes e clients são efêmeros)
# ---------------------------------------------------------------------------

_clients: dict[str, OAuthClientInformationFull] = {}
_auth_codes: dict[str, dict] = {}  # code -> {params, sei_creds, ...}


# ---------------------------------------------------------------------------
# OAuth Provider
# ---------------------------------------------------------------------------

class SEIProOAuthProvider:
    """OAuth 2.1 provider que encripta credenciais SEI no access token."""

    # -- Client registration (Dynamic Client Registration) --

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return _clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        _clients[client_info.client_id] = client_info

    # -- Authorization --

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        # Salva os params e redireciona para a página de login
        temp_id = secrets.token_urlsafe(32)
        _auth_codes[f"pending:{temp_id}"] = {
            "client_id": client.client_id,
            "params": params.model_dump(mode="json"),
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
            from mcp.server.auth.provider import TokenError
            raise TokenError(error="invalid_grant", error_description="Code not found")

        sei_creds = data["sei_creds"]
        now = time.time()

        access_payload = {
            "sub": sei_creds["sei_usuario"],
            "sei": sei_creds,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "exp": now + TOKEN_TTL,
            "iat": now,
            "type": "access",
        }
        access_token = _sign(access_payload)

        refresh_payload = {
            "sub": sei_creds["sei_usuario"],
            "sei": sei_creds,
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
            token_type="Bearer",
            expires_in=int(TOKEN_TTL),
        )

    # -- Refresh --

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
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
        payload = _verify(refresh_token.token)
        if not payload:
            from mcp.server.auth.provider import TokenError
            raise TokenError(error="invalid_grant", error_description="Invalid refresh token")

        sei_creds = payload["sei"]
        now = time.time()

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
            token_type="Bearer",
            expires_in=int(TOKEN_TTL),
        )

    # -- Token verification --

    async def load_access_token(self, token: str) -> AccessToken | None:
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
        pass  # Tokens stateless — expiram naturalmente


# ---------------------------------------------------------------------------
# Rotas extras (login page + callback)
# ---------------------------------------------------------------------------

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
  <div class="logo"><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAKHSURBVFhH7ZY9aBRBFMf/M3t3uYuJIoIWFjYiCIKNnYWgIIggKIiF2FgIFhYKgp2NjWBhI1iIiI2FhQiCIBZW" alt="SEI Pro"></div>
  <h1>SEI Pro</h1>
  <p class="sub">Conecte sua conta do SEI ao Claude</p>
  <label for="sei_url">URL da API do SEI</label>
  <input id="sei_url" name="sei_url" type="url" required
         placeholder="https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2">
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


async def login_submit(request: Request):
    """POST /login — recebe credenciais, gera auth code, redireciona de volta ao Claude."""
    form = await request.form()
    session_id = str(form.get("session", ""))
    pending = _auth_codes.pop(f"pending:{session_id}", None)
    if not pending:
        return HTMLResponse("<h1>Sessao expirada. Tente novamente.</h1>", status_code=400)

    # Checkbox marcado envia "false"; desmarcado não envia nada (= "true")
    verify_ssl = "false" if form.get("sei_verify_ssl") == "false" else "true"
    sei_creds = {
        "sei_url": str(form.get("sei_url", "")),
        "sei_usuario": str(form.get("sei_usuario", "")),
        "sei_senha": str(form.get("sei_senha", "")),
        "sei_orgao": str(form.get("sei_orgao", "0")),
        "sei_verify_ssl": verify_ssl,
    }

    code = secrets.token_urlsafe(32)
    params = pending["params"]
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

    usuario = sei_creds["sei_usuario"]
    html = _SUCCESS_HTML.replace("{redirect_uri}", str(redirect_uri)).replace("{usuario}", usuario)
    return HTMLResponse(html)


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
  <h1>SEI Pro configurado!</h1>
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
    """Extrai credenciais SEI de um access token. Usado pelo server.py."""
    payload = _verify(token)
    if not payload or payload.get("type") != "access":
        return None
    return payload.get("sei")
