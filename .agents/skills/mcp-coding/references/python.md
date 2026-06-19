# Python / FastMCP idioms

The alternative, for Python host codebases. Load this when implementing in Python.

## Setup

```bash
pip install "mcp[cli]" pydantic
```

```python
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from enum import Enum

mcp = FastMCP("example_mcp")   # name: {service}_mcp, snake_case
```

## Input models with Pydantic

Validate everything. Constraints live in `Field(...)`; examples go in the
`description` — the schema is the agent's only source of truth for your formats.

```python
class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"

class UserSearchInput(BaseModel):
    query: str = Field(
        ..., min_length=1, max_length=200,
        description="Search string for names/emails, e.g. 'denise'",
    )
    limit: int = Field(
        default=20, ge=1, le=100,
        description="Max results, 1-100 (default 20)",
    )
    offset: int = Field(
        default=0, ge=0,
        description="Results to skip for pagination (default 0)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format",
    )
```

## Registering a tool

`@mcp.tool` with annotations. The docstring is the tool **description** the agent
reads to choose the tool — write it the way you'd write the TS `description`.

```python
@mcp.tool(
    name="example_search_users",
    annotations={
        "title": "Search Example Users",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def example_search_users(params: UserSearchInput) -> str:
    """Search existing users in Example by name, email, or team.
    Does NOT create or modify users — search only.

    Returns: summary + trimmed records ({id, name, email, team?, active})
    with has_more / next_offset for pagination.

    Use when: "find the marketing team", "look up denise's account".
    Don't use when: you need to create a user (use example_create_user).
    Errors: "No users found matching '<query>'"; "Error: Rate limit exceeded".
    """
    try:
        data = await api_get(
            "users/search",
            params={"q": params.query, "limit": params.limit, "offset": params.offset},
        )
        users = data.get("users", [])
        total = data.get("total", 0)

        if not users:
            return f"No users found matching '{params.query}'"

        # Shape the payload — never return raw upstream JSON.
        shaped = [
            {
                "id": u["id"], "name": u["name"], "email": u["email"],  # id + name together
                **({"team": u["team"]} if u.get("team") else {}),
                "active": u.get("active", True),
            }
            for u in users
        ]
        has_more = total > params.offset + len(users)
        output = {
            "total": total, "count": len(users), "offset": params.offset,
            "users": shaped, "has_more": has_more,
            **({"next_offset": params.offset + len(users)} if has_more else {}),
        }

        if params.response_format == ResponseFormat.JSON:
            import json
            return json.dumps(output, indent=2)

        lines = [f"# Users matching '{params.query}' ({total} found, {len(users)} shown)", ""]
        for u in shaped:
            lines.append(f"## {u['name']} ({u['id']})")
            lines.append(f"- {u['email']}")
            if u.get("team"):
                lines.append(f"- {u['team']}")
            lines.append("")
        return "\n".join(lines)

    except Exception as e:
        return handle_api_error(e)   # actionable string, never a raw traceback
```

## Structured output

Annotate the return type with a Pydantic model (or `TypedDict`) and FastMCP emits
an `outputSchema` and structured content automatically:

```python
class UserSearchResult(BaseModel):
    total: int
    count: int
    offset: int
    users: list[dict]
    has_more: bool
    next_offset: int | None = None

@mcp.tool(name="...")
async def ... (params: UserSearchInput) -> UserSearchResult:
    ...
    return UserSearchResult(**output)
```

## Errors

Return an actionable string (or raise and convert centrally); never surface a raw
traceback. Tell the agent the next move.

```python
def handle_api_error(e: Exception) -> str:
    if isinstance(e, TimeoutError):
        return "Error: request timed out — retry, or narrow the query with a tighter filter."
    return f"Error: {type(e).__name__}. Check the query and try again."
```

**Only the tool layer catches broadly.** `handle_api_error` converts at the tool
boundary so the agent sees an actionable string, not a crash. At service/client
layers below, errors must propagate — never `except ... pass`, never
`suppress(Exception)`. If low-level cleanup must suppress, use a narrow type and
`logger.debug`:

```python
# OK — teardown only, narrow type, logged
from contextlib import suppress
import logging
logger = logging.getLogger(__name__)

with suppress(httpx.TransportError, OSError):  # not suppress(Exception)
    logger.debug("closing client after eviction")
    await client.aclose()
```

`S110`/`S112` from ruff means restructure so the error propagates or is explicitly
logged — not "replace with `suppress`".

## Auth & transport

- Secrets from `os.environ` only; validate at startup, fail loud.
- stdio (`mcp.run()`): **never `print()`** — stdout is the protocol channel. Log to
  stderr (`logging` defaults there, or `print(..., file=sys.stderr)`).
- For remote, run streamable HTTP (stateless) — see the SDK README for the ASGI app
  wiring; bind locally to `127.0.0.1` and validate `Origin`.

```python
if __name__ == "__main__":
    mcp.run()   # stdio by default
```

## Test

```bash
python -m py_compile server.py
npx @modelcontextprotocol/inspector   # exercise tools interactively
```

## Checklist

- [ ] Pydantic input models; constraints in `Field`, examples in descriptions
- [ ] Docstring states what/when/returns + use-when/don't-use-when + errors
- [ ] List tools have `limit` and return `has_more` + `next_offset`
- [ ] Responses shaped (summary + items), IDs paired with names
- [ ] Return-type model for structured output where data is structured
- [ ] Errors returned as actionable strings; no tracebacks leaked
- [ ] Secrets from env; stdio logs to stderr only
