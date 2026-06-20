"""AST guard: every _next value in src/ must be a list of dicts, not a list of strings.

The MCP protocol for next-action hints uses `[{"tool": "name", "args": {...}}]`.
Plain-string elements (`["sei_listar_processos"]`) silently break any client
that does `_next[0]["tool"]` — a TypeError with no indication of the source.
This test catches the regression statically without running the server.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src" / "todos"


def _find_string_next_elements() -> list[tuple[str, int, str]]:
    """Return (relative_path, lineno, value) for each plain-string element of a _next list."""
    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=False):
                if not (isinstance(key, ast.Constant) and key.value == "_next"):
                    continue
                if not isinstance(value, ast.List):
                    continue
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        rel = str(py_file.relative_to(Path(__file__).parent.parent))
                        violations.append((rel, elt.lineno, elt.value))
    return violations


def test_next_elements_are_dicts_not_strings() -> None:
    """No _next list may contain plain string elements."""
    violations = _find_string_next_elements()
    if violations:
        lines = "\n".join(
            f'  {path}:{lineno}: string {value!r} — use {{"tool": {value!r}, "args": {{}}}}'
            for path, lineno, value in violations
        )
        msg = f"_next contains {len(violations)} plain-string element(s):\n{lines}"
        raise AssertionError(msg)
