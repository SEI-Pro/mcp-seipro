"""Tests for the source-side error classifier `erro_do_sei`.

The client/scraper calls `raise erro_do_sei(contexto, mensagem)` at the point it
reads the SEI response — classifying the raw message ONCE, at the origin, into a
specific typed exception. Downstream code then catches by TYPE. These tests
assert the type, never a message substring.
"""

from __future__ import annotations

import pytest

from todos.exceptions import (
    SEIDocumentoAssinadoError,
    SEIDocumentoNaoAutorizadoError,
    SEIError,
    SEIPermissionError,
    SEIProcessoEmOutraUnidadeError,
    SEIValidationError,
    erro_do_sei,
)


@pytest.mark.parametrize(
    "mensagem",
    ["Acesso não autorizado", "documento nao autorizado", "Acesso negado ao documento"],
)
def test_nao_autorizado_classified_as_permission(mensagem: str) -> None:
    erro = erro_do_sei("Erro ao consultar documento 1", mensagem)
    assert isinstance(erro, SEIDocumentoNaoAutorizadoError)
    assert isinstance(erro, SEIPermissionError)  # category


def test_assinado_classified_as_validation() -> None:
    erro = erro_do_sei("Erro ao alterar documento", "Documento já assinado")
    assert isinstance(erro, SEIDocumentoAssinadoError)
    assert isinstance(erro, SEIValidationError)


def test_aberto_em_outra_unidade_classified() -> None:
    erro = erro_do_sei("Erro ao concluir processo", "Processo aberto na unidade SFC")
    assert isinstance(erro, SEIProcessoEmOutraUnidadeError)
    assert isinstance(erro, SEIValidationError)


def test_unknown_message_falls_back_to_base() -> None:
    erro = erro_do_sei("Erro ao listar", "falha genérica qualquer")
    assert type(erro) is SEIError  # exactly the base, not a specific subtype


def test_context_and_message_in_text() -> None:
    erro = erro_do_sei("Erro ao consultar documento 42", "Acesso não autorizado")
    assert "consultar documento 42" in str(erro)
    assert "não autorizado" in str(erro)


def test_empty_message_uses_context_only() -> None:
    erro = erro_do_sei("Erro ao consultar", None)
    assert str(erro) == "Erro ao consultar"


# ---------------------------------------------------------------------------
# Phase 4 — recoverable errors with continuation
# ---------------------------------------------------------------------------


def test_sei_error_without_suggestion_renders_plain_message() -> None:
    err = SEIError("algo falhou")
    assert str(err) == "algo falhou"
    assert err.error_code == ""
    assert err.recoverable is False
    assert err.suggested_next_tool is None
    assert err.suggested_args == {}


def test_sei_error_with_suggestion_embeds_continuation_in_message() -> None:
    err = SEIValidationError(
        "Unidade 'GPF' não encontrada.",
        error_code="UNIDADE_NAO_ENCONTRADA",
        recoverable=True,
        suggested_next_tool="sei_pesquisar_unidades",
        suggested_args={"filtro": "GPF"},
    )
    rendered = str(err)
    assert "Unidade 'GPF' não encontrada." in rendered
    assert "sei_pesquisar_unidades" in rendered
    assert '"filtro"' in rendered
    assert '"GPF"' in rendered
    assert err.error_code == "UNIDADE_NAO_ENCONTRADA"
    assert err.recoverable is True


def test_sei_error_structured_attrs_preserved_on_object() -> None:
    err = SEIError(
        "sessão expirada",
        error_code="SESSAO_EXPIRADA",
        recoverable=False,
        suggested_next_tool="sei_status",
        suggested_args={},
    )
    assert err.error_code == "SESSAO_EXPIRADA"
    assert err.recoverable is False
    assert err.suggested_next_tool == "sei_status"
    assert err.suggested_args == {}


def test_sei_error_message_contains_no_stack_trace() -> None:
    err = SEIError(
        "Documento '123' não encontrado.",
        error_code="DOCUMENTO_NAO_INDEXADO",
        recoverable=True,
        suggested_next_tool="sei_arvore_processo",
        suggested_args={"protocolo_formatado": ""},
    )
    rendered = str(err)
    assert "Traceback" not in rendered
    assert "File " not in rendered
    assert "sei_arvore_processo" in rendered


def test_sei_error_empty_args_renders_empty_object() -> None:
    err = SEIError(
        "falha",
        suggested_next_tool="sei_status",
        suggested_args={},
    )
    assert "{}" in str(err)
