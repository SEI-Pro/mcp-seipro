"""Testes para `todos.cli_call` — dispatcher genérico de tools (RFC 0018)."""

from __future__ import annotations

import asyncio

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from todos.cli_call import CliArgumentError, format_result, parse_kwargs, run


def test_parse_kwargs_valid() -> None:
    assert parse_kwargs(["a=1", "b=texto"]) == {"a": "1", "b": "texto"}


def test_parse_kwargs_empty() -> None:
    assert parse_kwargs([]) == {}


def test_parse_kwargs_missing_equals_raises() -> None:
    with pytest.raises(CliArgumentError, match="chave=valor"):
        parse_kwargs(["sem_igual"])


def test_parse_kwargs_value_contains_equals_splits_on_first() -> None:
    assert parse_kwargs(["filtro=a=b=c"]) == {"filtro": "a=b=c"}


def _text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)


def test_format_result_pretty_reindenta_json() -> None:
    result = _text_result('{"b": 2, "a": 1}')
    out = format_result(result, as_json=False)
    assert out == '{\n  "b": 2,\n  "a": 1\n}'


def test_format_result_as_json_preserva_texto_bruto() -> None:
    raw = '{"b":2,"a":1}'
    result = _text_result(raw)
    assert format_result(result, as_json=True) == raw


def test_format_result_texto_nao_json_passa_intacto() -> None:
    result = _text_result("não é json")
    assert format_result(result, as_json=False) == "não é json"


def test_format_result_multiplos_itens_junta_com_newline() -> None:
    result = CallToolResult(
        content=[
            TextContent(type="text", text="1"),
            TextContent(type="text", text="2"),
        ],
        isError=False,
    )
    assert format_result(result, as_json=True) == "1\n2"


def test_format_result_conteudo_nao_textual_usa_str() -> None:
    image = ImageContent(type="image", data="YQ==", mimeType="image/png")
    result = CallToolResult(content=[image], isError=False)
    assert str(image) in format_result(result, as_json=False)


async def _fake_call_tool(tool_name: str, kwargs: dict[str, str]) -> CallToolResult:
    del tool_name, kwargs
    return _text_result('{"ok": true}')


async def _fake_call_tool_error(tool_name: str, kwargs: dict[str, str]) -> CallToolResult:
    del tool_name, kwargs
    return _text_result("deu erro", is_error=True)


def test_run_success_prints_result_and_returns_zero(monkeypatch, capsys) -> None:
    monkeypatch.setattr("todos.cli_call.call_tool", _fake_call_tool)
    exit_code = asyncio.run(run("sei_estilos", []))
    assert exit_code == 0
    assert '"ok": true' in capsys.readouterr().out


def test_run_tool_error_returns_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr("todos.cli_call.call_tool", _fake_call_tool_error)
    exit_code = asyncio.run(run("sei_qualquer", []))
    assert exit_code == 1
    assert "deu erro" in capsys.readouterr().out


def test_run_propagates_cli_argument_error(monkeypatch) -> None:
    monkeypatch.setattr("todos.cli_call.call_tool", _fake_call_tool)
    with pytest.raises(CliArgumentError):
        asyncio.run(run("sei_qualquer", ["sem_igual"]))
