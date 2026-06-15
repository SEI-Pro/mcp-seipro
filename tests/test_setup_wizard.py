"""Tests for the setup wizard helpers (idempotency guard + set-password)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from todos.setup_wizard import _compute_keyring_user, _read_existing_todos_env


@pytest.mark.parametrize(
    ("usuario", "sei_root", "expected"),
    [
        ("76450694220", "https://sei.sistemas.ro.gov.br", "76450694220@sei.sistemas.ro.gov.br"),
        ("user", "http://x.gov.br/", "user@x.gov.br"),
        ("user", "https://sei.gov.br/sei/", "user@sei.gov.br/sei"),
        ("user", "", "user"),  # sem URL → só o usuário
    ],
)
def test_compute_keyring_user(usuario: str, sei_root: str, expected: str) -> None:
    # Deve casar EXATAMENTE com a chave montada pelo client (mesma lógica:
    # host sem scheme minúsculo, strip, sem barra final, lower).
    assert _compute_keyring_user(usuario, sei_root) == expected


def test_read_existing_todos_env_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / ".claude.json"
    env = {"SEI_USUARIO": "76450694220", "SEI_WEB_URL": "https://sei.gov.br"}
    cfg.write_text(json.dumps({"mcpServers": {"todos": {"command": "x", "env": env}}}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _read_existing_todos_env() == env


def test_read_existing_todos_env_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # sem arquivo
    assert _read_existing_todos_env() is None
    # arquivo sem o server 'todos'
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"outro": {}}}))
    assert _read_existing_todos_env() is None
