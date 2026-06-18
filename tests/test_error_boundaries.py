"""CI guard: tools/server layer must not leak httpx transport errors.

Any `raise httpx.*` in the tools or server module means a transport
exception escapes domain code without being wrapped in a SEIError.
Catching httpx exceptions (to convert them) is fine; re-raising them
directly is the violation this test detects.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_TOOLS_DIR = _REPO_ROOT / "src" / "todos" / "tools"
_SERVER_FILE = _REPO_ROOT / "src" / "todos" / "server.py"


def _tool_files() -> list[Path]:
    """Return all .py files in tools/ plus server.py."""
    files = sorted(_TOOLS_DIR.glob("*.py"))
    files.append(_SERVER_FILE)
    return files


def _direct_httpx_raises(path: Path) -> list[int]:
    """Return line numbers of `raise httpx.*` statements in path.

    These are violations: the tools layer must convert transport errors
    into SEIError subclasses before raising.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Attribute):
            if isinstance(exc.func.value, ast.Name) and exc.func.value.id == "httpx":
                lines.append(node.lineno)
        elif (
            isinstance(exc, ast.Attribute)
            and isinstance(exc.value, ast.Name)
            and exc.value.id == "httpx"
        ):
            lines.append(node.lineno)
    return lines


def test_no_direct_httpx_raise_in_tools_layer() -> None:
    """Tools and server.py must not raise httpx exceptions directly."""
    violations: dict[str, list[int]] = {}
    for path in _tool_files():
        lines = _direct_httpx_raises(path)
        if lines:
            violations[str(path.relative_to(_REPO_ROOT))] = lines
    assert not violations, (
        "Transport exception raised directly in tools/server layer "
        "(wrap in SEIConnectionError or another SEIError subclass):\n"
        + "\n".join(f"  {file}: line(s) {lines}" for file, lines in violations.items())
    )
