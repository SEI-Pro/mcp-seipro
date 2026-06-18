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

_TOOL_COUNT = 126
_DOC_FILES = (
    "README.md",
    "CLAUDE.md",
    "manifest.json",
    "src/todos/mcp_app.py",
    "pyproject.toml",
    "src/todos/server.py",
)
# Detecta qualquer número diferente de _TOOL_COUNT antes de "tools"/"ferramentas".
# Regex genérico — nunca precisa de atualização manual a cada bump de contagem.
_STALE_RE = re.compile(r"\b(\d+)\s*(tools|ferramentas)\b")


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
        for match in _STALE_RE.finditer(text):
            count = int(match.group(1))
            assert count == _TOOL_COUNT, (
                f"{rel} contém uma contagem de tools desatualizada: "
                f"'{match.group()}' (esperado {_TOOL_COUNT})."
            )
