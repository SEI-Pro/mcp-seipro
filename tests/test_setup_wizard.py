"""Tests for the setup wizard helpers (idempotency guard + set-password)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from todos import setup_wizard as sw
from todos.exceptions import SEIAuthError, SEICredenciaisError
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


# ---------------------------------------------------------------------------
# Chave(s) da API Gemini (opcional, para sei_analisar_processo)
# ---------------------------------------------------------------------------


class TestPromptGeminiKey:
    def test_vazio_pula_e_retorna_dict_vazio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sw.Prompt, "ask", lambda *_args, **_kwargs: "")
        assert sw._prompt_gemini_key() == {}

    def test_uma_key_vai_em_gemini_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sw.Prompt, "ask", lambda *_args, **_kwargs: "  minha-key-123  ")
        assert sw._prompt_gemini_key() == {"GEMINI_API_KEY": "minha-key-123"}

    def test_varias_keys_separadas_por_virgula_vao_em_gemini_api_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sw.Prompt, "ask", lambda *_args, **_kwargs: "key1, key2 ,, key3")
        assert sw._prompt_gemini_key() == {"GEMINI_API_KEYS": "key1,key2,key3"}


class TestBuildMcpEnvExtra:
    def test_sem_extra_env_nao_adiciona_chaves_gemini(self) -> None:
        mcp_env, _plaintext = sw._build_mcp_env(_inst(), "fulano", "secret")
        assert "GEMINI_API_KEY" not in mcp_env
        assert "GEMINI_API_KEYS" not in mcp_env

    def test_extra_env_e_mesclado_no_mcp_env(self) -> None:
        mcp_env, _plaintext = sw._build_mcp_env(
            _inst(), "fulano", "secret", extra_env={"GEMINI_API_KEY": "abc"}
        )
        assert mcp_env["GEMINI_API_KEY"] == "abc"
        assert mcp_env["SEI_USUARIO"] == "fulano"


# ---------------------------------------------------------------------------
# Provisionamento headless (RFC 0019 §2.3)
# ---------------------------------------------------------------------------


class _FakeWebClient:
    """Stand-in para SEIWebClient — outcome de login roteirizado, sem HTTP real."""

    def __init__(self, _config: object, *, outcome: str = "ok") -> None:
        self._outcome = outcome
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

    def limpar_senha(self) -> None:
        self.senha_cleared = True

    async def close(self) -> None:
        self.closed = True


def _conn(**overrides: object) -> sw._SEIConnConfig:
    base: dict[str, object] = {
        "sei_root": "https://sei.example.gov.br",
        "usuario": "fulano",
        "senha": "secret",
        "sigla_orgao": "RO",
        "sigla_orgao_sistema": "",
        "sigla_sistema": "",
        "verify_ssl_disabled": False,
    }
    base.update(overrides)
    return sw._SEIConnConfig(**base)  # type: ignore[arg-type]


def _inst(**overrides: object) -> sw._SEIInstanceConfig:
    base: dict[str, object] = {
        "sei_root": "https://sei.example.gov.br",
        "rest_url": "",
        "sigla_orgao": "RO",
        "sigla_orgao_sistema": "",
        "sigla_sistema": "",
        "orgao_id": "0",
        "verify_ssl_disabled": False,
    }
    base.update(overrides)
    return sw._SEIInstanceConfig(**base)  # type: ignore[arg-type]


class TestValidateCredentialsHeadless:
    def test_success_does_not_raise_and_clears_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeWebClient(None)
        monkeypatch.setattr(sw, "SEIWebClient", lambda _config: fake)

        sw._validate_credentials_headless(_conn())

        assert fake.senha_cleared
        assert fake.closed

    def test_credenciais_rejeitadas_aborta_sem_prompt(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeWebClient(None, outcome="credenciais")
        monkeypatch.setattr(sw, "SEIWebClient", lambda _config: fake)

        with pytest.raises(SystemExit) as exc_info:
            sw._validate_credentials_headless(_conn())

        assert exc_info.value.code == 1
        assert "Credenciais rejeitadas" in capsys.readouterr().err
        assert fake.senha_cleared

    def test_falha_autenticacao_aborta(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeWebClient(None, outcome="auth")
        monkeypatch.setattr(sw, "SEIWebClient", lambda _config: fake)

        with pytest.raises(SystemExit) as exc_info:
            sw._validate_credentials_headless(_conn())

        assert exc_info.value.code == 1
        assert "Falha de autenticação" in capsys.readouterr().err

    def test_erro_generico_aborta(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeWebClient(None, outcome="boom")
        monkeypatch.setattr(sw, "SEIWebClient", lambda _config: fake)

        with pytest.raises(SystemExit) as exc_info:
            sw._validate_credentials_headless(_conn())

        assert exc_info.value.code == 1
        assert "Erro na validação" in capsys.readouterr().err


class TestSavePasswordHeadless:
    def test_success_writes_to_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(
            sw._keyring,
            "set_password",
            lambda service, user, pwd: store.__setitem__((service, user), pwd),
        )

        sw._save_password_headless("fulano@sei.example.gov.br", "secret")

        assert store[("todos-mcp", "fulano@sei.example.gov.br")] == "secret"

    def test_failure_aborts_with_clear_message(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _boom(_service: str, _user: str, _pwd: str) -> None:
            msg = "keyring indisponível"
            raise RuntimeError(msg)

        monkeypatch.setattr(sw._keyring, "set_password", _boom)

        with pytest.raises(SystemExit) as exc_info:
            sw._save_password_headless("fulano@sei.example.gov.br", "secret")

        assert exc_info.value.code == 1
        assert "keyring indisponível" in capsys.readouterr().err


class TestRunSetupHeadless:
    def test_already_configured_without_force_aborts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"todos": {"command": "x", "env": {"SEI_USUARIO": "old"}}}})
        )

        with pytest.raises(SystemExit) as exc_info:
            sw.run_setup_headless(_inst(), "fulano", "secret")

        assert exc_info.value.code == 1
        assert "já está configurado" in capsys.readouterr().err

    def test_happy_path_writes_keyring_and_deploys_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)  # sem config existente
        fake = _FakeWebClient(None)
        monkeypatch.setattr(sw, "SEIWebClient", lambda _config: fake)
        monkeypatch.setattr(sw._keyring, "set_password", lambda *_args: None)
        deployed: dict[str, object] = {}
        monkeypatch.setattr(
            sw,
            "_deploy_mcp_configs",
            lambda mcp_env, *, using_plaintext_password: deployed.update(
                mcp_env=mcp_env, plaintext=using_plaintext_password
            ),
        )

        # Normalização de sei_root (rstrip "/") é responsabilidade do chamador
        # (a camada de CLI, `_cmd_setup`) — `run_setup_headless` usa `inst` como veio.
        sw.run_setup_headless(
            _inst(rest_url="https://sei.example.gov.br/api"),
            "fulano",
            "secret",
        )

        mcp_env = deployed["mcp_env"]
        assert mcp_env["SEI_USUARIO"] == "fulano"
        assert mcp_env["SEI_SENHA"] == ""  # keyring, não texto claro no env
        assert mcp_env["SEI_WEB_URL"] == "https://sei.example.gov.br"
        assert mcp_env["SEI_URL"] == "https://sei.example.gov.br/api"
        assert deployed["plaintext"] is False

    def test_force_bypasses_already_configured_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"todos": {"command": "x", "env": {"SEI_USUARIO": "old"}}}})
        )
        fake = _FakeWebClient(None)
        monkeypatch.setattr(sw, "SEIWebClient", lambda _config: fake)
        monkeypatch.setattr(sw._keyring, "set_password", lambda *_args: None)
        monkeypatch.setattr(sw, "_deploy_mcp_configs", lambda *_args, **_kwargs: None)

        sw.run_setup_headless(
            _inst(), "fulano", "secret", force=True
        )  # não deve levantar SystemExit
