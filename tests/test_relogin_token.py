"""Item 3: 401/403 com token recém-emitido não dispara re-login.

Token com menos de SEI_RELOGIN_IDADE_MIN_S (60 s) não pode ter expirado: o 403 é
acesso negado e sai direto. Token mais velho mantém o re-login (uma vez),
necessário com o cache de sessões (o wssei troca a chave do token a cada dia).
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_seipro.sei_client import (  # noqa: E402
    SEIAcessoNegado, SEICloudflareBlocked, SEIClient, SEICredencialRecusada,
)
from sei_simulado import SENHA_CERTA, SEISimulado, creds, instalar  # noqa: E402


@pytest.fixture
def sei(monkeypatch):
    sim = SEISimulado()
    instalar(monkeypatch, sim)
    return sim


def _cliente(senha=SENHA_CERTA):
    return SEIClient(**creds(senha))


def _envelhecer(cli, s=3600):
    cli._token_emitido_em -= s


def test_403_com_token_recem_emitido_nao_reloga(sei):
    sei.negados.add("/versao")
    cli = _cliente()
    with pytest.raises(SEIAcessoNegado) as e:
        asyncio.run(cli.versao())
    assert sei.logins == 1, sei.chamadas
    assert sei.chamadas == ["/autenticar", "/versao"]
    assert "autenticação/permissão" in str(e.value)  # texto que o envelope classifica


def test_403_com_token_velho_reloga_uma_vez_e_segue(sei):
    cli = _cliente()

    async def cenario():
        await cli.versao()
        _envelhecer(cli)
        sei.invalidar_tokens()  # virada de data do wssei
        return await cli.versao()

    assert asyncio.run(cenario())["sei"] == "5.0.4"
    assert sei.logins == 2


def test_403_persistente_com_token_velho_reloga_uma_vez_e_nega(sei):
    cli = _cliente()

    async def cenario():
        await cli.versao()
        _envelhecer(cli)
        sei.negados.add("/versao")
        await cli.versao()

    with pytest.raises(SEIAcessoNegado):
        asyncio.run(cenario())
    assert sei.logins == 2
    assert sei.chamadas[-3:] == ["/versao", "/autenticar", "/versao"]


def test_403_concorrentes_com_o_mesmo_token_velho_fazem_um_relogin(sei):
    cli = _cliente()

    async def cenario():
        await cli.versao()
        _envelhecer(cli)
        sei.invalidar_tokens()
        return await asyncio.gather(*[cli.versao() for _ in range(4)])

    assert all(r["sei"] == "5.0.4" for r in asyncio.run(cenario()))
    assert sei.logins == 2


def test_upload_multipart_segue_a_mesma_regra(sei):
    sei.negados.add("/x")
    cli = _cliente()
    with pytest.raises(SEIAcessoNegado):
        asyncio.run(cli._post_multipart("/x", {}, ("a.pdf", b"%PDF")))
    assert sei.logins == 1


def test_relogin_com_senha_recusada_nao_repete(sei):
    cli = _cliente()

    async def cenario():
        await cli.versao()
        _envelhecer(cli)
        sei.senha = "trocada"
        sei.invalidar_tokens()
        erros = []
        for _ in range(3):
            try:
                await cli.versao()
            except SEICredencialRecusada as e:
                erros.append(e)
        return erros

    erros = asyncio.run(cenario())
    assert len(erros) == 3 and sei.logins == 2
    assert all(str(e).startswith("Falha na autenticação SEI: ") for e in erros)
    assert not erros[0].repetida and erros[1].repetida


def test_autenticar_com_senha_errada_e_uma_requisicao_sem_retry(sei):
    cli = _cliente("errada")
    for _ in range(3):
        with pytest.raises(SEICredencialRecusada, match="^Falha na autenticação SEI: Usuário"):
            asyncio.run(cli.autenticar())
    assert sei.logins == 1


def test_borda_no_autenticar_nao_e_recusa_de_senha(sei):
    sei.borda_403 = True
    cli = _cliente()
    with pytest.raises(SEICloudflareBlocked):
        asyncio.run(cli.autenticar())
    assert cli._credencial_recusada is None  # pode tentar de novo depois
    sei.borda_403 = False
    asyncio.run(cli.autenticar())
    assert cli._token


def test_envelope_de_erro_inalterado():
    import mcp_seipro.server as srv

    recusa = json.loads(srv._error(str(SEICredencialRecusada("Usuário ou senha inválido!"))))
    assert recusa == {"error": "Falha na autenticação SEI: Usuário ou senha inválido!"}
    negado = json.loads(srv._error("403 do SEI (autenticação/permissão), não do WAF: x"))
    assert negado["erro_origem"] == "sei_acesso"
