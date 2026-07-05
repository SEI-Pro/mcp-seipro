"""Testes de `SEIWebClient.excluir_documento_web` (RFC 0022).

`excluir_documento_web` resolve a URL de exclusão pelo mesmo padrão `linkX`
já usado por `assinar_documento_web`/`_get_editor_montar_url`
(`_get_arvore_visualizar_link_var`, variável JS `linkExcluirDocumento` na
página `arvore_visualizar`) — não pelo caminho de reverse-engineering de
frames/estado-de-seleção que a RFC 0022 original cogitou.

Confirmado ao vivo em sei.sistemas.ro.gov.br, 2026-07-03/04 (processo
0016.004284/2026-81, documento 76861634): um único GET na URL resolvida por
`linkExcluirDocumento` já executa a exclusão de verdade.

Estes testes mockam `_arvore_do_processo`/`_get_arvore_visualizar_link_var`/
`client._http.get` (mesmo estilo de
`tests/test_sei_web_client_binary_doc_detection.py`) e cobrem:
- sucesso: link presente, GET ok, reconfirmação mostra o nó ausente;
- recusa por `confirmar=False` (nenhuma chamada HTTP é feita);
- recusa legítima do SEI (variável `linkExcluirDocumento` ausente);
- exclusão sem efeito: nó ainda presente após o GET — deve levantar erro,
  não reportar sucesso falso. O HTML "ausente" usado no teste de sucesso
  contém a string dos dígitos do id do documento excluído dentro do hash de
  OUTRO nó — de propósito, para provar que a checagem por
  `parse_arvore_nos`/comparação de `id` (não um `in html` ingênuo) é quem
  decide, exatamente o tipo de falso positivo relatado ao vivo nesta
  investigação.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIConnectionError, SEIParseError, SEIValidationError
from todos.sei_web_client import SEIWebClient

PROTOCOLO = "0016.004284/2026-81"
ID_DOCUMENTO = "76861634"

# Árvore com o documento-alvo (Nos[1]) e outro documento (Nos[2]) intacto.
_HTML_ARVORE_COM_DOCUMENTO = """
<script>
Nos[0] = new infraArvoreNo("PROCESSO","76635483",null,"controlador.php?acao=procedimento_visualizar","ifrVisualizacao","Processo 0016.004284/2026-81","Processo","svg/processo.svg?25");
Nos[1] = new infraArvoreNo("DOCUMENTO","76861634","76635483","controlador.php?acao=arvore_visualizar&acao_origem=procedimento_visualizar&id_procedimento=76635483&id_documento=76861634&infra_hash=aaa111","ifrConteudoVisualizacao","Anexo (74080657)","Anexo (74080657)","svg/documento_imagem.svg?25");
Nos[2] = new infraArvoreNo("DOCUMENTO","99999999","76635483","controlador.php?acao=arvore_visualizar&acao_origem=procedimento_visualizar&id_procedimento=76635483&id_documento=99999999&infra_hash=bbb222","ifrConteudoVisualizacao","Despacho GPF 99999999","Despacho GPF 99999999","svg/documento_gerado.svg?25");
</script>
"""

# Árvore após a exclusão: só sobra o outro documento. O hash de Nos[1]
# contém, por coincidência, os mesmos dígitos do id excluído (76861634) —
# um `in html` ingênuo acharia essa string e concluiria (errado) que o
# documento ainda está presente.
_HTML_ARVORE_SEM_DOCUMENTO = """
<script>
Nos[0] = new infraArvoreNo("PROCESSO","76635483",null,"controlador.php?acao=procedimento_visualizar","ifrVisualizacao","Processo 0016.004284/2026-81","Processo","svg/processo.svg?25");
Nos[1] = new infraArvoreNo("DOCUMENTO","99999999","76635483","controlador.php?acao=arvore_visualizar&acao_origem=procedimento_visualizar&id_procedimento=76635483&id_documento=99999999&infra_hash=76861634aaff","ifrConteudoVisualizacao","Despacho GPF 99999999","Despacho GPF 99999999","svg/documento_gerado.svg?25");
</script>
"""


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _resp(*, content: bytes = b"<html></html>", content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"content-type": content_type},
        request=httpx.Request("GET", "http://sei.test/x"),
    )


class TestExcluirDocumentoWebRecusaSemConfirmacao:
    def test_confirmar_false_raises_before_any_http_call(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()) as mock_auth,
            patch.object(client, "_arvore_do_processo", AsyncMock()) as mock_arvore,
            patch.object(client, "_get_arvore_visualizar_link_var", AsyncMock()) as mock_link,
            patch.object(client._http, "get", AsyncMock()) as mock_get,
            pytest.raises(SEIValidationError, match="destrutiva e irreversível"),
        ):
            asyncio.run(client.excluir_documento_web(PROTOCOLO, ID_DOCUMENTO, confirmar=False))

        mock_auth.assert_not_called()
        mock_arvore.assert_not_called()
        mock_link.assert_not_called()
        mock_get.assert_not_called()


class TestExcluirDocumentoWebSucesso:
    def test_success_deletes_and_reverifies_absence(self) -> None:
        client = make_client()
        arvore_calls = [
            (_HTML_ARVORE_COM_DOCUMENTO, "http://sei.test/arvore"),
            (_HTML_ARVORE_SEM_DOCUMENTO, "http://sei.test/arvore"),
        ]

        async def fake_arvore(_protocolo: str):
            return arvore_calls.pop(0)

        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_arvore_do_processo", fake_arvore),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    return_value=("http://sei.test/excluir", "http://sei.test/arvore_visualizar")
                ),
            ) as mock_link,
            patch.object(client._http, "get", AsyncMock(return_value=_resp())) as mock_get,
        ):
            result = asyncio.run(
                client.excluir_documento_web(PROTOCOLO, ID_DOCUMENTO, confirmar=True)
            )

        assert result == {
            "status": "ok",
            "id_documento": ID_DOCUMENTO,
            "processo": PROTOCOLO,
        }
        mock_link.assert_awaited_once_with(PROTOCOLO, ID_DOCUMENTO, "linkExcluirDocumento")
        mock_get.assert_awaited_once()
        assert mock_get.await_args is not None
        assert mock_get.await_args.args[0] == "http://sei.test/excluir"
        assert arvore_calls == []  # ambas as chamadas (antes/depois) consumidas


class TestExcluirDocumentoWebRecusaLegitimaDoSei:
    def test_link_var_missing_raises_validation_error(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client, "_arvore_do_processo", AsyncMock(return_value=(_HTML_ARVORE_COM_DOCUMENTO, "http://sei.test/arvore"))
            ),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    side_effect=SEIParseError(
                        f"Variável linkExcluirDocumento não encontrada para o documento "
                        f"{ID_DOCUMENTO} — verifique permissão neste documento."
                    )
                ),
            ),
            patch.object(client._http, "get", AsyncMock()) as mock_get,
            pytest.raises(SEIValidationError, match="SEI recusou a exclusão"),
        ):
            asyncio.run(client.excluir_documento_web(PROTOCOLO, ID_DOCUMENTO, confirmar=True))

        mock_get.assert_not_called()


class TestExcluirDocumentoWebSemEfeito:
    def test_node_still_present_after_delete_raises(self) -> None:
        client = make_client()

        async def fake_arvore(_protocolo: str):
            # Documento continua presente nas duas leituras (antes e depois
            # do GET de exclusão) — a exclusão não teve efeito real.
            return _HTML_ARVORE_COM_DOCUMENTO, "http://sei.test/arvore"

        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_arvore_do_processo", fake_arvore),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    return_value=("http://sei.test/excluir", "http://sei.test/arvore_visualizar")
                ),
            ),
            patch.object(client._http, "get", AsyncMock(return_value=_resp())),
            pytest.raises(SEIConnectionError, match="não teve efeito"),
        ):
            asyncio.run(client.excluir_documento_web(PROTOCOLO, ID_DOCUMENTO, confirmar=True))
