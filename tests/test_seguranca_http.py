"""Testes das defesas do modo HTTP (deploy remoto com OAuth).

Cada teste corresponde a um vetor concreto de ataque: leitura de arquivo do
servidor via arquivo_path, token forjável sem JWT_SECRET, senha legível no
token, XSS/phishing na tela de login, SSRF e vazamento dos segredos do operador.
"""
import asyncio
import base64
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("JWT_SECRET", "x" * 48)

from mcp.server.auth.provider import (  # noqa: E402
    AuthorizationParams,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import mcp_seipro.auth as auth  # noqa: E402
import mcp_seipro.seguranca as seg  # noqa: E402
from mcp_seipro.sei_client import SEIClient  # noqa: E402

CREDS = {
    "sei_url": "https://sei.exemplo.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2",
    "sei_usuario": "fulano",
    "sei_senha": "SenhaSuperSecreta123",
    "sei_orgao": "0",
    "sei_verify_ssl": "true",
}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _limpa_estado(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 48)
    for d in (auth._clients, auth._auth_codes, auth._sessoes_revogadas, auth._refresh_usados):
        d.clear()
    yield


def _cliente(redirect="https://claude.ai/api/mcp/auth_callback", nome="Claude"):
    return OAuthClientInformationFull(
        client_id="tmp", client_secret="s3cr3t", redirect_uris=[redirect],
        client_name=nome, token_endpoint_auth_method="client_secret_post",
    )


def _registrar(provider, **kw):
    info = _cliente(**kw)
    _run(provider.register_client(info))
    return info


# ---------------------------------------------------------------- JWT_SECRET

@pytest.mark.parametrize("segredo", ["", "curto"])
def test_sem_segredo_forte_o_provider_nao_sobe(monkeypatch, segredo):
    monkeypatch.setenv("JWT_SECRET", segredo)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.SEIProOAuthProvider()


# ---------------------------------------------------------------- tokens

def _tokens(provider, client):
    auth._auth_codes["code:abc"] = {
        "client_id": client.client_id, "params": {}, "sei_creds": dict(CREDS),
        "expires_at": time.time() + 60,
    }
    code = type("C", (), {"code": "abc", "scopes": []})()
    return _run(provider.exchange_authorization_code(client, code))


def test_senha_nao_fica_legivel_no_token():
    p = auth.SEIProOAuthProvider()
    c = _registrar(p)
    tok = _tokens(p, c)
    for t in (tok.access_token, tok.refresh_token):
        bruto = base64.urlsafe_b64decode(t[3:] + "=" * (-len(t[3:]) % 4))
        assert b"SenhaSuperSecreta123" not in bruto
        assert b"fulano" not in bruto
    assert auth.get_sei_credentials_from_token(tok.access_token)["sei_senha"] == CREDS["sei_senha"]
    assert tok.expires_in <= auth.ACCESS_TTL


def test_token_adulterado_ou_de_outra_chave_e_recusado(monkeypatch):
    p = auth.SEIProOAuthProvider()
    c = _registrar(p)
    tok = _tokens(p, c).access_token
    adulterado = tok[:-2] + ("A" if tok[-2] != "A" else "B") + tok[-1]
    assert _run(p.load_access_token(adulterado)) is None
    monkeypatch.setenv("JWT_SECRET", "y" * 48)
    assert _run(p.load_access_token(tok)) is None


def test_formato_antigo_hmac_nao_e_aceito():
    raw = base64.urlsafe_b64encode(json.dumps(
        {"type": "access", "sei": CREDS, "exp": time.time() + 999}).encode()).decode()
    assert _run(auth.SEIProOAuthProvider().load_access_token(raw + ".deadbeef")) is None


def test_refresh_rotaciona_e_reuso_derruba_a_sessao(monkeypatch):
    p = auth.SEIProOAuthProvider()
    c = _registrar(p)
    tok = _tokens(p, c)
    rt = _run(p.load_refresh_token(c, tok.refresh_token))
    novo = _run(p.exchange_refresh_token(c, rt, []))
    # o refresh antigo não serve mais
    assert _run(p.load_refresh_token(c, tok.refresh_token)) is None
    with pytest.raises(TokenError):
        _run(p.exchange_refresh_token(c, rt, []))
    # reuso fora da janela de concorrência revoga a sessão inteira
    monkeypatch.setattr(auth, "REUSO_TOLERADO", -1)
    assert _run(p.load_refresh_token(c, tok.refresh_token)) is None
    assert _run(p.load_access_token(novo.access_token)) is None
    assert _run(p.load_refresh_token(c, novo.refresh_token)) is None


def test_revogar_derruba_access_e_refresh():
    p = auth.SEIProOAuthProvider()
    c = _registrar(p)
    tok = _tokens(p, c)
    at = _run(p.load_access_token(tok.access_token))
    _run(p.revoke_token(at))
    assert _run(p.load_access_token(tok.access_token)) is None
    assert _run(p.load_refresh_token(c, tok.refresh_token)) is None
    assert auth.get_sei_credentials_from_token(tok.access_token) is None


def test_teto_absoluto_da_sessao(monkeypatch):
    p = auth.SEIProOAuthProvider()
    c = _registrar(p)
    tok = _tokens(p, c)
    rt = _run(p.load_refresh_token(c, tok.refresh_token))
    monkeypatch.setattr(auth, "SESSION_MAX", 0)
    with pytest.raises(TokenError) as e:
        _run(p.exchange_refresh_token(c, rt, []))
    assert "expirou" in e.value.error_description


# ---------------------------------------------------------------- registro de clientes

@pytest.mark.parametrize("uri", [
    "javascript://claude.ai/%0aalert(1)",
    "https://atacante.example/cb",
    "http://claude.ai/cb",
    "data:text/html,oi",
])
def test_registro_recusa_redirect_nao_permitido(uri):
    with pytest.raises(RegistrationError):
        _registrar(auth.SEIProOAuthProvider(), redirect=uri)


@pytest.mark.parametrize("uri", [
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost:33418/callback",
    "http://127.0.0.1:9000/cb",
])
def test_registro_aceita_redirect_permitido(uri):
    _registrar(auth.SEIProOAuthProvider(), redirect=uri)


def test_allowlist_de_redirect_configuravel(monkeypatch):
    monkeypatch.setenv("OAUTH_ALLOWED_REDIRECT_HOSTS", "*.cliente.com")
    assert auth.redirect_permitido("https://app.cliente.com/cb")
    assert not auth.redirect_permitido("https://claude.ai/cb")


def test_client_id_selado_sobrevive_a_restart():
    p = auth.SEIProOAuthProvider()
    c = _registrar(p)
    assert c.client_id.startswith("c2.")
    auth._clients.clear()  # simula restart
    recuperado = _run(p.get_client(c.client_id))
    assert recuperado.client_secret == "s3cr3t"
    assert [str(u) for u in recuperado.redirect_uris] == ["https://claude.ai/api/mcp/auth_callback"]
    assert _run(p.get_client("c2.forjado")) is None


# ---------------------------------------------------------------- tela de login

def _app():
    return TestClient(Starlette(routes=[
        Route("/login", auth.login_page, methods=["GET"]),
        Route("/login", auth.login_submit, methods=["POST"]),
    ]))


def _pendente(nome="<img src=x onerror=alert(1)>"):
    p = auth.SEIProOAuthProvider()
    c = _registrar(p, nome=nome)
    params = AuthorizationParams(
        state="st", scopes=[], code_challenge="x" * 43,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
    )
    url = _run(p.authorize(c, params))
    return url.split("session=", 1)[1]


def test_login_nao_reflete_session_arbitraria():
    r = _app().get('/login?session="><script>alert(1)</script>')
    assert r.status_code == 400
    assert "<script>" not in r.text


def test_login_escapa_nome_do_cliente_e_mostra_destino():
    sid = _pendente()
    r = _app().get(f"/login?session={sid}")
    assert r.status_code == 200
    assert "<img src=x" not in r.text and "&lt;img src=x" in r.text
    assert "claude.ai" in r.text
    assert "script-src" not in r.headers["content-security-policy"]  # default-src 'none'
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


@pytest.mark.parametrize("url", [
    "http://sei.exemplo.gov.br/api",
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1/api",
    "https://10.0.0.5/api",
    "https://localhost/api",
])
def test_login_recusa_url_sei_interna_ou_sem_https(url):
    sid = _pendente()
    r = _app().post("/login", data={**CREDS, "sei_url": url, "session": sid})
    assert r.status_code == 400
    assert not [k for k in auth._auth_codes if k.startswith("code:")]
    assert f"pending:{sid}" in auth._auth_codes  # pode corrigir e reenviar


def test_login_ok_escapa_usuario_no_sucesso(monkeypatch):
    async def ok(url):
        return None
    monkeypatch.setattr(auth, "validar_url_sei", ok)
    sid = _pendente(nome="Claude")
    r = _app().post("/login", data={**CREDS, "sei_usuario": '"><script>x()</script>',
                                     "session": sid})
    assert r.status_code == 200
    assert "<script>x()" not in r.text
    assert 'href="https://claude.ai/api/mcp/auth_callback?code=' in r.text
    assert "javascript:" not in r.text


# ---------------------------------------------------------------- SSRF / segredos

def test_allowlist_de_hosts_sei(monkeypatch):
    monkeypatch.setenv("SEI_ALLOWED_HOSTS", "sei.antaq.gov.br,*.sp.gov.br")
    assert seg.validar_url_sei_sem_rede("https://sei.antaq.gov.br/x") is None
    assert seg.validar_url_sei_sem_rede("https://sei.fazenda.sp.gov.br/x") is None
    assert seg.validar_url_sei_sem_rede("https://atacante.example/x")
    assert seg.validar_url_sei_sem_rede("https://sei.antaq.gov.br.atacante.example/x")


def test_curinga_no_meio_vale_um_rotulo(monkeypatch):
    monkeypatch.setenv("SEI_ALLOWED_HOSTS", "sei.*.gov.br")
    assert seg.validar_url_sei_sem_rede("https://sei.antaq.gov.br/x") is None
    assert seg.validar_url_sei_sem_rede("https://sei.mg.gov.br/x") is None
    assert seg.validar_url_sei_sem_rede("https://sei.a.b.gov.br/x")
    assert seg.validar_url_sei_sem_rede("https://sei.x.gov.br.atacante.example/x")
    assert seg.validar_url_sei_sem_rede("https://treinamentosei.antaq.gov.br/x")


def test_segredos_do_operador_so_vao_para_o_host_configurado(monkeypatch):
    monkeypatch.setenv("SEI_URL", "https://sei.antaq.gov.br/sei/modulos/wssei/api/v2")
    monkeypatch.setenv("SEI_EXTRA_HEADERS", '{"X-Bypass":"segredo"}')
    monkeypatch.setenv("SEI_CF_CLEARANCE", "cookie-secreto")
    monkeypatch.setenv("SEI_TRANSPORT", "httpx")

    alheio = SEIClient(sei_url="https://atacante.example/api", sei_usuario="u", sei_senha="s")
    assert "X-Bypass" not in alheio._client.headers
    assert not alheio._extra_headers
    assert "cf_clearance" not in alheio._client.cookies

    proprio = SEIClient()
    assert proprio._client.headers["X-Bypass"] == "segredo"
    assert proprio._client.cookies.get("cf_clearance") == "cookie-secreto"


def test_arquivo_local_desabilitado_no_cliente():
    cli = SEIClient(sei_url="https://sei.exemplo.gov.br", permitir_arquivo_local=False)
    with pytest.raises(Exception, match="desabilitado"):
        _run(cli.criar_documento_externo(id_procedimento="1", id_serie="1",
                                         arquivo_path="/proc/self/environ"))


def test_tools_recusam_arquivo_path_em_modo_http(monkeypatch):
    import mcp_seipro.server as srv

    monkeypatch.setattr(srv, "_http_mode", True)
    for coro in (
        srv.sei_criar_documento_externo(processo="1", id_serie="1",
                                        arquivo_path="/proc/self/environ", ctx=None),
        srv.sei_alterar_documento_externo(id_documento="1",
                                          arquivo_path="/proc/self/environ", ctx=None),
    ):
        out = json.loads(_run(coro))
        assert "desabilitado" in json.dumps(out, ensure_ascii=False)
