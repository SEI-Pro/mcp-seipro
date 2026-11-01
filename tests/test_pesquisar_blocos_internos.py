"""sei_pesquisar_blocos_internos: busca por nome tolerante a acento e charset."""

import json
from types import SimpleNamespace

import pytest

import mcp_seipro.server as srv

BLOCOS = [
    {"id": "905", "descricao": "SEJUC – Contratos 2026", "estado": "A",
     "siglaUnidade": "SEJUC/DEPLAF", "numeroProcessos": 12},
    {"id": "904", "descricao": "Licitação de mobiliário", "estado": "C",
     "siglaUnidade": "SEJUC/DEPLAF", "numeroProcessos": 3},
    # Gravado antes da correção de charset (UTF-8 lido como Latin-1):
    {"id": "903", "descricao": "AdministraÃ§Ã£o e FinanÃ§as", "estado": "A",
     "siglaUnidade": "SEJUC/DEPLAF", "numeroProcessos": 0},
    {"id": "902", "descricao": "ÂMBITO ESTADUAL - diárias", "estado": "A",
     "siglaUnidade": "SEJUC/DEPLAF", "numeroProcessos": 1},
]


class ClienteBlocos:
    def __init__(self, blocos, nativa_falha=False):
        self.blocos = blocos
        self.nativa_falha = nativa_falha
        self.chamadas = []

    async def pesquisar_blocos_internos(self, filtro="", limit=50, start=0, estado=""):
        self.chamadas.append({"filtro": filtro, "limit": limit, "start": start, "estado": estado})
        if filtro and self.nativa_falha:
            raise RuntimeError("busca nativa indisponível")
        itens = self.blocos
        if estado:
            itens = [b for b in itens if b["estado"] in estado.split(",")]
        if filtro:  # simula a busca nativa sensível a acento
            itens = [b for b in itens if filtro.lower() in b["descricao"].lower()]
        pagina = itens[start * limit:(start + 1) * limit]
        return {"blocos": pagina}


@pytest.fixture
def usar(monkeypatch):
    def _usar(cliente):
        monkeypatch.setattr(srv, "_get_client", lambda ctx: cliente)
        return cliente
    return _usar


async def _pesquisar(**kw):
    return json.loads(await srv.sei_pesquisar_blocos_internos(ctx=SimpleNamespace(), **kw))


@pytest.mark.asyncio
async def test_ignora_acento_maiuscula_e_ordem(usar):
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(nome="contratos sejuc")
    assert [b["id"] for b in r["blocos"]] == ["905"]

    r = await _pesquisar(nome="LICITACAO mobiliario")
    assert [b["id"] for b in r["blocos"]] == ["904"]
    assert r["blocos"][0]["estado_descricao"] == "Concluído"


@pytest.mark.asyncio
async def test_encontra_e_repara_nome_com_charset_corrompido(usar):
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(nome="administração")
    assert [b["id"] for b in r["blocos"]] == ["903"]
    assert r["blocos"][0]["descricao"] == "Administração e Finanças"


@pytest.mark.asyncio
async def test_maiuscula_acentuada_legitima_nao_e_estragada(usar):
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(nome="ambito diarias")
    assert r["blocos"][0]["descricao"] == "ÂMBITO ESTADUAL - diárias"


@pytest.mark.asyncio
async def test_filtra_por_estado(usar):
    c = usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(estado="c")
    assert [b["id"] for b in r["blocos"]] == ["904"]
    assert all(ch["estado"] == "C" for ch in c.chamadas)


@pytest.mark.asyncio
async def test_sem_nome_lista_mais_recentes_primeiro(usar):
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(limit=2)
    assert [b["id"] for b in r["blocos"]] == ["905", "904"]
    assert r["tem_mais"] is True


@pytest.mark.asyncio
async def test_sem_nome_com_pagina_menor_que_limite_parou_cedo(usar, monkeypatch):
    # Página de 100 e limit 20: antes devolvia total_encontrado=100 (tamanho da
    # página, não da unidade). Agora o total é desconhecido e tem_mais=True.
    monkeypatch.setattr(srv, "_TAM_PAGINA_BLOCOS", 3)
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(limit=2)
    assert r["total_encontrado"] is None
    assert r["tem_mais"] is True


@pytest.mark.asyncio
async def test_com_nome_total_e_exato(usar):
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(nome="sejuc")
    assert r["total_encontrado"] == 1
    assert r["tem_mais"] is False


@pytest.mark.asyncio
async def test_falha_na_busca_nativa_usa_varredura(usar):
    usar(ClienteBlocos(BLOCOS, nativa_falha=True))
    r = await _pesquisar(nome="mobiliario")
    assert [b["id"] for b in r["blocos"]] == ["904"]


@pytest.mark.asyncio
async def test_varredura_pagina_e_avisa_quando_trunca(usar, monkeypatch):
    monkeypatch.setattr(srv, "_TAM_PAGINA_BLOCOS", 2)
    monkeypatch.setattr(srv, "_MAX_PAGINAS_BLOCOS", 1)
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(nome="diarias")
    assert r["blocos"] == []  # o bloco 902 está além da 1ª página
    assert "_aviso" in r


@pytest.mark.asyncio
async def test_nada_encontrado(usar):
    usar(ClienteBlocos(BLOCOS))
    r = await _pesquisar(nome="inexistente")
    assert r["total_encontrado"] == 0
    assert r["blocos"] == []
