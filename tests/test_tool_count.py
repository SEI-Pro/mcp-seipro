"""Testes de regressão para a contagem de tools (RFC 0007 §6).

Importa ``todos.server`` (não só ``todos.mcp_app``) para que os 6 tools de
orquestração definidos em server.py sejam registrados no ``mcp`` compartilhado
— refletindo a contagem real de 124 tools disponíveis ao agente em produção.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

# Importar server garante que _todos_ os @mcp.tool dos submódulos + os 6 de
# orquestração em server.py sejam registrados no mcp compartilhado.
from todos.server import mcp

_TOOL_COUNT = 124
_DOC_FILES = ("README.md", "CLAUDE.md", "manifest.json", "src/todos/mcp_app.py")
# Detecta qualquer número de 100–123 seguido de "tools" ou "ferramentas"
_STALE_RE = re.compile(r"\b(1[01][0-9]|12[0-3])\s*(tools|ferramentas)\b")


def test_tool_count_matches_runtime() -> None:
    """Número de tools registradas em tempo de execução deve ser _TOOL_COUNT."""
    registered = len(asyncio.run(mcp.list_tools()))
    assert registered == _TOOL_COUNT, (
        f"{registered} tools registradas, {_TOOL_COUNT} documentadas. "
        "Atualize _TOOL_COUNT neste arquivo e as strings nos arquivos _DOC_FILES."
    )


def test_no_stale_tool_count_in_docs() -> None:
    """Nenhum arquivo de documentação pode citar uma contagem obsoleta de tools."""
    root = Path(__file__).resolve().parent.parent
    for rel in _DOC_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        match = _STALE_RE.search(text)
        assert match is None, (
            f"{rel} contém uma contagem de tools desatualizada: "
            f"'{match.group()}' (esperado {_TOOL_COUNT})."
        )
