#!/usr/bin/env python3
"""Chama qualquer tool MCP do todos diretamente via stdio, sem host MCP.

Uso:
    uv run python scripts/call_tool.py <tool_name> [chave=valor ...]
    uv run python scripts/call_tool.py sei_pesquisar_processos palavras_chave="fulano"
    uv run python scripts/call_tool.py sei_consultar_processo protocolo_formatado="0020.009181/2025-68" backend=web

Por que este script existe: `fastmcp call <arquivo.py> <tool> chave=valor` (o
atalho de CLI do próprio pacote fastmcp) NÃO repassa variáveis de ambiente
customizadas (SEI_URL, SEI_WEB_URL, SEI_USUARIO, etc.) para o subprocesso —
`StdioTransport`/`StdioServerParameters` do SDK usam `env=None` por padrão, o
que não herda o ambiente do processo pai. Este script usa o mesmo padrão já
validado em `scripts/smoke_mcp.py` (`env=dict(os.environ)` explícito), então
qualquer variável já exportada no shell chega ao servidor normalmente.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


def _parse_kwargs(pairs: list[str]) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            msg = f"Argumento inválido (esperado chave=valor): {pair!r}"
            raise ValueError(msg)
        key, value = pair.split("=", 1)
        kwargs[key] = value
    return kwargs


async def call_tool(tool_name: str, kwargs: dict[str, str]) -> None:
    """Conecta ao servidor todos via stdio (env do processo pai herdado) e chama a tool."""
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
        result = await session.call_tool(tool_name, kwargs)

        for item in result.content:
            if isinstance(item, TextContent):
                try:
                    parsed = json.loads(item.text)
                    print(json.dumps(parsed, indent=2, ensure_ascii=False))  # noqa: T201
                except json.JSONDecodeError:
                    print(item.text)  # noqa: T201
            else:
                print(item)  # noqa: T201

        if result.isError:
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("tool_name", help="Nome da tool MCP (ex.: sei_consultar_processo)")
    parser.add_argument("args", nargs="*", help="Argumentos chave=valor")
    parsed = parser.parse_args()

    asyncio.run(call_tool(parsed.tool_name, _parse_kwargs(parsed.args)))
