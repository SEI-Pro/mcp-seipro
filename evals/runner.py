"""Agent loop for T2 multi-call eval tests.

Runs a real agent loop using the Anthropic SDK against the FastMCP server
(in-process via FastMCPTransport). The agent calls tools until it has a final
text answer, which is then compared to the expected answer from golden.xml.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING

import anthropic
from fastmcp.client import Client, FastMCPTransport
from fastmcp.exceptions import ToolError

from todos.mcp_app import mcp as _default_mcp

if TYPE_CHECKING:
    from fastmcp import FastMCP

_TOOL_MODULES = [
    "todos.server",
    "todos.tools.acompanhamento",
    "todos.tools.assinatura",
    "todos.tools.blocos_assinatura",
    "todos.tools.blocos_internos",
    "todos.tools.catalogos",
    "todos.tools.credenciamento",
    "todos.tools.documentos",
    "todos.tools.marcadores",
    "todos.tools.processos",
    "todos.tools.unidades",
]

_MAX_TURNS = 15


def _ensure_tools_registered() -> None:
    """Import all tool modules so @mcp.tool decorators run."""
    for mod_name in _TOOL_MODULES:
        importlib.import_module(mod_name)


def _to_anthropic_tools(mcp_tools: list) -> list[dict]:
    """Convert FastMCP tool list to Anthropic SDK tool format."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
        }
        for t in mcp_tools
    ]


async def run_agent(
    question: str,
    mcp_server: FastMCP | None = None,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_turns: int = _MAX_TURNS,
) -> str:
    """Run a multi-call agent loop and return the final text answer.

    Uses the Anthropic SDK to orchestrate a real agent that calls MCP tools
    via FastMCPTransport (in-process, no network). Requires ANTHROPIC_API_KEY.

    Parameters
    ----------
    question:
        The user question to answer.
    mcp_server:
        FastMCP server instance. Defaults to todos.mcp_app.mcp.
    model:
        Anthropic model to use.
    max_turns:
        Maximum tool-call rounds before giving up.
    """
    if mcp_server is None:
        _ensure_tools_registered()
        mcp_server = _default_mcp

    sdk_client = anthropic.AsyncAnthropic()

    async with Client(FastMCPTransport(mcp_server)) as fastmcp_client:
        mcp_tools = await fastmcp_client.list_tools()
        tools = _to_anthropic_tools(mcp_tools)

        messages: list[dict] = [{"role": "user", "content": question}]

        for _ in range(max_turns):
            response = await sdk_client.messages.create(
                model=model,
                max_tokens=4096,
                tools=tools,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
                temperature=0,
            )

            messages.append({"role": "assistant", "content": response.content})  # type: ignore[arg-type]

            if response.stop_reason == "end_turn":
                texts = [b.text for b in response.content if b.type == "text"]
                return " ".join(texts)

            if response.stop_reason == "tool_use":
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                tasks = [_call_tool(fastmcp_client, block) for block in tool_uses]
                call_results = await asyncio.gather(*tasks, return_exceptions=True)
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(r) if isinstance(r, Exception) else r,
                    }
                    for block, r in zip(tool_uses, call_results, strict=True)
                ]
                messages.append({"role": "user", "content": tool_results})  # type: ignore[arg-type]
            else:
                break

    return ""


async def _call_tool(client: Client, block: object) -> str:
    """Execute a single tool call and return the text result."""
    try:
        result = await client.call_tool(block.name, block.input or {})  # type: ignore[attr-defined]
    except ToolError as exc:
        return f"Erro: {exc}"
    else:
        if result.content:
            return result.content[0].text  # type: ignore[attr-defined]
        return ""
