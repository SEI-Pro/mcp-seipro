"""``todos.skill`` — gera e instala a SKILL.md das tools MCP (RFC 0019 §2.1).

Compartilhado entre ``scripts/regen_mcp_skill.py`` (dev: regenera em
``.agents/skills/todos-mcp/`` para o CI checar drift) e o subcomando
``todos skill install`` (usuário final: instala no agent path apropriado,
sem precisar clonar o repo). Espelha ``pink.skill`` (mesmo mecanismo,
mesma dependência ``fastmcp.cli.generate.generate_skill_content``) —
adaptado só na sintaxe de invocação: ``todos <tool> chave=valor`` (RFC 0018)
em vez de ``pink <tool> campo=valor``.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Literal

from fastmcp import Client
from fastmcp.cli.generate import generate_skill_content

FRONTMATTER = """\
---
name: todos
description: >-
  Tools MCP do todos (SEI) invocáveis via CLI. `todos <tool> chave=valor`
  chama a tool via subprocesso stdio pelo mesmo path do protocolo MCP —
  mesma validação pydantic, mesmo envelope JSON (RFC 0018). Use no lugar
  de registrar o `todos` MCP no host quando o agente lê skills mas você
  não quer mexer no claude_desktop_config.json / .mcp.json.
---

# todos — referência das tools MCP

Gerado por `todos skill install` (ou `scripts/regen_mcp_skill.py` no dev)
a partir do schema vivo do servidor (introspecção in-process via
`fastmcp.Client`). **Não edite à mão** — rode o instalador.

## Invocação

- **No terminal:** `todos <tool> chave=valor` — tipos coercidos pelo
  validador pydantic do protocolo (int, bool como `true`/`false`,
  data `YYYY-MM-DD`, listas como JSON).
- **Pré-requisito:** instalar com `uv tool install --from
  git+https://github.com/franklinbaldo/todos todos-sei` (põe o `todos` no
  PATH); rodar `todos setup` uma vez para gravar credenciais.
"""

UTILITIES = """\
## Utilitários

```bash
todos --help                              # lista todos os comandos e tools disponíveis
todos <tool> chave=valor                  # invoca uma tool (RFC 0018, via subprocesso stdio)
todos setup                               # configuração interativa de credenciais
todos skill install                       # re-instala esta skill (após `git pull`/upgrade)
```
"""

# `uv run --with fastmcp python <gen>.py call-tool <tool> --a <value> --flag`
_CALL_LINE = re.compile(r"^uv run .*? call-tool (?P<tool>\S+)(?P<args>.*)$")
_TABLE_FLAG = re.compile(r"`--([a-z0-9-]+)`")

Scope = Literal["global", "project"]

# Paths de instalação por agente (espelham o que `npx skills add` usa).
AGENT_PATHS: dict[str, dict[Scope, Path]] = {
    "claude-code": {
        "global": Path.home() / ".claude" / "skills",
        "project": Path(".claude") / "skills",
    },
    "claude-desktop": {
        "global": Path.home() / ".claude" / "skills",
        "project": Path(".claude") / "skills",
    },
    "cursor": {
        "global": Path.home() / ".cursor" / "skills",
        "project": Path(".agents") / "skills",
    },
    "opencode": {
        "global": Path.home() / ".config" / "opencode" / "skills",
        "project": Path(".agents") / "skills",
    },
    "cline": {
        "global": Path.home() / ".agents" / "skills",
        "project": Path(".agents") / "skills",
    },
}


# Marcadores para auto-detectar agentes presentes na máquina. Calculado lazy
# (não no import time) para que ``monkeypatch.setattr(Path, "home", ...)`` em
# testes funcione e o estado real do filesystem seja consultado a cada chamada.
def _agent_home_markers() -> tuple[tuple[str, Path], ...]:
    home = Path.home()
    return (
        ("claude-code", home / ".claude"),
        ("cursor", home / ".cursor"),
        ("opencode", home / ".config" / "opencode"),
        ("cline", home / ".agents"),
    )


def _convert_call(line: str) -> str:
    """`… call-tool sei_listar_processos --pagina X --filtro Y` → `todos sei_listar_processos pagina=X filtro=Y`."""
    m = _CALL_LINE.match(line.strip())
    if not m:
        return line
    tokens = m.group("args").split()
    parts: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("--"):
            i += 1
            continue
        field = tok[2:].replace("-", "_")
        if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            parts.append(f"{field}={tokens[i + 1]}")
            i += 2
        else:
            parts.append(f"{field}=true")
            i += 1
    return " ".join(["todos", m.group("tool"), *parts])


def _normalize_table(line: str) -> str:
    """Alinha as tabelas à sintaxe key=value: `--protocolo-formatado` → `protocolo_formatado`, Flag → Campo."""
    line = _TABLE_FLAG.sub(lambda m: f"`{m.group(1).replace('-', '_')}`", line)
    return line.replace("| Flag |", "| Campo |")


def _transform(raw: str) -> str:
    """Pega o markdown gerado por ``generate_skill_content`` e devolve a SKILL.md final."""
    after = raw.split("\n## Tool Commands", 1)[-1]
    body = after.split("\n## Utility Commands", 1)[0]
    lines = [_normalize_table(_convert_call(line)) for line in body.splitlines()]
    cleaned = "\n".join(lines).strip()
    return f"{FRONTMATTER}\n## Tools\n\n{cleaned}\n\n{UTILITIES}"


async def _list_tools_in_process() -> list:
    """Connect to todos' own FastMCP server in-process and list its tools.

    No subprocess involved — ``fastmcp.Client`` accepts a live ``FastMCP``
    instance directly (in-memory transport).
    """
    # Import tardio: server.py importa install_skill deste módulo — import
    # no topo criaria ciclo.
    from todos.server import mcp  # noqa: PLC0415

    async with Client(mcp) as client:
        return await client.list_tools()


def generate_skill_md() -> str:
    """Introspect todos' MCP server in-process and return the transformed markdown.

    Não escreve em disco — quem chama decide onde gravar.
    """
    tools = asyncio.run(_list_tools_in_process())
    raw = generate_skill_content("todos", "cli.py", tools)
    return _transform(raw)


def detect_agents() -> list[str]:
    """Devolve os agentes plausivelmente instalados na máquina (por marker dir)."""
    return [name for name, marker in _agent_home_markers() if marker.is_dir()]


def resolve_target(
    agent: str,
    *,
    scope: Scope = "global",
    override: Path | None = None,
) -> Path:
    """Resolve o diretório de skills para ``agent``+``scope`` (ou usa ``override``)."""
    if override is not None:
        return override
    if agent not in AGENT_PATHS:
        msg = f"Agente desconhecido: {agent!r}. Use um de: {', '.join(sorted(AGENT_PATHS))}"
        raise ValueError(msg)
    return AGENT_PATHS[agent][scope]


def install_skill(
    *,
    agent: str = "auto",
    scope: Scope = "global",
    target: Path | None = None,
    skill_name: str = "todos",
) -> Path:
    """Gera e grava a SKILL.md no path do agente. Retorna o path final."""
    if agent == "auto":
        detected = detect_agents()
        if not detected:
            msg = (
                "Nenhum agente detectado. Passe --agent explicitamente "
                f"(opções: {', '.join(sorted(AGENT_PATHS))})."
            )
            raise RuntimeError(msg)
        agent = detected[0]
        if len(detected) > 1:
            sys.stderr.write(
                f"Múltiplos agentes detectados ({', '.join(detected)}); "
                f"instalando em {agent!r}. Passe --agent para escolher outro.\n"
            )

    content = generate_skill_md()
    dest_root = resolve_target(agent, scope=scope, override=target)
    skill_dir = dest_root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(content, encoding="utf-8", newline="\n")
    return skill_path
