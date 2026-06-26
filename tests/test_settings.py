"""Tests for the centralized process configuration (``todos.settings``).

RFC 0016. No live SEI server required: ``TodosSettings`` is pure env-reading.
The cached accessor ``get_settings`` is cleared around each test so env
overrides take effect (the same recipe production tests should use).
"""

from __future__ import annotations

import pytest

from todos.backends.models import SEIClientConfig, SEIWebClientConfig
from todos.sei_client import SEIClient
from todos.sei_web_client import SEIWebClient
from todos.settings import TodosSettings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Drop the cached settings before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem env nem .env, os defaults documentados valem."""
    for var in ("SEI_URL", "SEI_WEB_URL", "SEI_USUARIO", "SEI_SENHA", "SEI_ORGAO"):
        monkeypatch.delenv(var, raising=False)
    settings = TodosSettings(_env_file=None)
    assert settings.sei_url == ""
    assert settings.sei_orgao == "0"
    assert settings.sei_verify_ssl is True
    assert settings.sei_sigla_orgao == "ANTAQ"
    assert settings.sei_sigla_sistema == "SEI"


def test_reads_env_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os campos espelham as env vars com prefixo SEI_."""
    monkeypatch.setenv("SEI_URL", "https://sei.exemplo.gov.br/api/v2")
    monkeypatch.setenv("SEI_USUARIO", "fulano")
    settings = TodosSettings(_env_file=None)
    assert settings.sei_url == "https://sei.exemplo.gov.br/api/v2"
    assert settings.sei_usuario == "fulano"


def test_verify_ssl_parsed_as_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_VERIFY_SSL: só o literal ``false`` desabilita; o resto mantém ligado."""
    monkeypatch.setenv("SEI_VERIFY_SSL", "false")
    assert TodosSettings(_env_file=None).sei_verify_ssl is False
    monkeypatch.setenv("SEI_VERIFY_SSL", "FALSE")
    assert TodosSettings(_env_file=None).sei_verify_ssl is False
    monkeypatch.setenv("SEI_VERIFY_SSL", "true")
    assert TodosSettings(_env_file=None).sei_verify_ssl is True


def test_verify_ssl_blank_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_VERIFY_SSL='' (vazio) não levanta ValidationError; vale o default ligado."""
    monkeypatch.setenv("SEI_VERIFY_SSL", "")
    assert TodosSettings(_env_file=None).sei_verify_ssl is True


def test_explicit_config_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """O merge field-level: config explícita tem precedência sobre o ambiente."""
    monkeypatch.setenv("SEI_USUARIO", "do_ambiente")
    get_settings.cache_clear()
    client = SEIClient(SEIClientConfig(sei_usuario="explicito"))
    assert client._usuario == "explicito"


def test_client_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config vazia → o cliente resolve via settings/ambiente."""
    monkeypatch.setenv("SEI_USUARIO", "do_ambiente")
    monkeypatch.setenv("SEI_URL", "https://sei.exemplo.gov.br/sei/api/v2")
    get_settings.cache_clear()
    rest = SEIClient(SEIClientConfig())
    assert rest._usuario == "do_ambiente"
    web = SEIWebClient(SEIWebClientConfig())
    assert web._usuario == "do_ambiente"
