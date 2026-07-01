"""visualizar_documento_interno_web/baixar_documento_externo_web must not treat
raw binary content as HTML.

Regression context (2026-07-01): some external attachments (a mandado
imported from PJe, an acórdão imported from another tribunal) are served by
SEI's own `documento_visualizar` action as the raw PDF bytes directly — the
browser displays them inline — instead of an HTML wrapper page, and have no
`documento_download_anexo` link in the process tree at all. Before this fix,
`sei_ler_documento(tipo_documento='auto')` treated that raw PDF as if it were
HTML and ran it through the HTML parser (`_extrair_erro_sei`/
`html_to_markdown`), which normalizes `\r\n`/`\r` to `\n` — irreversibly
corrupting the FlateDecode-compressed streams inside the PDF from the first
`\r` in each stream onward. And once detected as "not internal", falling back
to `baixar_documento_externo_web` failed too, since these documents have no
`documento_download_anexo` action in the tree ("Ação ... não encontrada").
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIConnectionError, SEINotImplementedError, SEIParseError
from todos.sei_web_client import SEIWebClient

_REAL_PDF_PREFIX = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _resp(*, content: bytes, content_type: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        headers={"content-type": content_type},
        request=httpx.Request("GET", "http://sei.test/x"),
    )


class TestVisualizarDocumentoInternoWebDetectsBinary:
    def test_raises_not_implemented_when_body_is_a_pdf(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_doc_signed_url",
                AsyncMock(return_value=("http://sei.test/x", "http://sei.test/ref")),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(content=_REAL_PDF_PREFIX, content_type="application/pdf")
                ),
            ),
            pytest.raises(SEINotImplementedError, match="não é um documento interno"),
        ):
            asyncio.run(client.visualizar_documento_interno_web("50300.000123/2025-00", "1"))

    def test_raises_not_implemented_when_content_type_is_neither_html_nor_text(self) -> None:
        """Even without the %PDF magic bytes, an unexpected content-type is
        enough to refuse — defense in depth for other binary formats."""
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_doc_signed_url",
                AsyncMock(return_value=("http://sei.test/x", "http://sei.test/ref")),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(return_value=_resp(content=b"\x89PNG\r\n", content_type="image/png")),
            ),
            pytest.raises(SEINotImplementedError),
        ):
            asyncio.run(client.visualizar_documento_interno_web("50300.000123/2025-00", "1"))

    def test_returns_html_for_a_genuine_internal_document(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_doc_signed_url",
                AsyncMock(return_value=("http://sei.test/x", "http://sei.test/ref")),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(
                        content=b"<html><body><p>Despacho de verdade</p></body></html>",
                        content_type="text/html; charset=ISO-8859-1",
                    )
                ),
            ),
        ):
            html = asyncio.run(client.visualizar_documento_interno_web("50300.000123/2025-00", "1"))
        assert "Despacho de verdade" in html


class TestBaixarDocumentoExternoWebFallsBackToVisualizar:
    def test_falls_back_to_documento_visualizar_when_download_anexo_action_missing(
        self,
    ) -> None:
        client = make_client()
        signed_url_calls: list[str] = []

        async def fake_get_doc_signed_url(_protocolo: str, _id: str, acao: str):
            signed_url_calls.append(acao)
            if acao == "documento_download_anexo":
                msg = f"Ação '{acao}' não encontrada para o documento 1 na árvore do processo x."
                raise SEIParseError(msg)
            return "http://sei.test/visualizar", "http://sei.test/ref"

        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_get_doc_signed_url", fake_get_doc_signed_url),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(content=_REAL_PDF_PREFIX, content_type="application/pdf")
                ),
            ),
        ):
            content = asyncio.run(client.baixar_documento_externo_web("50300.000123/2025-00", "1"))

        assert content == _REAL_PDF_PREFIX
        assert signed_url_calls == ["documento_download_anexo", "documento_visualizar"]

    def test_does_not_fall_back_when_download_anexo_action_exists(self) -> None:
        client = make_client()
        signed_url_calls: list[str] = []

        async def fake_get_doc_signed_url(_protocolo: str, _id: str, acao: str):
            signed_url_calls.append(acao)
            return "http://sei.test/anexo", "http://sei.test/ref"

        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_get_doc_signed_url", fake_get_doc_signed_url),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(content=_REAL_PDF_PREFIX, content_type="application/pdf")
                ),
            ),
        ):
            content = asyncio.run(client.baixar_documento_externo_web("50300.000123/2025-00", "1"))

        assert content == _REAL_PDF_PREFIX
        assert signed_url_calls == ["documento_download_anexo"]

    def test_still_raises_on_a_genuine_error_page(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_doc_signed_url",
                AsyncMock(return_value=("http://sei.test/anexo", "http://sei.test/ref")),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(
                        content=b'<html><div class="infraMsg">Sessao expirada</div></html>',
                        content_type="text/html",
                    )
                ),
            ),
            pytest.raises(SEIConnectionError),
        ):
            asyncio.run(client.baixar_documento_externo_web("50300.000123/2025-00", "1"))
