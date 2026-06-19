---
name: mcp-coding
description: >-
  Opinionated best practices for writing MCP (Model Context Protocol) servers
  and agent-facing tools — the implementation guidance, not protocol theory.
  Use whenever building, designing, or reviewing tools that an LLM agent will
  call over an external service: "build/write an MCP server", "expose this API
  to an agent", "wrap this service as tools", "create a tool for Claude/ChatGPT",
  "agent-facing API", "MCP em TypeScript/Python", "crie uma tool pra um agente",
  "exponha esse endpoint como ferramenta", "transforme essa API em ferramentas".
  Trigger even when "MCP" is not said but the intent is clearly an agent tool
  surface — an LLM choosing and calling tools against some service. Covers
  tool-surface design, schemas, token-economical responses, errors, auth, and
  evals. Do NOT use for consuming existing MCP servers as a client, or for
  general REST API design with no agent in the loop.
---

# MCP Coding

## The one idea everything serves

You are not wrapping an API. You are designing a **tool surface for an agent**
that has a finite context window and **pays for every token twice — once to read
the tool, once to reason over what it returns.**

A great MCP server is one where the right tool is obvious to pick, the call is
cheap to make, and the result comes back small and already useful. Hold that in
mind while reading everything below; every rule here is downstream of it.

## Design the tool surface before anything else

The most common failure mode is a server that is a 1:1 mirror of a REST API —
one tool per endpoint, named after the endpoint, returning whatever the endpoint
returned. It compiles. It "works." And no agent can use it well, because the
agent doesn't think in endpoints; it thinks in tasks.

So the first deliverable is **not code** — it is a list of tool names + one-line
descriptions, designed from the tasks an agent will actually perform:

- **Collapse workflows.** If a real task is "find the issue, then comment on it,"
  and that's always two calls, consider one tool that does both. Fewer round
  trips = fewer tokens and fewer chances to pick wrong.
- **Drop what an agent never needs.** Not every endpoint deserves a tool.
  Internal pagination plumbing, health checks, admin-only routes — leave them out.
- **Name by intent, not by route.** `linear_create_issue`, not `post_issues`.
- **Keep each tool atomic and single-purpose.** One tool, one job. A tool that
  does five things behind a `mode` flag is five tools wearing a trench coat.

Write the surface first. Get it right on paper. Then implement.

**This pattern has a name: Facade** — a simplified interface over a complex
subsystem that exposes only the features callers actually need. The 1:1-endpoint
mistake is *Adapter* thinking (one wrapper per object); a mature MCP server is
*Facade* thinking (one intent over the whole subsystem). Watch the failure mode:
a Facade that grows to cover everything becomes a god object — when the surface
gets large, split it into domain-grouped facades (see *Large surfaces*).

**Tools should tell, not ask.** Prefer a tool that performs the meaningful
operation and returns the outcome over one that hands back raw state for the
agent to inspect and then call again — that round trip costs tokens and invites
wrong turns. Caveat, from Fowler himself: don't overdo it. Read/query tools that
*provide information* are legitimate and necessary — the agent genuinely needs to
inspect state it can't hold. "Tell, don't ask" kills needless ask-then-act round
trips; it does not mean eliminating every read tool.

## Tools vs. resources

Not everything should be a tool. MCP distinguishes **tools** (actions the agent
invokes) from **resources** (reference data/context read into the context
window). Reference catalogs — enums, type lists, templates, style guides, legal
bases — are usually better as a **resource** (cached, no tool call to fetch)
and/or a **search tool** for filtered lookup. Reserve tools for actions, dynamic
search, and anything with an effect.

Caveat: in many clients a resource is pulled into context by the app/user, not
invoked autonomously by the model mid-task. So if the agent must fetch reference
data on its own during a task, expose it as a tool (or both). Rule of thumb:
**resource for reference, tool for action and for anything the agent must fetch
unprompted.**

## Defaults, decided

Default first, alternatives second — don't deliberate per project.

| Decision   | Default                                    | Alternative                                  |
|------------|--------------------------------------------|----------------------------------------------|
| Language   | **TypeScript** (SDK quality, models write it well, static types catch schema bugs) | Python / FastMCP when the host codebase is Python |
| Transport  | **Streamable HTTP, stateless JSON** for remote / multi-client | **stdio** for local single-user / CLI tools  |
| Validation | **Zod** (TS) / **Pydantic** (Python)       | —                                            |

Avoid SSE — deprecated in favor of streamable HTTP. For stateless HTTP, create a
fresh transport per request (prevents request-ID collisions); this scales and is
simpler to operate than stateful sessions. See `references/typescript.md` and
`references/python.md` for the exact wiring.

## Workflow

1. **Research the service API** — endpoints, auth model, data shapes, rate limits.
2. **Design the tool surface** (names + one-line descriptions) from tasks, not
   endpoints. This is the load-bearing step.
3. **Write schemas** — inputs and outputs, with constraints and examples in the
   field descriptions.
4. **Implement** — shared API client, auth from env, response shaping, pagination,
   error handling.
5. **Test** — MCP Inspector (`npx @modelcontextprotocol/inspector`) + build/compile.
6. **Eval** — run through an agent that only has your tools. Non-negotiable (below).

## Tool descriptions are the real interface

The agent selects tools by reading their **descriptions**, never your code. The
description is the most important string in the server. Each one should answer:

1. **What** it does.
2. **When to use it** — and **when NOT to**, if a sibling tool is confusable.
3. **Main parameters** (types, constraints, one example each).
4. **Return shape**, summarized.
5. **Side effect**, if any.
6. **Confirmation / consent**, if required.

Precise *and* concise — both, held in tension. An empirical study of 856 tools
across 103 servers found 97.1% of descriptions carried at least one smell and 56%
never stated their purpose — but the *same* work found that over-augmenting
descriptions lifted task success only ~6pp while inflating execution steps ~67%
and regressing some cases, where compact variants kept reliability with far less
token overhead. So: say the six things, then stop. A long docstring is not a
better docstring.

Set **annotations** honestly — they are hints to the client, not security:
`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.

## Schemas

- Validate **everything** with Zod / Pydantic. Bad input should fail in the schema,
  not deep in a handler.
- Put **constraints in the schema** — enums, ranges, formats, min/max length. The
  agent reads them; the runtime enforces them.
- Put **examples in field descriptions** — `"e.g. 'PROJ-123'"`. Your schema is the
  agent's only source of truth for your ID formats and conventions.
- **Design every shaped response as a real schema now**, even if you serialize it
  to a JSON string in a text block for compatibility. Define the shape as a
  Pydantic model / JSON Schema, not an ad-hoc dict. Then adopting `outputSchema` +
  `structuredContent` later is wiring, not a redesign — the contract is unchanged.

## Response design — the token budget *is* the product

These are rules, not suggestions. A response that dumps 50 KB of JSON into the
context window is a bug even when it's correct.

- **Never return an unbounded list.** Every list tool takes `limit` (default 20–50)
  and signals truncation: `has_more` plus a cursor to continue.
- **Never return a raw upstream payload by default.** Shape it. Upstream JSON is
  full of fields no agent needs and every field costs tokens twice.
- **Return `summary` + `items`.** A one-line count / aggregate up top, then the
  trimmed records. The agent often only needs the summary.
- **Preserve stable IDs.** The agent chains calls; an ID that changes between calls
  breaks every multi-step workflow.
- **Put the human-readable name next to the ID** — `{"id": "U123", "name": "Denise"}`.
  So the agent (and the human reading the trace) can tell what it's holding without
  another round trip.
- **Every truncated or failed response carries its own continuation.** Don't just
  say "truncated" — return the next action: the tool + args to get more
  (`next_actions: [{tool, args, reason}]`). Same for recoverable errors (see
  *Error design*). The agent should never have to guess how to proceed.
- **Gate the firehose behind `include_raw: false`.** When the full payload is
  genuinely needed, make the agent ask for it explicitly.

**Pagination: prefer an opaque cursor to page/limit.** Return an opaque
`next_cursor` (`null` = end) rather than exposing the backend's page numbers; the
agent treats absence of a cursor as the end and never assumes a fixed page size.
If the backend only does numeric pages, hide that behind the cursor (encode it) —
the contract stays stable while the backend can change. Keep `has_more` honest: if
you only inferred it from `len(items) == limit`, mark it (`has_more_inferred: true`)
rather than implying certainty. (This mirrors the MCP spec's cursor pagination for
the protocol `*/list` methods; for your own tool *payloads* it's borrowed practice,
not a compliance requirement — but it's the right shape.)

Offer `response_format: "markdown" | "json"` when both views earn their keep:
markdown as default for read/browse, JSON for chaining into the next call.

## Error design

Two principles, one mechanism.

**Recoverable by the agent.** An execution error is feedback, not a dead end.
Where there's a clear next step, return a structured, recoverable error: a stable
`error_code`, a human `message`, `recoverable: true`, and — the key part —
`suggested_next_tool` + `suggested_args`. *"Unit 'GPF' not found → try
`search_units` with query='GPF'."* For terminal errors, a plain actionable message
is enough. Either way: **report inside the result** (`isError` / an error field),
never as a protocol crash — the agent recovers from a result, not from a dropped
transport. And **never leak internals** — no stack traces, SQL, or secrets; log
those server-side.

**Exceptions done right are the carrier.** The clean way to produce those errors
(in Python): a base category exception plus specific typed subclasses, raised **at
the origin** — the client/scraper that actually knows the context — each carrying
its own message and structured attributes, so `suggested_next_tool` lives *on the
exception*, not reconstructed by the caller. Use `raise X from e` to keep the
chain; `logger.exception(...)` to record the trace server-side; let the tool layer
serialize the exception to the agent. (This is "tell, don't ask" applied to errors:
the exception tells you what's wrong and what to do — you don't interrogate it.) If
the SDK maps a designated exception type straight through to the agent — e.g.
FastMCP's `ToolError` — subclass that and let it propagate, no rewrapping.

**`except … pass` and `suppress(Exception)` are forbidden.** At every layer below
the tool boundary (service, client, scraper), errors must propagate — never swallowed
silently. The correct hierarchy:

1. **Propagate** — let the exception travel up; the tool layer handles it.
2. **Log + return default** — `logger.warning(...)` *before* returning `None`/`[]`.
   Never return a silent default without a log entry.
3. **Narrow suppress for cleanup only** — `suppress(httpx.TransportError, OSError)`
   during resource teardown is acceptable; add `logger.debug` so it's traceable.
   `suppress(Exception)` is never acceptable.

If ruff flags `S110`/`S112` (`except … pass`), the answer is not "replace with
`suppress`" — it is to restructure so the error propagates or is explicitly logged.

## Auth & security

- **Secrets from environment only.** Never in code. Validate on startup; fail loud
  with a clear message.
- **Validate inputs** for path traversal, injection, URL/identifier sanity, and
  size/range — beyond schema typing.
- **Write, create, sign, and delete operations declare their effect and gate on
  human confirmation by risk level.** Annotate the side effect; for anything
  persistent or irreversible, require explicit human consent (e.g. via MCP
  `elicit`) before acting — don't let the model self-authorize a destructive or
  signing action. A risk ladder: read/search → none; light write → confirm if
  persistent; formal write / send / sign / create → confirm. Annotations advertise;
  confirmation enforces.
- **stdio: never log to stdout.** It corrupts the protocol stream. Use stderr.
- **Local HTTP:** enable DNS-rebinding protection, validate the `Origin` header,
  bind to `127.0.0.1` not `0.0.0.0`.
- Annotations are **not** authorization. Enforce real access control server-side.

## Evals — short, but non-negotiable

"It runs" is not "the agent can use it." The failure you cannot see without evals
is the important one: the agent can't pick the right tool, or feeds it the wrong
args, or drowns in the response. You only catch that by watching an agent try.

Write **10 questions**, each:

- **Independent** — not dependent on another question's result.
- **Read-only** — no destructive operations.
- **Realistic** — a task a human would actually want.
- **Complex** — needs multiple tool calls and real exploration.
- **Verifiable** — one clear, stable answer checkable by string comparison.

Crucially, include questions that (a) **discriminate between confusable sibling
tools** — the agent must pick the right one of two similar tools — and (b) require
a **sequence** of calls, not a single lookup. Single-tool questions don't test
selection under pressure.

Solve each yourself first to fix the answer. Then run them through an agent that
has **only** your tools. When it fails, the bug is almost always in a tool
**description** or a **response shape** — fix the server, not the question.

```xml
<evaluation>
  <qa_pair>
    <question>...</question>
    <answer>...</answer>
  </qa_pair>
  <!-- 10 total -->
</evaluation>
```

## Large surfaces — design for progressive discovery

Past a few dozen tools, loading every definition up front burns context and
degrades selection before the agent even starts. Don't fight this with evals
alone; design for it:

- **Group tools by domain** and prefix accordingly (`process_*`, `document_*`,
  `sign_*`).
- Give each tool a **short** description for the catalog and a **fuller** one for
  when it's the candidate tool.
- Consider a **capability-search tool** (or domain tags) so the agent finds tools
  by intent instead of scanning all of them.
- Lean on the server `instructions` to hand the agent a domain model and workflow
  recipes — the cheapest way to make selection predictable across a big surface.

Much of progressive discovery is client-side (the client decides what to load),
but the server *facilitates* it through grouping, short/long descriptions, and
search. This is also the cure for a Facade that grew into a god object: split it
into domain-grouped facades.

## Smell test — fast self-review before shipping

A bad MCP server:

- has tools named `get`, `list`, `query` — no prefix, no intent
- mirrors the REST API one endpoint per tool (Adapter, not Facade)
- returns whatever the upstream API returned, unshaped
- has a list tool with no `limit`
- truncates or errors without telling the agent how to continue
- has descriptions that say "gets data" — or are bloated past the six essentials
- returns IDs with no names, or names with no IDs
- lets the model invoke a write / sign / delete with no confirmation
- throws on bad input instead of returning an actionable error
- logs to stdout over stdio
- has no evals

Three or more true → the agent will struggle. Fix before shipping.

## References & foundations

Load the language refs only when implementing — the spine above reads in one pass.

- `references/typescript.md` — `McpServer` + `registerTool`, Zod schemas,
  `structuredContent`, stateless streamable-HTTP and stdio wiring.
- `references/python.md` — FastMCP, `@mcp.tool`, Pydantic models, `response_format`,
  pagination, error handling.

Foundations — the "why" under the rules (read for depth, not to implement):

- **Facade pattern** — `refactoring.guru/design-patterns/facade`. The tool surface
  is a Facade, not an Adapter; a Facade that covers everything is a god object.
- **Tell, Don't Ask** — `martinfowler.com/bliki/TellDontAsk.html`. Tools (and
  errors) tell; with Fowler's own caveat against eliminating query methods.
- **Handling exceptions in Python like a pro** —
  `guicommits.com/handling-exceptions-in-python-like-a-pro`. The exception-as-carrier
  pattern behind *Error design*.
- **Pattern catalog** — `sourcemaking.com/design_patterns`. Further reading; most
  GoF patterns don't apply to an MCP server — Facade does.
- **Empirical (descriptions as contract; precise *and* concise)** — "MCP Tool
  Descriptions Are Smelly!" `arXiv:2602.14878`; "From Docs to Descriptions"
  `arXiv:2602.18914`.
