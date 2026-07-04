"""Testes para o dispatch `todos <tool> chave=valor` no `_app` Cyclopts (RFC 0018)."""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

import todos.server as server_module
from todos.cli_call import CliArgumentError


def test_fixed_commands_matches_registered_cyclopts_commands() -> None:
    """`_FIXED_COMMANDS` deve ser derivado do `_app` Cyclopts, não hardcoded.

    Regressão: um `@_app.command(...)` futuro sem entrada equivalente aqui
    seria despachado como nome de tool por engano — ver RFC 0018 §6.1.
    """
    registered = {name for name in server_module._app if not name.startswith("-")}
    assert registered == server_module._FIXED_COMMANDS
    assert registered == {"setup", "set-password"}


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
    """Cobre falhas reais de protocolo/transporte MCP (não o caso de tool desconhecida:
    a FastMCP devolve isso como `CallToolResult(isError=True)`, tratado no caminho
    normal de `cli_call.run` — ver RFC 0018 §6.1).
    """

    async def fake_run(tool_name: str, args: list[str], *, as_json: bool) -> int:
        del tool_name, args, as_json
        raise McpError(ErrorData(code=-32602, message="tool desconhecida"))

    monkeypatch.setattr(server_module.cli_call, "run", fake_run)

    exit_code = server_module._dispatch_tool(["sei_inexistente"])

    assert exit_code == 1
    assert "tool desconhecida" in capsys.readouterr().err


@pytest.mark.parametrize(
    "exc",
    [
        OSError("subprocesso não iniciou"),
        anyio.BrokenResourceError(),
        anyio.ClosedResourceError(),
    ],
)
def test_dispatch_tool_reports_transport_failure(monkeypatch, capsys, exc: Exception) -> None:
    """Falha ao spawnar/negociar o subprocesso stdio deve virar erro claro, não traceback cru."""

    async def fake_run(tool_name: str, args: list[str], *, as_json: bool) -> int:
        del tool_name, args, as_json
        raise exc

    monkeypatch.setattr(server_module.cli_call, "run", fake_run)

    exit_code = server_module._dispatch_tool(["sei_estilos"])

    assert exit_code == 1
    assert "sei_estilos" in capsys.readouterr().err


def test_main_dispatches_tool_invocation_without_starting_cyclopts_app(monkeypatch) -> None:
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


def test_main_falls_back_to_cyclopts_app_for_fixed_commands(monkeypatch) -> None:
    monkeypatch.setattr(server_module.sys, "argv", ["todos", "setup"])
    called: dict[str, bool] = {"app": False}
    monkeypatch.setattr(server_module, "_app", lambda: called.__setitem__("app", True))

    server_module.main()

    assert called["app"] is True
