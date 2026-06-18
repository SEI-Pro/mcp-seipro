"""CI guard: tools/server layer must not leak httpx transport errors.

Three patterns are violations — all mean a transport exception escapes
domain code without being wrapped in a SEIError:

1. `raise httpx.SomeError(...)` — direct construction in tools/server
2. `except httpx.*: raise` — bare re-raise inside an httpx handler
   (including under if/for/with/try-body, but NOT inside nested handlers)
3. `except httpx.* as e: raise e` — explicit re-raise of the bound var
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


def _catches_httpx(handler: ast.ExceptHandler) -> bool:
    """Return True if this except clause catches any httpx.* exception."""
    typ = handler.type
    if typ is None:
        return False
    if isinstance(typ, ast.Attribute) and isinstance(typ.value, ast.Name):
        return typ.value.id == "httpx"
    if isinstance(typ, ast.Tuple):
        return any(
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "httpx"
            for elt in typ.elts
        )
    return False


def _bare_raises_not_in_nested_handler(stmts: list[ast.stmt]) -> list[int]:
    """Line numbers of bare `raise` reachable without entering a nested handler.

    Recurses into if/for/while/with and try-body/orelse/finalbody, but stops
    at nested except-handler bodies so only the enclosing httpx handler's
    exception context is considered.
    """
    lines: list[int] = []
    for stmt in stmts:
        if isinstance(stmt, ast.Raise) and stmt.exc is None:
            lines.append(stmt.lineno)
        elif isinstance(stmt, (ast.If, ast.For, ast.While)):
            lines.extend(_bare_raises_not_in_nested_handler(stmt.body))
            lines.extend(_bare_raises_not_in_nested_handler(stmt.orelse))
        elif isinstance(stmt, ast.With):
            lines.extend(_bare_raises_not_in_nested_handler(stmt.body))
        elif isinstance(stmt, ast.Try):
            lines.extend(_bare_raises_not_in_nested_handler(stmt.body))
            lines.extend(_bare_raises_not_in_nested_handler(stmt.orelse))
            lines.extend(_bare_raises_not_in_nested_handler(stmt.finalbody))
    return lines


def _httpx_reraises(path: Path) -> list[int]:
    """Lines where an httpx exception is re-raised from inside a handler."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _catches_httpx(handler):
                continue
            bound = handler.name  # 'e' in `except ... as e`, or None

            lines.extend(_bare_raises_not_in_nested_handler(handler.body))

            if bound:
                lines.extend(
                    sub.lineno
                    for sub in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
                    if (
                        isinstance(sub, ast.Raise)
                        and sub.exc is not None
                        and isinstance(sub.exc, ast.Name)
                        and sub.exc.id == bound
                    )
                )
    return sorted(set(lines))


def _direct_httpx_raises(path: Path) -> list[int]:
    """Lines of `raise httpx.SomeError(...)` in path (pattern 1)."""
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


def _all_leaks(path: Path) -> list[int]:
    """All boundary-violation line numbers in path."""
    return sorted(set(_direct_httpx_raises(path) + _httpx_reraises(path)))


def test_no_httpx_escape_in_tools_layer() -> None:
    """Tools and server.py must not let httpx exceptions escape unwrapped."""
    violations: dict[str, list[int]] = {}
    for path in _tool_files():
        lines = _all_leaks(path)
        if lines:
            violations[str(path.relative_to(_REPO_ROOT))] = lines
    assert not violations, (
        "Transport exception escapes tools/server layer unwrapped "
        "(wrap in SEIConnectionError or another SEIError subclass):\n"
        + "\n".join(f"  {file}: line(s) {lines}" for file, lines in violations.items())
    )
