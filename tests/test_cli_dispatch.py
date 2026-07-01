"""Testes para o dispatch `todos <tool> chave=valor` no `_app` Typer (RFC 0018)."""

from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

import todos.server as server_module
from todos.cli_call import CliArgumentError


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], False),
        (["setup"], False),
        (["set-password"], False),
        (["--help"], False),
        (["-h"], False),
        (["sei_consultar_processo"], True),
        (["sei_consultar_processo", "protocolo_formatado=123"], True),
    ],
)
def test_is_tool_invocation(argv: list[str], *, expected: bool) -> None:
    assert server_module._is_tool_invocation(argv) is expected


def test_dispatch_tool_forwards_name_kwargs_and_json_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run(tool_name: str, args: list[str], *, as_json: bool) -> int:
        captured["tool_name"] = tool_name
        captured["args"] = args
        captured["as_json"] = as_json
        return 0

    monkeypatch.setattr(server_module.cli_call, "run", fake_run)

    exit_code = server_module._dispatch_tool(
        ["sei_consultar_processo", "protocolo_formatado=123", "--json"]
    )

    assert exit_code == 0
    assert captured == {
        "tool_name": "sei_consultar_processo",
        "args": ["protocolo_formatado=123"],
        "as_json": True,
    }


def test_dispatch_tool_reports_cli_argument_error(monkeypatch, capsys) -> None:
    async def fake_run(tool_name: str, args: list[str], *, as_json: bool) -> int:
        del tool_name, args, as_json
        msg = "chave=valor obrigatório"
        raise CliArgumentError(msg)

    monkeypatch.setattr(server_module.cli_call, "run", fake_run)

    exit_code = server_module._dispatch_tool(["sei_qualquer", "invalido"])

    assert exit_code == 1
    assert "chave=valor obrigatório" in capsys.readouterr().err


def test_dispatch_tool_reports_mcp_error(monkeypatch, capsys) -> None:
    async def fake_run(tool_name: str, args: list[str], *, as_json: bool) -> int:
        del tool_name, args, as_json
        raise McpError(ErrorData(code=-32602, message="tool desconhecida"))

    monkeypatch.setattr(server_module.cli_call, "run", fake_run)

    exit_code = server_module._dispatch_tool(["sei_inexistente"])

    assert exit_code == 1
    assert "tool desconhecida" in capsys.readouterr().err


def test_main_dispatches_tool_invocation_without_starting_typer_app(monkeypatch) -> None:
    monkeypatch.setattr(server_module.sys, "argv", ["todos", "sei_estilos"])
    monkeypatch.setattr(server_module, "_dispatch_tool", lambda _argv: 0)
    monkeypatch.setattr(
        server_module,
        "_app",
        lambda: pytest.fail("_app não deveria ser chamado para uma invocação de tool"),
    )

    with pytest.raises(SystemExit) as exc_info:
        server_module.main()
    assert exc_info.value.code == 0


def test_main_falls_back_to_typer_app_for_fixed_commands(monkeypatch) -> None:
    monkeypatch.setattr(server_module.sys, "argv", ["todos", "setup"])
    called: dict[str, bool] = {"app": False}
    monkeypatch.setattr(server_module, "_app", lambda: called.__setitem__("app", True))

    server_module.main()

    assert called["app"] is True
