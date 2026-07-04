"""Testes para o dispatch de `todos setup` — modo headless (RFC 0019 §2.3).

Cobre só a lógica de roteamento em `_cmd_setup` (headless vs. --cli vs. web);
o comportamento de `run_setup_headless` em si é testado em
`test_setup_wizard.py::TestRunSetupHeadless`.
"""

from __future__ import annotations

import pytest

import todos.server as server_module


def test_partial_headless_flags_raises_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--usuario` sem os outros campos obrigatórios deve falhar com exit code 2, sem tentar nada."""
    called = {"headless": False}
    monkeypatch.setattr(
        server_module,
        "run_setup_headless",
        lambda *_args, **_kwargs: called.__setitem__("headless", True),
    )

    with pytest.raises(SystemExit) as exc_info:
        server_module._cmd_setup(usuario="fulano")

    assert exc_info.value.code == 2
    assert called["headless"] is False


def test_full_headless_flags_dispatch_to_run_setup_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        server_module,
        "run_setup_headless",
        lambda inst, usuario, senha, *, force: captured.update(
            inst=inst, usuario=usuario, senha=senha, force=force
        ),
    )
    monkeypatch.setattr(
        server_module,
        "browser_available",
        lambda: pytest.fail("modo headless não deveria checar disponibilidade de navegador"),
    )

    server_module._cmd_setup(
        usuario="fulano",
        senha="secret",
        sei_web_url="https://sei.example.gov.br/",
        sei_sigla_orgao="RO",
        force=True,
    )

    assert captured["usuario"] == "fulano"
    assert captured["senha"] == "secret"
    assert captured["force"] is True
    inst = captured["inst"]
    assert inst.sei_root == "https://sei.example.gov.br"  # rstrip("/") na camada de CLI
    assert inst.sigla_orgao == "RO"
    assert inst.orgao_id == "0"
    assert inst.rest_url == ""
    assert inst.verify_ssl_disabled is False


def test_no_headless_flags_falls_back_to_existing_cli_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem --usuario/--senha, o roteamento --cli/web existente (já testado alhures) não muda."""
    called = {"cli_wizard": False}
    monkeypatch.setattr(
        server_module, "run_setup_wizard", lambda **_kwargs: called.__setitem__("cli_wizard", True)
    )

    server_module._cmd_setup(cli=True)

    assert called["cli_wizard"] is True
