"""Unit tests for `todos.wizard.server` — the web setup wizard (`todos setup`).

Covers the two adapters this module adds on top of `setup_wizard`'s
already-tested helpers (`_validate_login`, `_save_password`) — the parts
that drop the CLI wizard's Rich console output / `Confirm.ask`/`sys.exit`
side effects. Does not re-test `_detect_organs`/`_detect_modsei_url`/
`_build_mcp_env`/`_deploy_mcp_configs` (covered by `test_setup_wizard.py`
and `test_detect_modsei.py`) or spin up a real HTTP server — the handler
routes are thin dispatch wrappers around these two functions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from todos.exceptions import SEIAuthError, SEICredenciaisError
from todos.setup_wizard import _SEIConnConfig
from todos.wizard import server as wizard_server

if TYPE_CHECKING:
    import pytest


def _conn(**overrides: str | bool) -> _SEIConnConfig:
    base: dict[str, str | bool] = {
        "sei_root": "https://sei.example.gov.br",
        "usuario": "fulano",
        "senha": "secret",
        "sigla_orgao": "RO",
        "sigla_orgao_sistema": "",
        "sigla_sistema": "",
        "verify_ssl_disabled": False,
    }
    base.update(overrides)
    return _SEIConnConfig(**base)  # type: ignore[arg-type]


class _FakeWebClient:
    """Stand-in for SEIWebClient — scripted auth outcome, no real HTTP."""

    def __init__(self, config: object, *, outcome: str = "ok", unidade: dict | None = None) -> None:
        self._config = config
        self._outcome = outcome
        self._unidade = unidade or {}
        self.nome_usuario = "Fulano de Tal"
        self.closed = False
        self.senha_cleared = False

    async def ensure_authenticated(self) -> None:
        if self._outcome == "credenciais":
            msg = "usuário ou senha inválidos"
            raise SEICredenciaisError(msg)
        if self._outcome == "auth":
            msg = "sessão expirada"
            raise SEIAuthError(msg)
        if self._outcome == "boom":
            msg = "timeout de conexão"
            raise TimeoutError(msg)

    async def unidade_atual(self) -> dict:
        return self._unidade

    def limpar_senha(self) -> None:
        self.senha_cleared = True

    async def close(self) -> None:
        self.closed = True


def test_validate_login_success_reports_nome_and_unidade(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeWebClient(None, unidade={"sigla": "GAB1", "nome": "Gabinete 1"})
    monkeypatch.setattr(wizard_server, "SEIWebClient", lambda _config: fake)

    result = asyncio.run(wizard_server._validate_login(_conn()))

    assert result == {
        "ok": True,
        "nome": "Fulano de Tal",
        "unidade_sigla": "GAB1",
        "unidade_nome": "Gabinete 1",
    }
    assert fake.senha_cleared
    assert fake.closed


def test_validate_login_credenciais_error_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWebClient(None, outcome="credenciais")
    monkeypatch.setattr(wizard_server, "SEIWebClient", lambda _config: fake)

    result = asyncio.run(wizard_server._validate_login(_conn()))

    assert result["ok"] is False
    assert "Credenciais rejeitadas" in result["error"]
    assert fake.senha_cleared  # cleanup still happens on the failure path


def test_validate_login_auth_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeWebClient(None, outcome="auth")
    monkeypatch.setattr(wizard_server, "SEIWebClient", lambda _config: fake)

    result = asyncio.run(wizard_server._validate_login(_conn()))

    assert result["ok"] is False
    assert "Falha de autenticação" in result["error"]


def test_validate_login_unexpected_error_reported_generically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWebClient(None, outcome="boom")
    monkeypatch.setattr(wizard_server, "SEIWebClient", lambda _config: fake)

    result = asyncio.run(wizard_server._validate_login(_conn()))

    assert result["ok"] is False
    assert "Erro na validação" in result["error"]


def test_validate_login_unidade_fetch_failure_does_not_fail_the_whole_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful login must still report ok=True even if the follow-up
    unidade_atual() call fails — same tolerance as _validate_credentials."""

    class _NoUnidadeClient(_FakeWebClient):
        async def unidade_atual(self) -> dict:
            msg = "não foi possível obter a unidade"
            raise TimeoutError(msg)

    fake = _NoUnidadeClient(None)
    monkeypatch.setattr(wizard_server, "SEIWebClient", lambda _config: fake)

    result = asyncio.run(wizard_server._validate_login(_conn()))

    assert result["ok"] is True
    assert result["unidade_sigla"] == ""


def test_save_password_success(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        wizard_server._keyring,
        "set_password",
        lambda service, user, pwd: store.__setitem__((service, user), pwd),
    )

    ok, err = wizard_server._save_password("fulano@sei.example.gov.br", "secret")

    assert ok is True
    assert err == ""
    assert store[("todos-mcp", "fulano@sei.example.gov.br")] == "secret"


def test_save_password_failure_returns_error_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_service: str, _user: str, _pwd: str) -> None:
        msg = "keyring backend indisponível"
        raise RuntimeError(msg)

    monkeypatch.setattr(wizard_server._keyring, "set_password", _boom)

    ok, err = wizard_server._save_password("fulano@sei.example.gov.br", "secret")

    assert ok is False
    assert "keyring backend indisponível" in err
