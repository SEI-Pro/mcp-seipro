"""sei_gerar_pdf_processo/sei_gerar_zip_processo must not embed a huge base64
blob in the MCP tool response by default.

Regression context: a 14.7 MB PDF (~19.6 MB base64) returned inline through
sei_gerar_pdf_processo closed the whole MCP stdio connection ("Connection
closed", not a catchable tool error) — the entire server had to reconnect,
losing session state. The file was already saved to disk either way
(`arquivo`); base64 is now opt-in (`incluir_base64=True`) and capped at
_MAX_INLINE_BASE64_MB regardless.
"""

from __future__ import annotations

import asyncio
import base64 as b64
import json

import pytest

from todos.exceptions import SEIValidationError
from todos.tools import processos
from todos.tools.processos import _MAX_INLINE_BASE64_MB, _salvar_arquivo_temp


class _FakeBackend:
    """Minimal stand-in exposing only gerar_pdf_processo/gerar_zip_processo."""

    def __init__(self, conteudo: bytes) -> None:
        self._conteudo = conteudo

    async def gerar_pdf_processo(self, _protocolo: str) -> bytes:
        return self._conteudo

    async def gerar_zip_processo(self, _protocolo: str) -> bytes:
        return self._conteudo


def _aconst(v: object):
    async def _f(_ctx: object) -> object:
        return v

    return _f


class TestSalvarArquivoTempBase64Default:
    def test_base64_omitted_by_default(self) -> None:
        result = _salvar_arquivo_temp(
            "50300.000123/2025-00", b"pequeno", "pdf", 50.0, incluir_base64=False
        )
        assert "base64" not in result
        assert "arquivo" in result
        assert result["tamanho_bytes"] == len(b"pequeno")

    def test_base64_included_when_requested_and_small(self) -> None:
        conteudo = b"pequeno o bastante"
        result = _salvar_arquivo_temp(
            "50300.000123/2025-00", conteudo, "pdf", 50.0, incluir_base64=True
        )
        assert "base64" in result
        assert b64.b64decode(result["base64"]) == conteudo

    def test_base64_request_raises_for_large_file_even_under_max_mb(self) -> None:
        """A file can pass the overall max_mb guard (e.g. 50 MB PDF cap) while
        still being far too large to embed inline — the two limits are
        independent."""
        tamanho = int((_MAX_INLINE_BASE64_MB + 1) * 1024 * 1024)
        conteudo = b"x" * tamanho
        with pytest.raises(SEIValidationError, match="grande demais para incluir inline"):
            _salvar_arquivo_temp("50300.000123/2025-00", conteudo, "pdf", 50.0, incluir_base64=True)

    def test_overall_max_mb_guard_still_applies_regardless_of_incluir_base64(self) -> None:
        conteudo = b"x" * (1024 * 1024)
        with pytest.raises(SEIValidationError, match="muito grande"):
            _salvar_arquivo_temp(
                "50300.000123/2025-00", conteudo, "pdf", max_mb=0.5, incluir_base64=False
            )


class TestSeiGerarPdfProcessoTool:
    def test_default_response_has_no_base64_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeBackend(b"%PDF-1.4 conteudo de teste")
        monkeypatch.setattr(processos, "_backend", _aconst(fake))

        raw = asyncio.run(processos.sei_gerar_pdf_processo("50300.000123/2025-00", ctx=None))
        payload = json.loads(raw)
        assert "base64" not in payload
        assert "arquivo" in payload
        assert payload["tamanho_bytes"] == len(b"%PDF-1.4 conteudo de teste")

    def test_incluir_base64_true_adds_the_field_for_small_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeBackend(b"%PDF-1.4 pequeno")
        monkeypatch.setattr(processos, "_backend", _aconst(fake))

        raw = asyncio.run(
            processos.sei_gerar_pdf_processo("50300.000123/2025-00", ctx=None, incluir_base64=True)
        )
        payload = json.loads(raw)
        assert "base64" in payload


class TestSeiGerarZipProcessoTool:
    def test_default_response_has_no_base64_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeBackend(b"PK\x03\x04 conteudo de teste")
        monkeypatch.setattr(processos, "_backend", _aconst(fake))

        raw = asyncio.run(processos.sei_gerar_zip_processo("50300.000123/2025-00", ctx=None))
        payload = json.loads(raw)
        assert "base64" not in payload
        assert "arquivo" in payload

    def test_incluir_base64_true_adds_the_field_for_small_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeBackend(b"PK\x03\x04 pequeno")
        monkeypatch.setattr(processos, "_backend", _aconst(fake))

        raw = asyncio.run(
            processos.sei_gerar_zip_processo("50300.000123/2025-00", ctx=None, incluir_base64=True)
        )
        payload = json.loads(raw)
        assert "base64" in payload
