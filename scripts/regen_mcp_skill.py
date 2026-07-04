#!/usr/bin/env python3
"""Regenera ``.agents/skills/todos-mcp/SKILL.md`` (dev/CI).

Lógica vive em :mod:`todos.skill` — este script só direciona o output para o
path do repo onde o CI checa drift (`git diff --exit-code`). Para o caminho
do usuário final, prefira ``todos skill install`` (que escolhe o path do
agente, sem depender de clone).

Uso::

    uv run --no-sync python scripts/regen_mcp_skill.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".agents" / "skills" / "todos-mcp" / "SKILL.md"

# Permite executar como script standalone sem `uv sync` antes — adiciona
# ``src/`` ao path manualmente.
sys.path.insert(0, str(REPO / "src"))

from todos.skill import generate_skill_md  # noqa: E402 — import depois do sys.path.insert


def main() -> int:
    """Gera o SKILL.md e grava em ``OUT`` (LF determinístico)."""
    content = generate_skill_md()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8", newline="\n")
    sys.stdout.write(f"Wrote {OUT.relative_to(REPO)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
