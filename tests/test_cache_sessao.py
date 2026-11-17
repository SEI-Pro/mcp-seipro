"""Item 2: um SEIClient autenticado por sessão OAuth, reaproveitado entre tools.

As tools são as do server.py, chamadas com o mesmo lifespan que o modo HTTP
usa por request; o SEI é simulado e conta cada POST /autenticar.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("JWT_SECRET", "x" * 48)

from mcp.server.auth.provider import AccessToken  # noqa: E402

import mcp_seipro.auth as auth  # noqa: E402
import mcp_seipro.server as srv  # noqa: E402
from mcp_seipro.limite_login import LIMITE  # noqa: E402
from mcp_seipro.sessao_cache import CACHE  # noqa: E402
from sei_simulado import SEISimulado, creds, instalar  # noqa: E402


@pytest.fixture(autouse=True)
def _estado(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 48)
    monkeypatch.setattr(srv, "_http_mode", True)
    for d in (auth._clients, auth._auth_codes, auth._sessoes_revogadas, auth._refresh_usados):
        d.clear()
    LIMITE.limpar()
    CACHE.limpar()
    yield
    CACHE.limpar()
    LIMITE.limpar()


@pytest.fixture
def sei(monkeypatch):
    sim = SEISimulado()
    instalar(monkeypatch, sim)
    return sim


def _sessao(senha=None, usuario="fulano"):
    """Tokens de uma sessão OAuth nova (sid próprio), como o /token emitiria."""
    c = creds(usuario=usuario) if senha is None else creds(senha, usuario)
    return auth._emitir(sei=c, client_id="cli", scopes=[], sid=os.urandom(8).hex(),
                        auth_time=__import__("time").time())


def _usar_token(monkeypatch, token: str):
    import mcp.server.auth.middleware.auth_context as ac
    monkeypatch.setattr(ac, "get_access_token",
                        lambda: AccessToken(token=token, client_id="cli", scopes=[]))


async def _tool(tool, **kw):
    """Uma chamada de tool = um request HTTP stateless = um lifespan."""
    async with srv.lifespan(srv.mcp) as lc:
        ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lc))
        return json.loads(await tool(ctx=ctx, **kw))


def _sid(tok):
    return auth.get_sessao_from_token(tok.access_token)[0]


# ------------------------------------------------------------------ um login por sessão

def test_duas_tools_na_mesma_sessao_fazem_um_login_so(sei, monkeypatch):
    tok = _sessao()
    _usar_token(monkeypatch, tok.access_token)

    async def cenario():
        v = await _tool(srv.sei_versao)
        u = await _tool(srv.sei_listar_unidades)
        return v, u

    v, u = asyncio.run(cenario())
    assert v == {"sei": "5.0.4", "wssei": "3.0.2"}
    assert u == []
    assert sei.logins == 1, sei.chamadas
    assert sei.chamadas == ["/autenticar", "/versao", "/usuario/unidades"]


def test_refresh_mantem_a_sessao_e_o_login(sei, monkeypatch):
    tok = _sessao()
    _usar_token(monkeypatch, tok.access_token)
    asyncio.run(_tool(srv.sei_versao))
    # Refresh: access token novo, mesmo sid → mesmo cliente.
    payload = auth._abrir_token(tok.refresh_token)
    novo = auth._emitir(sei=payload["sei"], client_id="cli", scopes=[],
                        sid=payload["sid"], auth_time=payload["auth_time"])
    _usar_token(monkeypatch, novo.access_token)
    asyncio.run(_tool(srv.sei_versao))
    assert sei.logins == 1


def test_chamadas_simultaneas_da_mesma_sessao_fazem_um_login(sei, monkeypatch):
    _usar_token(monkeypatch, _sessao().access_token)

    async def cenario():
        return await asyncio.gather(*[_tool(srv.sei_versao) for _ in range(5)])

    assert all("error" not in r for r in asyncio.run(cenario()))
    assert sei.logins == 1


def test_sessoes_diferentes_do_mesmo_usuario_nao_se_misturam(sei, monkeypatch):
    a, b = _sessao(), _sessao()
    assert _sid(a) != _sid(b)
    _usar_token(monkeypatch, a.access_token)
    asyncio.run(_tool(srv.sei_versao))
    _usar_token(monkeypatch, b.access_token)
    asyncio.run(_tool(srv.sei_versao))
    assert sei.logins == 2
    assert len(CACHE) == 2


def test_mesma_chave_com_credencial_diferente_troca_o_cliente():
    criados = []
    from mcp_seipro.sessao_cache import CacheClientesSEI
    cache = CacheClientesSEI(fabrica=lambda c: criados.append(c) or SimpleNamespace())
    e1 = cache.emprestar("sid", creds(usuario="a"))
    cache.devolver(e1)
    e2 = cache.emprestar("sid", creds(usuario="b"))
    assert e1.cliente is not e2.cliente and e1.despejado and len(criados) == 2


# ------------------------------------------------------------------ invalidação e fechamento

def test_revogacao_invalida_e_fecha_o_cliente(sei, monkeypatch):
    tok = _sessao()
    _usar_token(monkeypatch, tok.access_token)

    async def cenario():
        await _tool(srv.sei_versao)
        at = await auth.SEIProOAuthProvider().load_access_token(tok.access_token)
        await auth.SEIProOAuthProvider().revoke_token(at)
        await asyncio.sleep(0)  # deixa o fechamento agendado rodar
        return await _tool(srv.sei_versao)

    r = asyncio.run(cenario())
    assert r["error"] == "Token invalido ou expirado. Reconecte o MCP."
    assert len(CACHE) == 0
    assert sei.fechados == 1
    assert sei.logins == 1


def test_ttl_expira_faz_login_novo_e_fecha_o_antigo(sei, monkeypatch):
    agora = [0.0]
    monkeypatch.setattr(CACHE, "_relogio", lambda: agora[0])
    _usar_token(monkeypatch, _sessao().access_token)

    async def cenario():
        await _tool(srv.sei_versao)
        agora[0] += CACHE.ttl_s + 1
        await _tool(srv.sei_versao)
        await asyncio.sleep(0)

    asyncio.run(cenario())
    assert sei.logins == 2
    assert sei.fechados == 1


def test_lru_despeja_e_so_fecha_depois_da_devolucao():
    fechados = []

    class Cli:
        falha_autenticacao = None

        def __init__(self, n):
            self.n = n

        async def close(self):
            fechados.append(self.n)

    from mcp_seipro.sessao_cache import CacheClientesSEI
    seq = iter(range(10))
    cache = CacheClientesSEI(fabrica=lambda c: Cli(next(seq)), max_entradas=2)

    async def cenario():
        em_uso = cache.emprestar("s0", creds())     # chamada longa em curso
        cache.devolver(cache.emprestar("s1", creds()))
        cache.devolver(cache.emprestar("s2", creds()))  # estoura: s0 é o mais antigo
        await asyncio.sleep(0)
        assert "s0" not in cache and em_uso.despejado
        assert fechados == [], "fechou um cliente no meio de uma chamada"
        cache.devolver(em_uso)
        await asyncio.sleep(0)
        assert fechados == [0]

    asyncio.run(cenario())


def test_limite_de_clientes_com_browser():
    from mcp_seipro.sessao_cache import CacheClientesSEI

    class Cli:
        falha_autenticacao = None

        def __init__(self, browser):
            self.browser = browser

        def _is_browser_client(self):
            return self.browser

        async def close(self):
            pass

    cache = CacheClientesSEI(fabrica=lambda c: Cli(c["b"]), max_entradas=10, max_browser=1)
    cache.devolver(cache.emprestar("a", {"b": True}))
    cache.devolver(cache.emprestar("h", {"b": False}))
    cache.devolver(cache.emprestar("c", {"b": True}))
    assert "a" not in cache and "c" in cache and "h" in cache


# ------------------------------------------------------------------ senha recusada no meio da sessão

def test_senha_trocada_no_sei_gera_uma_recusa_e_para(sei, monkeypatch):
    tok = _sessao()
    _usar_token(monkeypatch, tok.access_token)

    async def cenario():
        await _tool(srv.sei_versao)                     # login 1, ok
        entrada = CACHE._entradas[_sid(tok)]
        entrada.cliente._token_emitido_em -= 3600       # token velho
        sei.senha = "outra-senha"                       # senha trocada no SEI
        sei.invalidar_tokens()
        r2 = await _tool(srv.sei_versao)                # 403 → re-login (login 2) recusado
        r3 = await _tool(srv.sei_versao)                # nada vai ao SEI
        r4 = await _tool(srv.sei_listar_unidades)
        return r2, r3, r4

    r2, r3, r4 = asyncio.run(cenario())
    assert sei.logins == 2, sei.chamadas
    assert sei.chamadas[-1] == "/autenticar", "houve requisição ao SEI depois da recusa"
    for r in (r2, r3, r4):
        assert r["error"].startswith("Falha na autenticação SEI: Usuário ou senha inválido!")
        assert "erro_origem" not in r  # classificação de antes, inalterada
    assert "não tenta de novo" in r3["error"]


def test_recusas_em_sessoes_diferentes_respeitam_o_limite_do_usuario(sei, monkeypatch):
    async def cenario():
        for _ in range(5):
            _usar_token(monkeypatch, _sessao(senha="errada").access_token)
            await _tool(srv.sei_versao)

    asyncio.run(cenario())
    assert sei.logins == LIMITE.max_falhas == 3


# ------------------------------------------------------------------ estado por chamada

def test_unidade_atual_nao_atravessa_chamadas(sei, monkeypatch):
    tok = _sessao()
    _usar_token(monkeypatch, tok.access_token)

    async def cenario():
        await _tool(srv.sei_trocar_unidade, id_unidade="110")
        cli = CACHE._entradas[_sid(tok)].cliente
        assert cli._unidade_ativa == "110"
        cli._cache_set("marcadores::50:0", {"x": 1})
        cli._cache_set("unidades_usuario", ["u"])
        await _tool(srv.sei_versao)
        return cli

    cli = asyncio.run(cenario())
    assert cli._unidade_ativa is None, "crença sobre a unidade vazou para a chamada seguinte"
    assert cli._cache_get("marcadores::50:0") is None
    assert cli._cache_get("unidades_usuario") == ["u"]  # metadado por usuário continua
    assert sei.logins == 1
