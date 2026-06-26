"""Tests for the centralized process configuration (``todos.settings``).

RFC 0016. No live SEI server required: ``TodosSettings`` is pure env-reading.
The cached accessor ``get_settings`` is cleared around each test so env
overrides take effect (the same recipe production tests should use).
"""

from __future__ import annotations

import pydantic
import pytest

from todos.backends.models import SEIClientConfig, SEIWebClientConfig
from todos.sei_client import SEIClient
from todos.sei_web_client import SEIWebClient
from todos.settings import TodosSettings, get_settings


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


# ---------------------------------------------------------------------------
# Phase 2 — typed parsers
# ---------------------------------------------------------------------------


def test_sei_max_sessions_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_MAX_SESSIONS: default 100; env var override; blank → default."""
    assert TodosSettings(_env_file=None).sei_max_sessions == 100
    monkeypatch.setenv("SEI_MAX_SESSIONS", "50")
    assert TodosSettings(_env_file=None).sei_max_sessions == 50
    monkeypatch.setenv("SEI_MAX_SESSIONS", "")
    assert TodosSettings(_env_file=None).sei_max_sessions == 100


def test_sei_cache_ttl_primary_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_CACHE_TTL_SECONDS sobrepõe o default None."""
    monkeypatch.setenv("SEI_CACHE_TTL_SECONDS", "3600")
    assert TodosSettings(_env_file=None).sei_cache_ttl_seconds == 3600


def test_sei_cache_ttl_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """CATALOG_CACHE_TTL é um alias legado — AliasChoices faz o fallback."""
    monkeypatch.delenv("SEI_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.setenv("CATALOG_CACHE_TTL", "7200")
    assert TodosSettings(_env_file=None).sei_cache_ttl_seconds == 7200


def test_sei_cache_ttl_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valor <= 0 levanta ValidationError."""
    monkeypatch.setenv("SEI_CACHE_TTL_SECONDS", "0")
    with pytest.raises(pydantic.ValidationError):
        TodosSettings(_env_file=None)


def test_sei_max_ocr_pages_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_MAX_OCR_PAGES <= 0 falha rápido em vez de OCRar 0 páginas silenciosamente."""
    for val in ("0", "-1"):
        monkeypatch.setenv("SEI_MAX_OCR_PAGES", val)
        with pytest.raises(pydantic.ValidationError):
            TodosSettings(_env_file=None)


def test_sei_max_sessions_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_MAX_SESSIONS <= 0 levanta ValidationError (semáforo com 0 trava tudo)."""
    monkeypatch.setenv("SEI_MAX_SESSIONS", "0")
    with pytest.raises(pydantic.ValidationError):
        TodosSettings(_env_file=None)


def test_sei_riscos_extra_pipe_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_RISCOS_EXTRA é guardado como string bruta; access_control faz o split."""
    monkeypatch.setenv("SEI_RISCOS_EXTRA", "risco A|art. 6, II LGPD|risco C")
    settings = TodosSettings(_env_file=None)
    assert settings.sei_riscos_extra == "risco A|art. 6, II LGPD|risco C"


def test_sei_riscos_extra_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_RISCOS_EXTRA vazio → string vazia."""
    monkeypatch.setenv("SEI_RISCOS_EXTRA", "")
    assert TodosSettings(_env_file=None).sei_riscos_extra == ""


def test_sei_permitir_restritos_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """'sim' e 'yes' habilitam (além de 'true' e '1')."""
    for val in ("sim", "Sim", "yes", "true", "TRUE", "1"):
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", val)
        assert TodosSettings(_env_file=None).sei_permitir_restritos is True, f"falhou para {val!r}"


def test_sei_permitir_restritos_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualquer valor não-truthy e ausência → False."""
    for val in ("false", "0", "no", "nao", ""):
        monkeypatch.setenv("SEI_PERMITIR_RESTRITOS", val)
        assert TodosSettings(_env_file=None).sei_permitir_restritos is False, f"falhou para {val!r}"
    monkeypatch.delenv("SEI_PERMITIR_RESTRITOS", raising=False)
    assert TodosSettings(_env_file=None).sei_permitir_restritos is False


def test_sei_hints_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEI_HINTS é guardado como string bruta; hints.get_hints() faz o parse JSON."""
    monkeypatch.setenv("SEI_HINTS", '["dica 1", "dica 2"]')
    assert TodosSettings(_env_file=None).sei_hints == '["dica 1", "dica 2"]'


def test_sei_hints_invalid_json_stored_as_raw_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON inválido é guardado como string bruta; get_hints() cai no default."""
    monkeypatch.setenv("SEI_HINTS", "nao-e-json")
    assert TodosSettings(_env_file=None).sei_hints == "nao-e-json"


# ---------------------------------------------------------------------------
# Phase 3 — import-time reads
# ---------------------------------------------------------------------------


def test_jwt_secret_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT_SECRET é lido via settings (Phase 3)."""
    env_value = "minha-chave-secreta-de-pelo-menos-32-chars!"
    monkeypatch.setenv("JWT_SECRET", env_value)
    assert TodosSettings(_env_file=None).jwt_secret == env_value
