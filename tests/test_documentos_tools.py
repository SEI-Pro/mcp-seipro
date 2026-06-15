"""Tests for documentos tool helpers, pure formatters, and validation branches.

Complements the routing harness: exercises the content formatters (with/without
the restricted-access disclaimer), the section-payload builder, the CSS-style
catalog tool, and the validation/error branches of the document tools — all
without a live SEI (backends are faked, PDF extraction is stubbed).
"""

from __future__ import annotations

import asyncio

import pytest

from todos import access_control
from todos.exceptions import DocumentoAssinadoError, SEIPermissionError
from todos.tools import assinatura as a
from todos.tools import documentos as d


def _disclaimer() -> dict:
    return access_control.construir_disclaimer_acompanhante(
        "1", "Art. 31 LAI", {"tipo": "documento", "id": "1"}
    )


# ---------------------------------------------------------------------------
# _formatar_doc_interno (pure, real HTML)
# ---------------------------------------------------------------------------


class TestFormatarDocInterno:
    def test_markdown_with_disclaimer(self) -> None:
        out = d._formatar_doc_interno("<p>texto <strong>x</strong></p>", "markdown", _disclaimer())
        assert "**x**" in out
        assert "ATENÇÃO" in out  # markdown disclaimer prefix

    def test_texto_with_disclaimer(self) -> None:
        out = d._formatar_doc_interno("<p>corpo</p>", "texto", _disclaimer())
        assert "corpo" in out
        assert "AVISO" in out

    def test_html_with_disclaimer_envelopes(self) -> None:
        out = d._formatar_doc_interno("<p>corpo</p>", "html", _disclaimer())
        assert "<aside" in out
        assert "<p>corpo</p>" in out

    def test_html_without_disclaimer_is_passthrough(self) -> None:
        assert d._formatar_doc_interno("<p>x</p>", "html", None) == "<p>x</p>"

    def test_markdown_without_disclaimer(self) -> None:
        assert "T" in d._formatar_doc_interno("<h1>T</h1>", "markdown", None)


# ---------------------------------------------------------------------------
# _formatar_doc_externo (size/type guards; PDF extraction stubbed)
# ---------------------------------------------------------------------------


class TestFormatarDocExterno:
    def test_non_pdf_errors(self) -> None:
        assert "não é PDF" in d._formatar_doc_externo(b"NOTPDF", "markdown", None)

    def test_too_large_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(d, "MAX_BINARY_SIZE", 3)
        assert "muito grande" in d._formatar_doc_externo(b"%PDF1234", "markdown", None)

    def test_markdown_with_disclaimer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(d, "pdf_to_markdown", lambda _c: "CORPO_PDF")
        out = d._formatar_doc_externo(b"%PDF-1.4", "markdown", _disclaimer())
        assert "CORPO_PDF" in out
        assert "ATENÇÃO" in out

    def test_texto_with_disclaimer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(d, "pdf_to_text", lambda _c: "TXT_PDF")
        out = d._formatar_doc_externo(b"%PDF-1.4", "texto", _disclaimer())
        assert "TXT_PDF" in out
        assert "AVISO" in out


# ---------------------------------------------------------------------------
# sei_estilos (pure catalog tool)
# ---------------------------------------------------------------------------


class TestEstilos:
    def test_default_returns_shortcuts(self) -> None:
        assert "atalhos" in asyncio.run(d.sei_estilos())

    def test_todos_returns_full_catalog(self) -> None:
        assert "Texto_Justificado" in asyncio.run(d.sei_estilos("todos"))

    @pytest.mark.parametrize("categoria", ["texto", "titulo", "lista", "tabela", "destaque"])
    def test_known_category_filters(self, categoria: str) -> None:
        assert asyncio.run(d.sei_estilos(categoria)).startswith("{")

    def test_unknown_category_errors(self) -> None:
        assert "não encontrada" in asyncio.run(d.sei_estilos("inexistente"))


# ---------------------------------------------------------------------------
# sei_editar_secao — full-payload builder (keeps unchanged sections, unescapes)
# ---------------------------------------------------------------------------


class _SecoesBackend:
    name = "fake"

    def __init__(self) -> None:
        self.sent: tuple[list[dict], str] | None = None

    async def listar_secoes(self, id_documento: str) -> dict:
        del id_documento
        return {
            "secoes": [
                {"id": "10", "idSecaoModelo": "1", "conteudo": "&lt;p&gt;antigo&lt;/p&gt;"},
                {"id": "11", "idSecaoModelo": "2", "conteudo": "manter"},
            ],
            "ultimaVersaoDocumento": "5",
        }

    async def alterar_secoes(self, id_documento: str, secoes: list[dict], versao: str) -> dict:
        del id_documento
        self.sent = (secoes, versao)
        return {"ok": True}


def test_editar_secao_builds_full_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _SecoesBackend()
    monkeypatch.setattr(d, "_backend", lambda _ctx: backend)

    asyncio.run(
        d.sei_editar_secao("D", [{"idSecaoModelo": "1", "conteudo": "<p>novo</p>"}], ctx=None)
    )
    assert backend.sent is not None
    secoes, versao = backend.sent
    assert versao == "5"  # picked up from ultimaVersaoDocumento
    by_modelo = {s["idSecaoModelo"]: s["conteudo"] for s in secoes}
    assert by_modelo["1"] == "<p>novo</p>"  # user-edited section
    assert "manter" in by_modelo["2"]  # untouched section preserved


# ---------------------------------------------------------------------------
# Validation branches (return before touching the backend)
# ---------------------------------------------------------------------------


class TestIncluirValidation:
    def test_base64_without_nome_arquivo(self) -> None:
        out = asyncio.run(d.sei_incluir_documento_externo("PF", arquivo_base64="eA==", ctx=None))
        assert "nome_arquivo é obrigatório" in out

    def test_invalid_base64(self) -> None:
        out = asyncio.run(
            d.sei_incluir_documento_externo(
                "PF", arquivo_base64="!!!", nome_arquivo="x.pdf", ctx=None
            )
        )
        assert "inválido" in out

    def test_id_serie_without_file_errors(self) -> None:
        out = asyncio.run(d.sei_incluir_documento_externo("PF", id_serie="S", ctx=None))
        assert "Informe arquivo_path" in out

    def test_remote_mode_blocks_server_file_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Security guard: in HTTP/remote mode a local path would point at the
        # server's filesystem, so arquivo_path must be rejected in favor of base64.
        monkeypatch.setattr(d, "_http_mode", True)
        out = asyncio.run(
            d.sei_incluir_documento_externo("PF", arquivo_path="/etc/passwd", ctx=None)
        )
        assert "modo remoto" in out


def test_criar_documento_requires_id_serie_in_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_has_rest", lambda _ctx: True)
    out = asyncio.run(d.sei_criar_documento("PF", id_serie="", ctx=None))
    assert "id_serie é obrigatório" in out


# ---------------------------------------------------------------------------
# Read tools: web-only guard, gate fail-closed surfacing, size limit, disclaimer
# ---------------------------------------------------------------------------


def test_ler_documento_web_only_requires_processo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", lambda _ctx: object())
    monkeypatch.setattr(d, "_has_rest", lambda _ctx: False)
    out = asyncio.run(d.sei_ler_documento("D", tipo_documento="I", processo=None, ctx=None))
    assert "forneça o parâmetro 'processo'" in out


class _GateErroBackend:
    name = "fake"

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        # Real backends translate "não autorizado" into this typed error whose
        # message no longer carries the substring — must be detected by type.
        msg = "Acesso ao documento negado."
        raise SEIPermissionError(msg)


def test_ler_documento_surfaces_nao_autorizado_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", lambda _ctx: _GateErroBackend())
    monkeypatch.setattr(d, "_has_rest", lambda _ctx: False)
    out = asyncio.run(d.sei_ler_documento("D", tipo_documento="I", processo="PF", ctx=None))
    assert "id INTERNO" in out


class _AnexoBackend:
    name = "fake"

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        return {"nivelAcesso": "0"}

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        del id_documento, processo
        return b"%PDF" + b"x" * 100


def test_baixar_anexo_too_large_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", lambda _ctx: _AnexoBackend())
    monkeypatch.setattr(d, "_has_rest", lambda _ctx: False)
    monkeypatch.setattr(d, "MAX_BINARY_SIZE", 10)
    out = asyncio.run(d.sei_baixar_anexo("D", processo="PF", ctx=None))
    assert "muito grande" in out


class _RestritoBackend:
    name = "fake"

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        return {"nivelAcesso": "1", "hipoteseLegal": "Art. 31"}


def test_consultar_documento_externo_attaches_aviso_for_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(d, "_backend", lambda _ctx: _RestritoBackend())
    out = asyncio.run(d.sei_consultar_documento_externo("D", processo="PF", ctx=None))
    assert "_aviso_acesso" in out


_NUMERO_SEI = "2867926"  # protocoloFormatado the user might pass by mistake
_ID_INTERNO = "3149544"  # the internal id it resolves to


class _ReconsultaBackend:
    name = "fake"

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del processo
        if id_documento == _NUMERO_SEI:  # número SEI → permission denied
            msg = "Acesso ao documento negado."
            raise SEIPermissionError(msg)
        return {"nivelAcesso": "0", "id": id_documento}


def test_consultar_documento_externo_recovers_via_reconsulta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression guard: a typed SEIPermissionError (translated message, no
    # "não autorizado" substring) must still trigger número SEI → id recovery.
    async def _fake_resolver(_client: object, _ref: str) -> tuple[str, str]:
        return _ID_INTERNO, "X"

    monkeypatch.setattr(d, "_backend", lambda _ctx: _ReconsultaBackend())
    monkeypatch.setattr(d, "_has_rest", lambda _ctx: True)
    monkeypatch.setattr(d, "_get_client", lambda _ctx: object())
    monkeypatch.setattr(d, "_resolver_documento", _fake_resolver)

    out = asyncio.run(d.sei_consultar_documento_externo(_NUMERO_SEI, ctx=None))
    assert _ID_INTERNO in out  # recovered and returned the resolved doc's metadata


# ---------------------------------------------------------------------------
# sei_cancelar_assinatura — detect the signed-lock by TYPE, not message
# ---------------------------------------------------------------------------


class _CancelarBackend:
    name = "fake"

    def __init__(self, *, locked: bool) -> None:
        self._locked = locked

    async def listar_secoes(self, id_documento: str) -> dict:
        del id_documento
        return {
            "secoes": [{"id": "1", "idSecaoModelo": "2", "conteudo": "x"}],
            "ultimaVersaoDocumento": "4",
        }

    async def alterar_secoes(self, id_documento: str, secoes: list[dict], versao: str) -> dict:
        del id_documento, secoes, versao
        if self._locked:
            msg = "Documento já assinado — edite pela interface web."
            raise DocumentoAssinadoError(msg)
        return {"versao": "5"}


def _patch_cancelar(monkeypatch: pytest.MonkeyPatch, backend: _CancelarBackend) -> None:
    async def _fake_resolver(_client: object, ref: str) -> tuple[str, str]:
        return ref, "I"

    monkeypatch.setattr(a, "_backend", lambda _ctx: backend)
    monkeypatch.setattr(a, "_get_client", lambda _ctx: object())
    monkeypatch.setattr(a, "_resolver_documento", _fake_resolver)


def test_cancelar_assinatura_reports_signed_lock_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    # The backend raises DocumentoAssinadoError; the tool must catch it by TYPE
    # and return the web-fallback guidance (not a generic error).
    _patch_cancelar(monkeypatch, _CancelarBackend(locked=True))
    out = asyncio.run(a.sei_cancelar_assinatura("D", ctx=None))
    assert "Não foi possível cancelar" in out
    assert "Editar Conteúdo" in out


def test_cancelar_assinatura_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cancelar(monkeypatch, _CancelarBackend(locked=False))
    out = asyncio.run(a.sei_cancelar_assinatura("D", ctx=None))
    assert "sucesso" in out
