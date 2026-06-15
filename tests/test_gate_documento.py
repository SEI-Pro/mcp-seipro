"""Tests for the backend-routed document access gate (`_aplicar_gate_documento`).

This is the privacy firewall after the composite migration: it consults document
metadata through the composite backend and decides liberar / bloquear / recusou /
erro. Previously this logic had no direct tests (flagged in code review); these
cover the decision matrix, the REST/web two-extractor nivel extraction, and the
fail-closed behavior on consult failure.

ctx is None throughout, so elicit is unavailable and a restricted doc without
prior consent deterministically blocks. No live SEI required.
"""

from __future__ import annotations

import asyncio

import pytest

from todos.backends.base import SEIBackend
from todos.exceptions import DocumentoNaoAutorizadoError, SEIError
from todos.mcp_app import _aplicar_gate_documento


class _GateBackend(SEIBackend):
    """Fake backend whose consult ops return canned metadata (or raise)."""

    name = "fake"

    def __init__(self, meta: dict | None = None, exc: Exception | None = None) -> None:
        self._meta = meta if meta is not None else {}
        self._exc = exc
        self.consulted: list[str] = []

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        self.consulted.append("externo")
        if self._exc is not None:
            raise self._exc
        return dict(self._meta)

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        self.consulted.append("interno")
        if self._exc is not None:
            raise self._exc
        return dict(self._meta)


def _gate(backend: SEIBackend, tipo: str = "X", *, confirmou: bool = False) -> tuple:
    return asyncio.run(
        _aplicar_gate_documento(None, backend, "123", tipo, "PROC", confirmou=confirmou)
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)


class TestGateDecisionMatrix:
    def test_public_liberates_without_disclaimer(self) -> None:
        acao, payload, erro = _gate(_GateBackend({"nivelAcesso": "0"}))
        assert acao == "liberar"
        assert payload is None
        assert erro == ""

    def test_restricted_without_consent_blocks(self) -> None:
        acao, payload, _ = _gate(_GateBackend({"nivelAcesso": "1"}))
        assert acao == "bloquear"
        assert payload is not None
        assert payload["tipo_resposta"] == "consentimento_pendente"

    def test_restricted_with_per_call_consent_liberates_with_disclaimer(self) -> None:
        acao, payload, _ = _gate(_GateBackend({"nivelAcesso": "1"}), confirmou=True)
        assert acao == "liberar"
        assert payload is not None
        assert payload["consentimento_necessario"] is False

    def test_restricted_with_env_consent_liberates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", "true")
        acao, payload, _ = _gate(_GateBackend({"nivelAcesso": "2"}))
        assert acao == "liberar"
        assert payload is not None


class TestGateNivelExtraction:
    def test_rest_shape_camelcase(self) -> None:
        acao, _, _ = _gate(_GateBackend({"nivelAcesso": "1"}))
        assert acao == "bloquear"

    def test_web_shape_accented_text_value(self) -> None:
        # extrair_nivel (REST keys) misses this; extrair_nivel_web must catch it.
        acao, _, _ = _gate(_GateBackend({"nível_de_acesso": "Restrito"}))
        assert acao == "bloquear"

    def test_internal_doc_consults_interno(self) -> None:
        backend = _GateBackend({"nivelAcesso": "0"})
        _gate(backend, tipo="I")
        assert backend.consulted == ["interno"]

    def test_external_doc_consults_externo(self) -> None:
        backend = _GateBackend({"nivelAcesso": "0"})
        _gate(backend, tipo="X")
        assert backend.consulted == ["externo"]


class TestGateFailClosed:
    def test_consult_failure_returns_erro_not_liberar(self) -> None:
        # Fail-closed: a metadata consult failure must NOT release content
        # ungated (the old web path failed open — this is the safer behavior).
        acao, payload, erro = _gate(_GateBackend(exc=SEIError("boom")))
        assert acao == "erro"
        assert payload is None
        assert "Falha ao consultar" in erro

    def test_permission_error_returns_id_hint(self) -> None:
        # The real type the backends raise: its translated message no longer
        # contains "não autorizado", so detection must be by TYPE, not substring
        # (the regression the migration introduced).
        exc = DocumentoNaoAutorizadoError("Acesso ao documento negado.")
        acao, _, erro = _gate(_GateBackend(exc=exc))
        assert acao == "erro"
        assert "id INTERNO" in erro

    def test_raw_nao_autorizado_message_still_detected(self) -> None:
        # An untranslated error that still carries the SEI text is also caught.
        acao, _, erro = _gate(_GateBackend(exc=SEIError("não autorizado")))
        assert acao == "erro"
        assert "id INTERNO" in erro
