"""Tests for the backend-routed document access gate (`_aplicar_gate_documento`).

This is the privacy firewall after the composite migration: it consults document
metadata through the composite backend and decides liberar / bloquear / recusou.
Covers the decision matrix, the REST/web two-extractor nivel extraction, the
internal/external consult routing, and fail-closed-by-propagation (a consult
error is NOT swallowed — it propagates, so the read never happens).

ctx is None throughout, so elicit is unavailable and a restricted doc without
prior consent deterministically blocks. No live SEI required.
"""

from __future__ import annotations

import asyncio

import pytest

from todos.access_control import ConsentRecusadoError, GateBloqueadoError
from todos.backends.base import SEIBackend
from todos.exceptions import SEIDocumentoNaoAutorizadoError, SEIError
from todos.mcp_app import _aplicar_gate_documento, _DocumentoRef


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
    try:
        disclaimer = asyncio.run(
            _aplicar_gate_documento(
                None,
                backend,
                _DocumentoRef(id="123", tipo_documento=tipo, processo="PROC"),
                confirmou=confirmou,
            )
        )
    except ConsentRecusadoError as exc:
        return "recusou", exc.payload
    except GateBloqueadoError as exc:
        return "bloquear", exc.payload
    return "liberar", disclaimer


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)


class TestGateDecisionMatrix:
    def test_public_liberates_without_disclaimer(self) -> None:
        acao, payload = _gate(_GateBackend({"nivelAcesso": "0"}))
        assert acao == "liberar"
        assert payload is None

    def test_restricted_without_consent_blocks(self) -> None:
        acao, payload = _gate(_GateBackend({"nivelAcesso": "1"}))
        assert acao == "bloquear"
        assert payload is not None
        assert payload["tipo_resposta"] == "consentimento_pendente"

    def test_restricted_with_per_call_consent_liberates_with_disclaimer(self) -> None:
        acao, payload = _gate(_GateBackend({"nivelAcesso": "1"}), confirmou=True)
        assert acao == "liberar"
        assert payload is not None
        assert payload["consentimento_necessario"] is False

    def test_restricted_with_env_consent_liberates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", "true")
        acao, payload = _gate(_GateBackend({"nivelAcesso": "2"}))
        assert acao == "liberar"
        assert payload is not None


class TestGateNivelExtraction:
    def test_rest_shape_camelcase(self) -> None:
        acao, _ = _gate(_GateBackend({"nivelAcesso": "1"}))
        assert acao == "bloquear"

    def test_web_shape_accented_text_value(self) -> None:
        # extrair_nivel (REST keys) misses this; extrair_nivel_web must catch it.
        acao, _ = _gate(_GateBackend({"nível_de_acesso": "Restrito"}))
        assert acao == "bloquear"

    def test_internal_doc_consults_interno(self) -> None:
        backend = _GateBackend({"nivelAcesso": "0"})
        _gate(backend, tipo="I")
        assert backend.consulted == ["interno"]

    def test_external_doc_consults_externo(self) -> None:
        backend = _GateBackend({"nivelAcesso": "0"})
        _gate(backend, tipo="X")
        assert backend.consulted == ["externo"]


class TestGateFailClosedByPropagation:
    def test_consult_failure_propagates_not_liberates(self) -> None:
        # Fail-closed: a consult failure propagates (the read never runs) instead
        # of being swallowed into a 'liberar'.
        with pytest.raises(SEIError):
            _gate(_GateBackend(exc=SEIError("boom")))

    def test_specific_error_type_propagates(self) -> None:
        # The client raises the specific type at the source; the gate doesn't
        # catch or re-type it — it propagates by TYPE.
        exc = SEIDocumentoNaoAutorizadoError("Erro ao consultar documento 123: não autorizado")
        with pytest.raises(SEIDocumentoNaoAutorizadoError):
            _gate(_GateBackend(exc=exc))
