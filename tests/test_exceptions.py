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
