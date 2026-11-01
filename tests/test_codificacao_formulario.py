"""Campos de formulário vão ao wssei em ISO-8859-1.

O wssei repassa os campos crus ao núcleo do SEI, que grava em ISO-8859-1.
Enviar UTF-8 gravava "AdministraÃ§Ã£o" — e o cargo da assinatura deixava de
casar com o cadastro ("Cargo/Função ... não está associado com este usuário").
Validado ao vivo no SEI 5.0.5 / wssei 3.0.4 (sei.rr.gov.br) em 01/11/2026.
"""

from urllib.parse import parse_qs

import httpx
import pytest

from mcp_seipro.browser_transport import BrowserClient
from mcp_seipro.sei_client import SEIClient

CARGO = "Diretor do Departamento de Planejamento, Administração e Finanças"


def _cliente(**extra) -> SEIClient:
    return SEIClient(
        sei_url="https://sei.exemplo.gov.br/api/v2",
        sei_usuario="teste",
        sei_senha="ficticia",
        sei_orgao="1",
        permitir_arquivo_local=False,
        **extra,
    )


def _decodificar(corpo: bytes) -> dict:
    return {k: v[0] for k, v in parse_qs(corpo.decode("ascii"), encoding="iso-8859-1").items()}


def test_formulario_vira_iso_8859_1():
    c = _cliente()
    kwargs = {"data": {"cargo": CARGO, "documento": 123, "vazio": None}}
    c._codificar_formulario(kwargs)

    assert "data" not in kwargs
    assert kwargs["headers"]["Content-Type"].endswith("charset=ISO-8859-1")
    corpo = kwargs["content"]
    assert b"Administra%E7%E3o" in corpo  # ç e ã em um byte cada
    assert _decodificar(corpo) == {"cargo": CARGO, "documento": "123"}


def test_tipografia_fora_do_latin1_e_transliterada():
    c = _cliente()
    kwargs = {"data": {"descricao": "Resposta – “ARP” nº 074…"}}
    c._codificar_formulario(kwargs)
    assert _decodificar(kwargs["content"])["descricao"] == 'Resposta - "ARP" nº 074...'


def test_charset_utf8_mantem_comportamento_antigo():
    c = _cliente(sei_form_charset="utf-8")
    kwargs = {"data": {"cargo": CARGO}}
    c._codificar_formulario(kwargs)
    assert kwargs == {"data": {"cargo": CARGO}}


def test_multipart_nao_e_convertido_em_corpo_urlencoded():
    c = _cliente()
    kwargs = {"data": {"descricao": "Ofício"}, "files": {"anexo": ("a.pdf", b"x")}}
    c._codificar_formulario(kwargs)
    assert "content" not in kwargs
    assert c._campos_multipart({"descricao": "Ofício", "n": 1}) == {
        "descricao": "Ofício".encode("iso-8859-1"),
        "n": 1,
    }


@pytest.mark.asyncio
async def test_assinatura_envia_cargo_em_latin1_pelo_request():
    enviados = []

    def responder(request: httpx.Request) -> httpx.Response:
        enviados.append(request)
        return httpx.Response(200, json={"sucesso": True, "data": {"ok": 1}})

    c = _cliente()
    c._token = "token-ficticio"
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    try:
        await c.assinar_documento(
            id_documento="27113158", login="u", senha="s", cargo=CARGO,
            orgao="16", id_usuario="99",
        )
    finally:
        await c.close()

    req = enviados[0]
    assert req.headers["content-type"].endswith("charset=ISO-8859-1")
    assert _decodificar(req.content)["cargo"] == CARGO
    assert req.headers["token"] == "token-ficticio"


def test_browser_transport_usa_content_ja_serializado():
    corpo, ctype = BrowserClient._encode_body(b"cargo=Administra%E7%E3o", None)
    assert corpo == "cargo=Administra%E7%E3o"
    assert ctype == "application/x-www-form-urlencoded"


@pytest.mark.asyncio
@pytest.mark.parametrize("metodo,rota", [
    ("anotar_documento_bloco_assinatura", "/bloco/assinatura/anotacao/cadastrar"),
    ("alterar_anotacao_bloco_assinatura", "/bloco/assinatura/anotacao/alterar"),
])
async def test_anotacao_bloco_assinatura_usa_campo_anotacao(metodo, rota):
    # MdWsSeiServicosV2 lê `anotacao` (setStrAnotacao); `descricao` era ignorado.
    enviados = []

    def responder(request: httpx.Request) -> httpx.Response:
        enviados.append(request)
        return httpx.Response(200, json={"sucesso": True, "data": {}})

    c = _cliente()
    c._token = "t"
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    try:
        await getattr(c, metodo)("10", "20", "Anotação revisada")
    finally:
        await c.close()

    assert enviados[0].url.path.endswith(rota)
    f = _decodificar(enviados[0].content)
    assert f["anotacao"] == "Anotação revisada"
    assert "descricao" not in f
