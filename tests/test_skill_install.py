"""Testes do ``todos skill install`` e dos helpers em :mod:`todos.skill`.

Confirma o caminho equivalente ao ``npx skills add``: gera a SKILL.md das
tools e drop no diretório padrão do agente. Espelha
``pink/tests/test_skill_install.py`` (RFC 0019 §2.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from todos import skill


def test_resolve_target_claude_code_global() -> None:
    target = skill.resolve_target("claude-code", scope="global")
    assert target == Path.home() / ".claude" / "skills"


def test_resolve_target_cursor_project() -> None:
    target = skill.resolve_target("cursor", scope="project")
    assert target == Path(".agents") / "skills"


def test_resolve_target_override_wins(tmp_path: Path) -> None:
    target = skill.resolve_target("claude-code", scope="global", override=tmp_path)
    assert target == tmp_path


def test_resolve_target_rejects_unknown_agent() -> None:
    with pytest.raises(ValueError, match="Agente desconhecido"):
        skill.resolve_target("emacs", scope="global")


def test_detect_agents_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Detecta apenas os agentes cujo marker dir existe."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    # nada para opencode/cline
    detected = skill.detect_agents()
    assert "claude-code" in detected
    assert "cursor" in detected
    assert "opencode" not in detected
    assert "cline" not in detected


def test_install_skill_writes_to_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``install_skill`` grava SKILL.md em ``<target>/<skill_name>/SKILL.md``."""
    monkeypatch.setattr(skill, "generate_skill_md", lambda: "# fake skill")
    path = skill.install_skill(agent="claude-code", scope="global", target=tmp_path)

    assert path == tmp_path / "todos" / "SKILL.md"
    assert path.read_text(encoding="utf-8") == "# fake skill"


def test_install_skill_auto_uses_first_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Com ``agent='auto'``, pega o primeiro agente detectado."""
    monkeypatch.setattr(skill, "generate_skill_md", lambda: "fake")
    monkeypatch.setattr(skill, "detect_agents", lambda: ["cursor"])

    path = skill.install_skill(agent="auto", scope="global", target=tmp_path)
    # `cursor` detectado → usa o path do cursor; mas target override vence.
    assert path == tmp_path / "todos" / "SKILL.md"


def test_install_skill_auto_fails_without_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem agente detectado e sem ``--agent``, falha com mensagem útil."""
    monkeypatch.setattr(skill, "generate_skill_md", lambda: "fake")
    monkeypatch.setattr(skill, "detect_agents", list)

    with pytest.raises(RuntimeError, match="Nenhum agente detectado"):
        skill.install_skill(agent="auto", scope="global")


def test_frontmatter_names_skill_todos() -> None:
    """O frontmatter precisa anunciar ``name: todos`` (o que `npx skills` espera)."""
    assert "name: todos\n" in skill.FRONTMATTER


def test_frontmatter_mentions_uv_tool_install_prereq() -> None:
    """Pré-requisito ``uv tool install`` precisa estar no frontmatter para o agente ver."""
    assert "uv tool install" in skill.FRONTMATTER
    assert "git+https://github.com/franklinbaldo/todos" in skill.FRONTMATTER


def test_convert_call_translates_flags_to_key_value() -> None:
    """`--flag valor` (saída do fastmcp) vira `chave=valor` (sintaxe RFC 0018)."""
    line = "uv run --with fastmcp python cli.py call-tool sei_listar_processos --pagina 2 --apenas-meus true"
    assert skill._convert_call(line) == "todos sei_listar_processos pagina=2 apenas_meus=true"


def test_convert_call_bool_flag_without_value() -> None:
    line = "uv run --with fastmcp python cli.py call-tool sei_estilos --fresh"
    assert skill._convert_call(line) == "todos sei_estilos fresh=true"


def test_generate_skill_md_uses_in_process_introspection_not_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate_skill_md`` must not shell out to the ``fastmcp`` executable.

    Mesmo invariante que ``pink.skill`` já garante (ver docstring lá para o
    histórico do bug original): ``fastmcp.Client`` conecta na instância
    ``FastMCP`` do próprio ``todos`` em processo — nunca via subprocesso.
    """
    assert not hasattr(skill, "subprocess"), (
        "todos.skill must not import subprocess — generate_skill_md() should "
        "introspect the MCP server in-process via fastmcp.Client(mcp), never "
        "shell out to the fastmcp executable."
    )

    monkeypatch.setattr(skill, "_transform", lambda _raw: "ok")
    monkeypatch.setattr(skill, "_list_tools_in_process", _fake_list_tools_in_process)
    monkeypatch.setattr(skill, "generate_skill_content", lambda *_args, **_kwargs: "raw")

    assert skill.generate_skill_md() == "ok"


async def _fake_list_tools_in_process() -> list:
    return []
