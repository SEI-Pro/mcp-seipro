"""Tests for documentos tool helpers, pure formatters, and validation branches.

Complements the routing harness: exercises the content formatters (with/without
the restricted-access disclaimer), the section-payload builder, the CSS-style
catalog tool, and the validation/error branches of the document tools — all
without a live SEI (backends are faked, PDF extraction is stubbed).
"""

from __future__ import annotations

import asyncio

import pytest
from helpers import FakeCtx, aconst
from PIL import Image

from todos import access_control
from todos.exceptions import SEIError, SEINotImplementedError, SEIValidationError
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
        # Disclaimer must appear BEFORE the content
        assert out.index("ATENÇÃO") < out.index("**x**")
        # Check disclaimer contains access level and legal basis (not just the heading word)
        assert "Restrito" in out
        assert "LGPD" in out
        assert "Art. 31 LAI" in out

    def test_texto_with_disclaimer(self) -> None:
        out = d._formatar_doc_interno("<p>corpo</p>", "texto", _disclaimer())
        assert "corpo" in out
        # Disclaimer must appear BEFORE the content
        assert out.index("AVISO") < out.index("corpo")
        # Check disclaimer contains access level and legal basis
        assert "Restrito" in out
        assert "LGPD" in out
        assert "Art. 31 LAI" in out

    def test_html_with_disclaimer_envelopes(self) -> None:
        out = d._formatar_doc_interno("<p>corpo</p>", "html", _disclaimer())
        # <aside> wraps the disclaimer; content follows outside it
        assert "<aside" in out
        assert "</aside>" in out
        assert "<p>corpo</p>" in out
        # Disclaimer must appear BEFORE the content
        assert out.index("<aside") < out.index("<p>corpo</p>")
        # Check disclaimer contains substantive content
        assert "Restrito" in out
        assert "LGPD" in out

    def test_html_without_disclaimer_is_passthrough(self) -> None:
        assert d._formatar_doc_interno("<p>x</p>", "html", None) == "<p>x</p>"

    def test_markdown_without_disclaimer(self) -> None:
        assert "T" in d._formatar_doc_interno("<h1>T</h1>", "markdown", None)

    def test_pdf_bytes_misclassified_as_interno_raises(self) -> None:
        """Reproduz o bug: `_resolver_documento` classifica um documento externo (X)
        como interno ("I") sempre que `visualizar_documento_interno` devolve mais de
        alguns bytes, sem checar se o conteúdo é HTML de fato. Sem o guard de
        `_parece_binario`, o PDF bruto (ou corrompido por um decode com charset
        errado) seguiria intacto para `html_to_markdown` e vazaria como se fosse
        o teor do documento — em vez de um erro acionável.
        """
        pdf_like_raw = "%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n"
        with pytest.raises(SEIValidationError, match="binário"):
            d._formatar_doc_interno(pdf_like_raw, "markdown", None)

    def test_nul_byte_content_raises(self) -> None:
        """Bytes decodificados 1:1 (ex.: latin-1) preservam NUL — impossível em
        HTML/texto legítimo do SEI, então também deve disparar o guard."""
        with pytest.raises(SEIValidationError, match="binário"):
            d._formatar_doc_interno("lixo\x00binario\x00aqui", "markdown", None)


# ---------------------------------------------------------------------------
# _formatar_doc_externo (size/type guards; PDF extraction stubbed)
# ---------------------------------------------------------------------------


class TestFormatarDocExterno:
    def test_non_pdf_raises(self) -> None:
        with pytest.raises(SEIValidationError, match="não é PDF"):
            d._formatar_doc_externo(b"NOTPDF", "markdown", None)

    def test_too_large_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(d, "MAX_BINARY_SIZE", 3)
        with pytest.raises(SEIValidationError, match="muito grande"):
            d._formatar_doc_externo(b"%PDF1234", "markdown", None)

    def test_markdown_with_disclaimer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(d, "pdf_to_markdown", lambda _c: "CORPO_PDF")
        out = d._formatar_doc_externo(b"%PDF-1.4", "markdown", _disclaimer())
        assert "CORPO_PDF" in out
        # Disclaimer must appear BEFORE the content
        assert out.index("ATENÇÃO") < out.index("CORPO_PDF")
        # Check disclaimer contains substantive content, not just the heading word
        assert "Restrito" in out
        assert "LGPD" in out

    def test_texto_with_disclaimer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(d, "pdf_to_text", lambda _c: "TXT_PDF")
        out = d._formatar_doc_externo(b"%PDF-1.4", "texto", _disclaimer())
        assert "TXT_PDF" in out
        # Disclaimer must appear BEFORE the content
        assert out.index("AVISO") < out.index("TXT_PDF")
        # Check disclaimer contains substantive content
        assert "Restrito" in out
        assert "LGPD" in out


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

    def test_unknown_category_raises(self) -> None:
        with pytest.raises(SEIValidationError, match="não encontrada"):
            asyncio.run(d.sei_estilos("inexistente"))


# ---------------------------------------------------------------------------
# sei_editar_secao — full-payload builder (keeps unchanged sections, unescapes)
# ---------------------------------------------------------------------------


class _SecoesBackend:
    name = "fake"

    def __init__(self) -> None:
        self.sent: tuple[list[dict], str] | None = None

    def __getattr__(self, op: str) -> object:
        msg = f"_SecoesBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
        del id_documento, processo
        return {
            "secoes": [
                {"id": "10", "idSecaoModelo": "1", "conteudo": "&lt;p&gt;antigo&lt;/p&gt;"},
                {"id": "11", "idSecaoModelo": "2", "conteudo": "manter"},
            ],
            "ultimaVersaoDocumento": "5",
        }

    async def alterar_secoes(
        self, id_documento: str, secoes: list[dict], versao: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        self.sent = (secoes, versao)
        return {"ok": True}


def test_editar_secao_builds_full_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _SecoesBackend()
    monkeypatch.setattr(d, "_backend", aconst(backend))

    asyncio.run(
        d.sei_editar_secao(
            "D",
            [{"idSecaoModelo": "1", "conteudo": "<p>novo</p>"}],
            ctx=FakeCtx(),
            backend="web",
        )
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
        with pytest.raises(SEIValidationError, match="nome_arquivo é obrigatório"):
            asyncio.run(
                d.sei_incluir_documento_externo(
                    "PF", arquivo_base64="eA==", ctx=FakeCtx(), backend="web"
                )
            )

    def test_invalid_base64(self) -> None:
        with pytest.raises(SEIValidationError, match="inválido"):
            asyncio.run(
                d.sei_incluir_documento_externo(
                    "PF",
                    arquivo_base64="!!!",
                    nome_arquivo="x.pdf",
                    ctx=FakeCtx(),
                    backend="web",
                )
            )

    def test_id_serie_without_file_raises(self) -> None:
        with pytest.raises(SEIValidationError, match="Informe arquivo_path"):
            asyncio.run(
                d.sei_incluir_documento_externo("PF", id_serie="S", ctx=FakeCtx(), backend="web")
            )

    def test_remote_mode_blocks_server_file_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Security guard: in HTTP/remote mode a local path would point at the
        # server's filesystem, so arquivo_path must be rejected in favor of base64.
        monkeypatch.setattr(d, "_http_mode", True)
        with pytest.raises(SEIValidationError, match="modo remoto"):
            asyncio.run(
                d.sei_incluir_documento_externo(
                    "PF", arquivo_path="/etc/passwd", ctx=FakeCtx(), backend="web"
                )
            )

    def test_base64_with_backend_rest_raises_clear_error(self) -> None:
        # REST's criar_documento_externo reads a real file path off disk — it
        # has no concept of arquivo_base64. Without the old auto-routing (which
        # used to send base64 uploads to web silently), this must fail with an
        # actionable message instead of a confusing "Arquivo não encontrado".
        with pytest.raises(SEIValidationError, match="backend='rest' não suporta"):
            asyncio.run(
                d.sei_incluir_documento_externo(
                    "PF",
                    arquivo_base64="eA==",
                    nome_arquivo="x.pdf",
                    ctx=FakeCtx(),
                    backend="rest",
                )
            )


class _RequerIdSerieBackend:
    name = "fake"

    def __getattr__(self, op: str) -> object:
        msg = f"_RequerIdSerieBackend.{op} not implemented"
        raise NotImplementedError(msg)


def test_criar_documento_requires_id_serie_in_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", aconst(_RequerIdSerieBackend()))
    with pytest.raises(SEIValidationError, match="id_serie é obrigatório"):
        asyncio.run(d.sei_criar_documento("PF", id_serie="", ctx=FakeCtx(), backend="rest"))


# ---------------------------------------------------------------------------
# Read tools: web-only guard, gate fail-closed surfacing, size limit, disclaimer
# ---------------------------------------------------------------------------


class _WebOnlyBackend:
    name = "fake"

    def __getattr__(self, op: str) -> object:
        msg = f"_WebOnlyBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento
        if processo is None:
            msg = "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' para consultar metadados de documento."
            raise SEINotImplementedError(msg)
        return {"nivelAcesso": "0"}


def test_ler_documento_web_only_requires_processo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", aconst(_WebOnlyBackend()))
    with pytest.raises(Exception, match="forneça o parâmetro 'processo'"):
        asyncio.run(
            d.sei_ler_documento(
                "D", tipo_documento="I", processo=None, ctx=FakeCtx(), backend="web"
            )
        )


class _GateErroBackend:
    name = "fake"

    def __getattr__(self, op: str) -> object:
        msg = f"_GateErroBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        # The client raises a SEIError carrying the SEI message; it propagates
        # untouched (no translation), surfacing through the gate.
        msg = "documento não autorizado"
        raise SEIError(msg)


def test_ler_documento_propagates_consult_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", aconst(_GateErroBackend()))
    # Fail-closed by propagation: the gate's consult error propagates (the read
    # never runs); no tailored hint, no envelope.
    with pytest.raises(SEIError, match="não autorizado"):
        asyncio.run(
            d.sei_ler_documento(
                "D", tipo_documento="I", processo="PF", ctx=FakeCtx(), backend="web"
            )
        )


class _AnexoBackend:
    name = "fake"

    def __getattr__(self, op: str) -> object:
        msg = f"_AnexoBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def resolver_documento(self, referencia: str) -> tuple[str, str]:
        return referencia, "X"

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        return {"nivelAcesso": "0"}

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        del id_documento, processo
        return b"%PDF" + b"x" * 100


def test_baixar_anexo_too_large_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "_backend", aconst(_AnexoBackend()))
    monkeypatch.setattr(d, "MAX_BINARY_SIZE", 10)
    with pytest.raises(SEIValidationError, match="muito grande"):
        asyncio.run(d.sei_baixar_anexo("D", processo="PF", ctx=FakeCtx(), backend="web"))


class _RestritoBackend:
    name = "fake"

    def __getattr__(self, op: str) -> object:
        msg = f"_RestritoBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        return {"nivelAcesso": "1", "hipoteseLegal": "Art. 31"}


def test_consultar_documento_externo_attaches_aviso_for_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(d, "_backend", aconst(_RestritoBackend()))
    out = asyncio.run(
        d.sei_consultar_documento_externo("D", processo="PF", ctx=FakeCtx(), backend="web")
    )
    assert "_aviso_acesso" in out


class _ConsultaErroBackend:
    name = "fake"

    def __getattr__(self, op: str) -> object:
        msg = f"_ConsultaErroBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        msg = "documento não autorizado"
        raise SEIError(msg)


def test_consultar_documento_externo_propagates_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No translation / no auto-recovery: the SEIError (a ToolError) propagates
    # with the SEI's own message.
    monkeypatch.setattr(d, "_backend", aconst(_ConsultaErroBackend()))
    with pytest.raises(SEIError, match="não autorizado"):
        asyncio.run(
            d.sei_consultar_documento_externo("D", processo="PF", ctx=FakeCtx(), backend="web")
        )


# ---------------------------------------------------------------------------
# sei_cancelar_assinatura — original SEI error propagates when the doc is locked
# ---------------------------------------------------------------------------


class _CancelarBackend:
    name = "fake"

    def __init__(self, *, locked: bool) -> None:
        self._locked = locked

    def __getattr__(self, op: str) -> object:
        msg = f"_CancelarBackend.{op} not implemented"
        raise NotImplementedError(msg)

    async def resolver_documento(self, referencia: str) -> tuple[str, str]:
        return referencia, "I"

    async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
        del id_documento, processo
        return {
            "secoes": [{"id": "1", "idSecaoModelo": "2", "conteudo": "x"}],
            "ultimaVersaoDocumento": "4",
        }

    async def alterar_secoes(
        self, id_documento: str, secoes: list[dict], versao: str, processo: str | None = None
    ) -> dict:
        del id_documento, secoes, versao, processo
        if self._locked:
            # The SEIClient raises this with the SEI's own message; it propagates.
            msg = "Erro ao alterar documento: documento já assinado."
            raise SEIError(msg)
        return {"versao": "5"}


def _patch_cancelar(monkeypatch: pytest.MonkeyPatch, backend: _CancelarBackend) -> None:
    monkeypatch.setattr(a, "_backend", aconst(backend))


def test_cancelar_assinatura_propagates_lock_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Locked document ⇒ the SEIError (a ToolError) propagates with the SEI
    # message — no JSON envelope, no translation.
    _patch_cancelar(monkeypatch, _CancelarBackend(locked=True))
    with pytest.raises(SEIError, match="assinado"):
        asyncio.run(a.sei_cancelar_assinatura("D", ctx=FakeCtx(), backend="web"))


def test_cancelar_assinatura_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cancelar(monkeypatch, _CancelarBackend(locked=False))
    out = asyncio.run(a.sei_cancelar_assinatura("D", ctx=FakeCtx(), backend="web"))
    assert "sucesso" in out


# ---------------------------------------------------------------------------
# sei_preparar_imagem_para_embed (pure local utility, no backend — RFC 0024)
# ---------------------------------------------------------------------------


class TestPrepararImagemParaEmbed:
    def test_monta_data_uri_e_sugere_editar_secao(self, tmp_path) -> None:
        caminho = tmp_path / "print.png"
        Image.new("RGB", (50, 50), color=(10, 20, 30)).save(caminho, "PNG")

        out = asyncio.run(d.sei_preparar_imagem_para_embed(str(caminho)))

        prefixo = "data:image/jpeg;base64,"
        assert out["data_uri"].startswith(prefixo)
        # tamanho_base64_chars mede só o payload base64 (o que RFC 0024's
        # max_base64_chars realmente limita) — NÃO o data_uri inteiro, que
        # inclui o prefixo "data:image/jpeg;base64," e inflaria a métrica.
        base64_payload = out["data_uri"].removeprefix(prefixo)
        assert out["tamanho_base64_chars"] == len(base64_payload)
        assert out["tamanho_base64_chars"] < len(out["data_uri"])
        assert out["_next"][0]["tool"] == "sei_editar_secao"

    def test_extensao_nao_permitida_levanta_erro(self, tmp_path) -> None:
        caminho = tmp_path / "arquivo.txt"
        caminho.write_text("não é imagem")

        with pytest.raises(SEIValidationError, match="não permitida"):
            asyncio.run(d.sei_preparar_imagem_para_embed(str(caminho)))
