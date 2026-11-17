"""Item 1: o POST /login confere a senha no SEI antes de emitir o código OAuth.

Regra que governa tudo: senha errada repetida BLOQUEIA a conta no SEI. Então:
uma requisição ao /autenticar por envio do formulário, nunca retry, e um limite
por usuário que recusa sem tocar no SEI. Tudo contra um SEI simulado.
"""
import asyncio
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("JWT_SECRET", "x" * 48)

from mcp.server.auth.provider import AuthorizationParams  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import mcp_seipro.auth as auth  # noqa: E402
from mcp_seipro.limite_login import LIMITE  # noqa: E402
from mcp_seipro.sei_client import SEIClient  # noqa: E402
from mcp_seipro.sessao_cache import CACHE  # noqa: E402
from sei_simulado import SENHA_CERTA, SEISimulado, creds, instalar  # noqa: E402

SENHA_ERRADA = "Errada#QueNaoPodeVazar-7731"


@pytest.fixture(autouse=True)
def _estado(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 48)
    for d in (auth._clients, auth._auth_codes, auth._sessoes_revogadas, auth._refresh_usados):
        d.clear()
    LIMITE.limpar()
    CACHE.limpar()

    async def url_ok(url):
        return None
    monkeypatch.setattr(auth, "validar_url_sei", url_ok)
    yield
    LIMITE.limpar()
    CACHE.limpar()


@pytest.fixture
def sei(monkeypatch):
    sim = SEISimulado()
    instalar(monkeypatch, sim)
    return sim


def _app():
    return TestClient(Starlette(routes=[
        Route("/login", auth.login_page, methods=["GET"]),
        Route("/login", auth.login_submit, methods=["POST"]),
    ]))


def _pendente():
    p = auth.SEIProOAuthProvider()
    c = OAuthClientInformationFull(
        client_id="tmp", client_secret="s", client_name="Claude",
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        token_endpoint_auth_method="client_secret_post",
    )
    asyncio.run(p.register_client(c))
    params = AuthorizationParams(
        state="st", scopes=[], code_challenge="x" * 43,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
    )
    return asyncio.run(p.authorize(c, params)).split("session=", 1)[1]


def _post(sid, senha, usuario="fulano"):
    return _app().post("/login", data={**creds(senha, usuario), "session": sid})


def _codigos():
    return [k for k in auth._auth_codes if k.startswith("code:")]


# ------------------------------------------------------------------ senha errada

def test_senha_errada_faz_exatamente_uma_chamada_ao_autenticar(sei, caplog):
    caplog.set_level(logging.DEBUG)
    sid = _pendente()
    r = _post(sid, SENHA_ERRADA)

    assert sei.logins == 1, sei.chamadas
    assert sei.chamadas == ["/autenticar"]  # e nada além dele
    assert r.status_code == 400
    assert "O SEI recusou o login" in r.text
    assert "Usuário ou senha inválido" in r.text
    assert not _codigos(), "código OAuth emitido com senha errada"
    assert f"pending:{sid}" in auth._auth_codes, "pending perdido: usuário não pode corrigir"
    # A senha não volta no formulário nem vai para o log.
    assert SENHA_ERRADA not in r.text
    assert SENHA_ERRADA not in caplog.text
    # O usuário digitado volta (para corrigir só a senha).
    assert 'value="fulano"' in r.text


def test_senha_certa_emite_codigo_com_um_login(sei):
    sid = _pendente()
    r = _post(sid, SENHA_CERTA)
    assert r.status_code == 200
    assert sei.logins == 1
    assert len(_codigos()) == 1
    assert f"pending:{sid}" not in auth._auth_codes
    assert "conferido com o SEI" in r.text


def test_cliente_da_conferencia_e_fechado(sei, monkeypatch):
    fechados = []
    original = SEIClient.close

    async def close(self):
        fechados.append(self)
        await original(self)
    monkeypatch.setattr(SEIClient, "close", close)

    _post(_pendente(), SENHA_ERRADA)
    _post(_pendente(), SENHA_CERTA)
    sei.fora_do_ar = True
    _post(_pendente(), SENHA_CERTA)
    assert len(fechados) == 3, "cada conferência fecha o seu cliente (httpx/browser)"
    assert sei.fechados >= 3
    assert len(CACHE) == 0, "a conferência não deixa cliente vivo no cache"


def test_401_403_json_no_autenticar_conta_como_recusa(sei):
    sei.autenticar_status = 401
    r = _post(_pendente(), SENHA_CERTA)
    assert r.status_code == 400 and "O SEI recusou o login" in r.text
    assert sei.logins == 1
    assert not _codigos()


# ------------------------------------------------------------------ limite por usuário

def test_limite_recusa_sem_tocar_no_sei(sei):
    sid = _pendente()
    for _ in range(3):
        assert _post(sid, SENHA_ERRADA).status_code == 400
    assert sei.logins == 3

    # 4ª tentativa: nem a senha certa vai ao SEI dentro da janela.
    r = _post(sid, SENHA_CERTA)
    assert r.status_code == 429
    assert "Login suspenso" in r.text
    assert sei.logins == 3, "o formulário virou caminho de bloqueio da conta"
    assert not _codigos()
    assert f"pending:{sid}" in auth._auth_codes


def test_limite_e_por_usuario_e_expira(sei, monkeypatch):
    agora = [1000.0]
    monkeypatch.setattr(LIMITE, "_relogio", lambda: agora[0])
    for _ in range(3):
        _post(_pendente(), SENHA_ERRADA)
    assert _post(_pendente(), SENHA_CERTA).status_code == 429
    # outro usuário não é afetado
    assert _post(_pendente(), SENHA_CERTA, usuario="beltrano").status_code == 200
    # usuário com maiúsculas/espaços é o mesmo usuário
    assert _post(_pendente(), SENHA_CERTA, usuario=" FULANO ").status_code == 429
    agora[0] += LIMITE.janela_s + 1
    assert _post(_pendente(), SENHA_CERTA).status_code == 200


def test_avisa_quantas_tentativas_restam(sei):
    r = _post(_pendente(), SENHA_ERRADA)
    assert "Mais 2 recusa(s)" in r.text


# ------------------------------------------------------------------ não deu para conferir

def test_sei_fora_do_ar_recusa_com_mensagem_distinta(sei):
    sei.fora_do_ar = True
    sid = _pendente()
    r = _post(sid, SENHA_CERTA)
    assert r.status_code == 503
    assert "Não foi possível conferir a senha agora" in r.text
    assert "O SEI recusou" not in r.text
    assert not _codigos()
    assert f"pending:{sid}" in auth._auth_codes
    assert sei.logins == 1  # sem retry
    # Indisponibilidade não é recusa de senha: não consome o limite.
    for _ in range(5):
        _post(sid, SENHA_CERTA)
    sei.fora_do_ar = False
    assert _post(sid, SENHA_CERTA).status_code == 200


def test_borda_cloudflare_recusa_com_mensagem_de_borda(sei):
    sei.borda_403 = True
    r = _post(_pendente(), SENHA_CERTA)
    assert r.status_code == 503
    assert "Cloudflare" in r.text
    assert not _codigos()
    assert sei.logins == 1


def test_post_duplicado_durante_a_conferencia_nao_faz_outro_login(sei):
    sid = _pendente()
    auth._auth_codes[f"pending:{sid}"]["conferindo"] = True  # 1º POST ainda no SEI
    r = _post(sid, SENHA_CERTA)
    assert r.status_code == 409
    assert sei.logins == 0
