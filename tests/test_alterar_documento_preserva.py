"""Alterar documento não pode apagar metadados.

`MdWsSeiDocumentoRN::encapsulaDocumento` (wssei 3.0.4) grava todos os campos a
partir do POST: o que não vier é apagado (assuntos viram lista vazia,
interessados/destinatários somem, observação e nível de acesso ficam nulos —
"Nível de acesso local não informado"). A alteração precisa partir da
consulta atual.
"""

from urllib.parse import parse_qs

import httpx
import pytest

from mcp_seipro.sei_client import SEIClient

ATUAL_INTERNO = {
    "idDocumento": "27113158",
    "descricao": "Descrição antiga",
    "nivelAcesso": "1",
    "idHipoteseLegal": "34",
    "grauSigilo": None,
    "observacao": "Observação da unidade",
    "idTipoConferencia": None,
    "assuntos": [{"id": "1243"}, {"id": "877"}],
    "interessados": [{"id": "501"}],
    "destinatarios": [{"id": "900"}, {"id": "901"}],
}

ATUAL_EXTERNO = {
    **ATUAL_INTERNO,
    "numero": "123/2026",
    "idSerie": "100",
    "dataElaboracao": "30/10/2026",
    "idTipoConferencia": "2",
    "remetente": {"id": "777", "nome": "Fulano"},
}


def _form(req: httpx.Request) -> dict:
    return {
        k: v[0]
        for k, v in parse_qs(
            req.content.decode("ascii"), keep_blank_values=True, encoding="iso-8859-1"
        ).items()
    }


def _cliente(atual: dict, enviados: list) -> SEIClient:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"sucesso": True, "data": atual})
        enviados.append(request)
        return httpx.Response(200, json={"sucesso": True, "data": {"ok": 1}})

    c = SEIClient(
        sei_url="https://sei.exemplo.gov.br/api/v2", sei_usuario="u",
        sei_senha="s", sei_orgao="1", permitir_arquivo_local=False,
    )
    c._token = "t"
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    return c


@pytest.mark.asyncio
async def test_interno_so_descricao_preserva_o_resto():
    enviados = []
    c = _cliente(ATUAL_INTERNO, enviados)
    try:
        await c.alterar_documento_interno("27113158", descricao="Consolidação nova")
    finally:
        await c.close()

    f = _form(enviados[0])
    assert f["descricao"] == "Consolidação nova"
    assert f["nivelAcesso"] == "1"
    assert f["idHipoteseLegal"] == "34"
    assert f["observacao"] == "Observação da unidade"
    assert f["assuntos"] == "1243,877"
    assert f["interessados"] == "501"
    assert f["destinatarios"] == "900,901"
    assert "numero" not in f


@pytest.mark.asyncio
async def test_interno_tornar_publico_limpa_hipotese_e_sigilo():
    enviados = []
    c = _cliente({**ATUAL_INTERNO, "nivelAcesso": "2", "grauSigilo": "A"}, enviados)
    try:
        await c.alterar_documento_interno("27113158", nivel_acesso="0")
    finally:
        await c.close()

    f = _form(enviados[0])
    assert f["nivelAcesso"] == "0"
    assert f["idHipoteseLegal"] == ""
    assert f["grauSigilo"] == ""
    assert f["descricao"] == "Descrição antiga"


@pytest.mark.asyncio
async def test_externo_preserva_numero_serie_data_e_remetente():
    enviados = []
    c = _cliente(ATUAL_EXTERNO, enviados)
    try:
        await c.alterar_documento_externo("27113158", descricao="Nova")
    finally:
        await c.close()

    f = _form(enviados[0])
    assert f["descricao"] == "Nova"
    assert f["numero"] == "123/2026"
    assert f["idSerie"] == "100"
    assert f["dataElaboracao"] == "30/10/2026"
    assert f["idTipoConferencia"] == "2"
    assert f["remetente"] == "777"
    assert f["assuntos"] == "1243,877"


def test_ids_csv_aceita_formatos():
    assert SEIClient._ids_csv(None) == ""
    assert SEIClient._ids_csv({"id": 5}) == "5"
    assert SEIClient._ids_csv([{"id": "1"}, {"id": ""}, "3"]) == "1,3"
