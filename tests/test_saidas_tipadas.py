"""Saída tipada (outputSchema + structuredContent) das tools de leitura principais.

Os testes passam pelo protocolo MCP de ponta a ponta (cliente e servidor em
memória): é lá que o SDK valida o retorno contra o modelo e contra o JSON
Schema. Chamar a função Python direto não exercitaria essa validação — e é ela
que transforma um retorno fora do schema em "Output validation error".

Os payloads da wssei abaixo vêm de respostas reais (processo de teste
50300.018905/2018-67, SEI 5.0.4 / wssei 3.0.2), com ids ora string, ora número.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

import mcp_seipro.server as srv  # noqa: E402

TOOLS_TIPADAS = (
    "sei_listar_processos", "sei_listar_documentos",
    "sei_consultar_processo", "sei_ler_documento",
)


def _doc_wssei(n: int, restrito: bool = False) -> dict:
    return {
        "id": 3000000 + n,  # número, como algumas versões da wssei devolvem
        "atributos": {
            "protocoloFormatado": str(2900000 + n),
            "tipo": "Despacho",
            "tipoDocumento": "I",
            "siglaUnidade": "GPF",
            "nomeComposto": f"Despacho GPF {2900000 + n}",
            "status": {
                "documentoAssinado": "S",
                "documentoCancelado": "N",
                "documentoRestrito": "S" if restrito else "N",
                "documentoSigiloso": "N",
            },
        },
    }


def _processo_wssei() -> dict:
    return {
        "id": "683589",
        "atributos": {
            "idProcedimento": 683589,
            "numero": "50300.018905/2018-67",
            "tipoProcesso": "Finalístico: Plano Anual de Fiscalização (PAF)",
            "descricao": "Modelos de Nota T&eacute;cnica",
            "status": {"nivelAcessoGlobal": "1", "processoEmTramitacao": "S",
                       "processoGeradoRecebido": "G"},
            "marcador": [{"nome": "Prioridade #ff0000", "descricaoCor": "Vermelho",
                          "texto": "prazo 05/12/2026"}],
            "dadosAbertura": {"unidades": [{"id": "110000040", "nome": "GPF"}],
                              "lista": [{"sigla": "GPF"}]},
            "usuarioAtribuido": {"idUsuario": 100007336, "nome": "Fulano"},
            "unidade": {"idUnidade": "110000040"},
            "ciencias": [{"usuario": "x"}],
        },
    }


CONSULTA_REAL = {
    "IdProcedimento": "683589",
    "ProtocoloProcedimentoFormatado": "50300.018905/2018-67",
    "NomeTipoProcedimento": "Finalístico: Plano Anual de Fiscalização (PAF)",
    "especificacao": "Modelos de Nota Técnica para Metodologia Multicritério 2023",
    "tipoProcesso": "100000505",
    "assuntos": [{"id": "6", "codigo": "010", "descricao": "ORGANIZAÇÃO E FUNCIONAMENTO"}],
    "interessados": [{"id": 100007336, "nome": "Pedro Henrique Soares"}],
    "nivelAcesso": "1",
    "hipoteseLegal": "63",
    "grauSigilo": "",
    "observacoes": [{"unidade": "110000040", "observacao": "Teste"}],
    "campoNovoDaWssei": {"qualquer": "coisa"},
}


# Minuta de outra unidade: o número SEI converte, e o SEI recusa o id.
NUMERO_RECUSADO = "2900500"
ID_RECUSADO = "3000500"


class _FakeSEI:
    def __init__(self, *a, **k):
        self._unidade_ativa = "110000040"
        self.falhar_com = None

    async def close(self):
        pass

    async def listar_processos(self, limit=50, start=0, apenas_meus="", filtro=""):
        if FAKE.falhar_com:
            raise Exception(FAKE.falhar_com)
        return {"processos": [_processo_wssei()], "total_itens": "1", "tem_proxima": False}

    async def listar_documentos_pagina(self, id_proc, limit=200, start=0):
        return {"documentos": [_doc_wssei(0), _doc_wssei(1)], "total": 2}

    async def listar_documentos(self, id_proc, limit=200):
        return [_doc_wssei(0), _doc_wssei(1)]

    async def consultar_processo_completo(self, protocolo):
        return dict(CONSULTA_REAL)

    async def consultar_processo(self, protocolo):
        return {"IdProcedimento": "683589"}

    async def consultar_documento_interno(self, id_doc):
        if id_doc == ID_RECUSADO:
            raise Exception("Acesso ao documento não autorizado.")
        return {"nivelAcesso": "1" if id_doc == "3000009" else "0"}

    async def consultar_documento_interno_formatado(self, numero):
        if numero == NUMERO_RECUSADO:
            return {"idDocumento": ID_RECUSADO, "protocolo": "683589"}
        raise Exception("Documento não encontrado.")

    async def visualizar_documento_interno(self, id_doc):
        return "<p class='Texto_Justificado'>Conteúdo do <b>despacho</b>.</p>"


FAKE = _FakeSEI()


class _FakeWeb:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


def _chamar(nome: str, args: dict):
    orig_cli, orig_web = srv.SEIClient, srv.SEIWebClient
    srv.SEIClient = lambda *a, **k: FAKE
    srv.SEIWebClient = _FakeWeb

    async def run():
        async with create_connected_server_and_client_session(srv.mcp._mcp_server) as s:
            ferramentas = {t.name: t for t in (await s.list_tools()).tools}
            return ferramentas, await s.call_tool(nome, args)

    try:
        return asyncio.run(run())
    finally:
        srv.SEIClient, srv.SEIWebClient = orig_cli, orig_web
        FAKE.falhar_com = None


def test_schemas_descrevem_a_forma_real():
    tools, _ = _chamar("sei_listar_documentos", {"protocolo_formatado": "683589"})
    for nome in TOOLS_TIPADAS:
        props = tools[nome].outputSchema["properties"]
        assert "result" not in props, f"{nome} ainda devolve string embrulhada"
    doc = tools["sei_listar_documentos"].outputSchema
    item = doc["$defs"]["DocumentoResumido"]["properties"]
    assert item["assinado"]["type"] == "boolean"
    assert "_nota" in tools["sei_listar_processos"].outputSchema["properties"]


def test_listar_documentos_estruturado_e_ids_como_string():
    _, r = _chamar("sei_listar_documentos", {"protocolo_formatado": "683589"})
    assert not r.isError, r.content
    sc = r.structuredContent
    assert sc["total"] == 2 and sc["retornados"] == 2
    assert sc["documentos"][0]["id"] == "3000000"  # número da wssei → string
    assert sc["documentos"][0]["assinado"] is True
    assert sc["documentos"][0]["bruto"] is None


def test_listar_documentos_bruto_sob_demanda():
    _, r = _chamar("sei_listar_documentos",
                   {"protocolo_formatado": "683589", "resumido": False})
    assert not r.isError, r.content
    bruto = r.structuredContent["documentos"][0]["bruto"]
    assert bruto["atributos"]["status"]["documentoAssinado"] == "S"


def test_listar_processos_estruturado():
    _, r = _chamar("sei_listar_processos", {})
    assert not r.isError, r.content
    p = r.structuredContent["processos"][0]
    assert p["id_procedimento"] == "683589"
    assert p["descricao"] == "Modelos de Nota Técnica"
    assert p["marcador"] == {"nome": "Prioridade", "cor": "vermelho"}
    assert p["prazo"] == "2026-12-05"
    assert p["atribuido_unidade_atual"]["id_usuario"] == "100007336"
    assert r.structuredContent["total_itens"] == 1  # "1" da wssei → int


def test_listar_processos_detalhe_e_contagem():
    _, r = _chamar("sei_listar_processos", {"incluir_detalhe": True})
    assert r.structuredContent["processos"][0]["ciencias"] == [{"usuario": "x"}]
    _, r = _chamar("sei_listar_processos", {"apenas_contar": True})
    assert not r.isError, r.content
    assert r.structuredContent["processos"] == []
    assert r.structuredContent["paginas"] == 1


def test_erro_vira_isError_com_classificacao():
    FAKE.falhar_com = "Bloqueado pela borda: desafio do Cloudflare (managed challenge)"
    _, r = _chamar("sei_listar_processos", {})
    assert r.isError
    texto = r.content[0].text
    corpo = json.loads(texto[texto.index("{"):])
    assert corpo["erro_origem"] == "cloudflare_borda"
    assert "Output validation error" not in texto


def test_consultar_processo_preserva_campos_e_aviso():
    _, r = _chamar("sei_consultar_processo",
                   {"protocolo_formatado": "50300.018905/2018-67"})
    assert not r.isError, r.content
    sc = r.structuredContent
    assert sc["IdProcedimento"] == "683589"
    assert sc["interessados"][0]["id"] == "100007336"
    assert sc["assuntos"][0]["codigo"] == "010"
    assert sc["_aviso_acesso"]["nivel_acesso"] == "1"
    assert sc["campoNovoDaWssei"] == {"qualquer": "coisa"}, "campo extra não pode sumir"


def test_ler_documento_texto_legivel_e_estruturado():
    _, r = _chamar("sei_ler_documento", {"id_documento": "3000000", "tipo_documento": "I"})
    assert not r.isError, r.content
    sc = r.structuredContent
    assert sc["liberado"] is True and sc["formato"] == "markdown"
    assert "**despacho**" in sc["conteudo"]
    # content leva o documento em si, não JSON com escapes
    assert r.content[0].text == sc["conteudo"]


def test_ler_documento_restrito_sem_consentimento_bloqueia():
    _, r = _chamar("sei_ler_documento", {"id_documento": "3000009", "tipo_documento": "I"})
    assert not r.isError, r.content
    sc = r.structuredContent
    assert sc["liberado"] is False and sc["conteudo"] is None
    assert sc["bloqueio"]["nivel_acesso"] == "1"
    assert "Conteúdo do" not in r.content[0].text


def test_ler_documento_restrito_autorizado_leva_aviso():
    _, r = _chamar("sei_ler_documento", {"id_documento": "3000009", "tipo_documento": "I",
                                         "confirmar_acesso_restrito": True})
    sc = r.structuredContent
    assert sc["liberado"] is True
    assert sc["aviso_acesso"]["rotulo_nivel"] == "Restrito"
    assert sc["conteudo"].startswith("> **ATENÇÃO"), "aviso continua no texto"


def test_nao_autorizado_apos_conversao_diz_o_que_se_sabe():
    """O número foi convertido; a mensagem não pode sugerir que não foi."""
    _, r = _chamar("sei_ler_documento", {"id_documento": NUMERO_RECUSADO})
    assert r.isError
    texto = r.content[0].text
    assert f"{NUMERO_RECUSADO}, foi convertido para o id interno {ID_RECUSADO}" in texto
    assert "não distingue" in texto
    assert "Verifique se você passou" not in texto
    assert json.loads(texto.split(": ", 1)[1])["erro_origem"] == "sei_acesso"


def test_nao_autorizado_sem_conversao_aponta_o_auto():
    _, r = _chamar("sei_ler_documento", {"id_documento": ID_RECUSADO, "tipo_documento": "I"})
    assert r.isError
    assert "sem conversão" in r.content[0].text
    assert "tipo_documento='auto'" in r.content[0].text


def test_msg_nao_autorizado_quando_nao_achou_como_numero():
    m = srv._msg_nao_autorizado("123", "123", "Acesso negado.")
    assert "não foi encontrado como número SEI" in m
    assert m.endswith("Mensagem do SEI: Acesso negado.")
