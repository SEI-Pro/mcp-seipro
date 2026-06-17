"""Keyring re-read on credential rejection (no restart needed when the password changes)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import keyring as _keyring

from todos.backends.models import SEIWebClientConfig
from todos.sei_web_client import SEIWebClient

if TYPE_CHECKING:
    import pytest


def _client(senha: str = "") -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="https://sei.gov.br", sei_usuario="u", sei_senha=senha)
    )


def test_keyring_user_persist_set_when_no_password() -> None:
    # Senha vazia → keyring é a fonte → guarda a chave persistente p/ releitura.
    assert _client()._keyring_user_persist == "u@sei.gov.br"


def test_keyring_user_persist_none_when_password_given() -> None:
    # Senha explícita → não usa keyring → sem chave persistente.
    assert _client("x")._keyring_user_persist is None


def test_ler_senha_keyring_reads_current_value(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_get(service: str, user: str) -> str | None:
        captured["args"] = (service, user)
        return "nova-senha" if user == "u@sei.gov.br" else None

    monkeypatch.setattr(_keyring, "get_password", fake_get)
    got = asyncio.run(_client()._ler_senha_keyring("u@sei.gov.br"))
    assert got == "nova-senha"
    assert captured["args"] == ("todos-mcp", "u@sei.gov.br")
