"""Dispatcher genérico de tools via `todos <tool> chave=valor` (RFC 0018).

Sobe o servidor real (`python -m todos`) como subprocesso stdio e chama uma
tool nele — o mesmo caminho que um host MCP usaria, sem exigir um host MCP.
Reusa o padrão já validado em `scripts/smoke_mcp.py`: `env=dict(os.environ)`
explícito, porque `StdioServerParameters` usa `env=None` por padrão e não
herda as variáveis `SEI_*` do processo pai — ver RFC 0018 §1.1.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent


class CliArgumentError(ValueError):
    """Argumento de linha de comando malformado (não é `chave=valor`)."""


def validate_tool_name(tool_name: str) -> None:
    """Rejeita um nome de tool vazio ou que pareça um argumento `chave=valor` esquecido.

    Sem isso, `todos foo=bar` (tool name omitido por engano) ou `todos ""`
    chegariam ao MCP como um nome de tool literal e produziriam um erro
    genérico "Unknown tool" em vez de um erro de uso claro.
    """
    if not tool_name or "=" in tool_name:
        msg = f"Nome de tool inválido: {tool_name!r} — esperado 'todos <tool> chave=valor'"
        raise CliArgumentError(msg)


def parse_kwargs(pairs: list[str]) -> dict[str, str]:
    """Parseia argumentos posicionais `chave=valor` e devolve os kwargs de uma tool."""
    kwargs: dict[str, str] = {}
    for pair in pairs:
        chave, separador, valor = pair.partition("=")
        if not separador:
            msg = f"Argumento inválido (esperado chave=valor): {pair!r}"
            raise CliArgumentError(msg)
        kwargs[chave] = valor
    return kwargs


async def call_tool(tool_name: str, kwargs: dict[str, str]) -> CallToolResult:
    """Conecta ao servidor `todos` via stdio (env do processo pai herdado) e chama `tool_name`."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "todos"],
        cwd=Path.cwd(),
        env=dict(os.environ),
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await session.call_tool(tool_name, kwargs)


def format_result(result: CallToolResult, *, as_json: bool) -> str:
    """Formata o conteúdo textual de um `CallToolResult` para exibição no terminal.

    `as_json=False` (padrão): reindenta JSON para leitura humana, mantém texto
    puro como está. `as_json=True`: preserva o texto bruto de cada item (uma
    linha por item), pensado para consumo por outro processo/script.
    """
    lines: list[str] = []
    for item in result.content:
        if not isinstance(item, TextContent):
            # Conteúdo não-textual (ex.: ImageContent) não tem representação
            # textual óbvia; em modo --json ainda assim devolve JSON válido
            # (model_dump_json), nunca o repr Python de str().
            lines.append(item.model_dump_json() if as_json else str(item))
            continue
        if as_json:
            lines.append(item.text)
            continue
        try:
            parsed = json.loads(item.text)
        except json.JSONDecodeError:
            lines.append(item.text)
        else:
            lines.append(json.dumps(parsed, indent=2, ensure_ascii=False))
    return "\n".join(lines)


async def run(tool_name: str, args: list[str], *, as_json: bool = False) -> int:
    """Parseia `args`, chama `tool_name` e imprime o resultado.

    Retorna o exit code do processo CLI: `0` em sucesso, `1` se a tool
    respondeu com erro (`CallToolResult.isError`). Levanta `CliArgumentError`
    para argumentos malformados — o chamador decide como reportar.
    """
    validate_tool_name(tool_name)
    kwargs = parse_kwargs(args)
    result = await call_tool(tool_name, kwargs)
    sys.stdout.write(format_result(result, as_json=as_json) + "\n")
    return 1 if result.isError else 0
