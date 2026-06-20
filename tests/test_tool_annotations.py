"""Guard: tool annotation profile dicts must have explicit destructiveHint values.

MCP spec: when `destructiveHint` is absent the client is allowed to assume True.
`_WRITE` and `_IDEM` profiles must carry `destructiveHint: False` explicitly so
non-destructive tools are not presented as dangerous.  `_DEST` must carry True.
`_READ` is read-only and carries no destructiveHint (readOnlyHint implies it).

These tests import the constants directly — they catch anyone who removes the
explicit `destructiveHint` key from a profile without noticing the side-effect.
"""

from __future__ import annotations

import ast
from pathlib import Path

from todos.mcp_app import _DEST, _IDEM, _READ, _WRITE

_SRC_TOOLS = Path(__file__).parent.parent / "src" / "todos" / "tools"
_SRC_SERVER = Path(__file__).parent.parent / "src" / "todos" / "server.py"
_KNOWN_PROFILES = frozenset({"_READ", "_WRITE", "_IDEM", "_DEST"})


def _all_tool_files() -> list[Path]:
    return [*list(_SRC_TOOLS.glob("*.py")), _SRC_SERVER]


def _find_mcp_tool_calls(py_file: Path) -> list[ast.Call]:
    """Return all ast.Call nodes for mcp.tool(...) in py_file."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "tool"
            and isinstance(func.value, ast.Name)
            and func.value.id == "mcp"
        ):
            calls.append(node)
    return calls


# ---------------------------------------------------------------------------
# Profile dict correctness
# ---------------------------------------------------------------------------


def test_dest_has_destructive_hint_true() -> None:
    assert _DEST.get("destructiveHint") is True, (
        "_DEST must carry destructiveHint=True so destructive tools are flagged"
    )


def test_write_has_explicit_destructive_hint_false() -> None:
    assert "destructiveHint" in _WRITE, (
        "_WRITE must declare destructiveHint explicitly — absent value defaults to True in MCP spec"
    )
    assert _WRITE["destructiveHint"] is False


def test_idem_has_explicit_destructive_hint_false() -> None:
    assert "destructiveHint" in _IDEM, (
        "_IDEM must declare destructiveHint explicitly — absent value defaults to True in MCP spec"
    )
    assert _IDEM["destructiveHint"] is False


def test_read_is_read_only() -> None:
    assert _READ.get("readOnlyHint") is True, "_READ must carry readOnlyHint=True"


def test_dest_is_not_read_only() -> None:
    assert not _DEST.get("readOnlyHint"), "_DEST must not be read-only"


# ---------------------------------------------------------------------------
# Every tool module must use one of the four known annotation profiles
# ---------------------------------------------------------------------------


def _tool_annotation_refs() -> list[tuple[str, int, str]]:
    """Return (file, lineno, annotation_name) for each @mcp.tool(annotations=<Name>) call."""
    refs: list[tuple[str, int, str]] = []
    for py_file in _all_tool_files():
        for call in _find_mcp_tool_calls(py_file):
            for kw in call.keywords:
                if kw.arg == "annotations" and isinstance(kw.value, ast.Name):
                    rel = str(py_file.relative_to(Path(__file__).parent.parent))
                    refs.append((rel, call.lineno, kw.value.id))
    return refs


def test_all_tool_annotations_use_known_profile() -> None:
    """Every @mcp.tool(annotations=X) must use one of the four named profiles."""
    refs = _tool_annotation_refs()
    unknown = [(path, lineno, name) for path, lineno, name in refs if name not in _KNOWN_PROFILES]
    if unknown:
        lines = "\n".join(
            f"  {path}:{lineno}: annotations={name!r}" for path, lineno, name in unknown
        )
        msg = f"{len(unknown)} tool(s) use an unknown annotation profile:\n{lines}"
        raise AssertionError(msg)


def test_annotation_coverage_not_empty() -> None:
    """Sanity: we must find at least 50 annotated tools (catches import/parse failure)."""
    refs = _tool_annotation_refs()
    assert len(refs) >= 50, f"Expected >=50 annotated tools, found {len(refs)}"


# ---------------------------------------------------------------------------
# @mcp.tool decorator without a named annotations= profile must not exist
# ---------------------------------------------------------------------------


def _unannotated_tool_defs() -> list[tuple[str, int]]:
    """Return (file, lineno) for @mcp.tool calls lacking a named annotations= profile.

    Flags both missing annotations= keyword and inline-dict annotations
    (e.g. annotations={"destructiveHint": True}) — only named profiles are allowed.
    """
    missing: list[tuple[str, int]] = []
    for py_file in _all_tool_files():
        for call in _find_mcp_tool_calls(py_file):
            ann_kw = next((kw for kw in call.keywords if kw.arg == "annotations"), None)
            if ann_kw is None or not isinstance(ann_kw.value, ast.Name):
                rel = str(py_file.relative_to(Path(__file__).parent.parent))
                missing.append((rel, call.lineno))
    return missing


def test_every_mcp_tool_has_annotations() -> None:
    """Every @mcp.tool() call must use a named annotations= profile."""
    missing = _unannotated_tool_defs()
    if missing:
        lines = "\n".join(f"  {path}:{lineno}" for path, lineno in missing)
        msg = f"{len(missing)} @mcp.tool() call(s) missing a named annotations= profile:\n{lines}"
        raise AssertionError(msg)
