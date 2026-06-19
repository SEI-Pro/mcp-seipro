# TypeScript / Node MCP idioms

The default stack. Load this when implementing in TypeScript.

## Setup

```bash
npm i @modelcontextprotocol/sdk zod express
```

`package.json`: `"type": "module"`. `tsconfig.json`: `"module": "NodeNext"`,
`"target": "ES2022"`, `"strict": true`.

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const server = new McpServer({ name: "example-mcp-server", version: "1.0.0" });
```

Server name: `{service}-mcp-server`, kebab-case, no version in the name.

## Registering a tool

Use `server.registerTool(name, config, handler)`. The config carries the
`title`, the all-important `description`, `inputSchema`, optional `outputSchema`,
and `annotations`.

```typescript
const UserSearchInput = z.object({
  query: z.string().min(1).describe("Search string for name/email, e.g. 'denise'"),
  limit: z.number().int().min(1).max(100).default(20)
    .describe("Max results, 1-100 (default 20)"),
  offset: z.number().int().min(0).default(0)
    .describe("Results to skip for pagination (default 0)"),
  response_format: z.enum(["markdown", "json"]).default("markdown")
    .describe("Output format"),
}).strict();

server.registerTool(
  "example_search_users",
  {
    title: "Search Example Users",
    description: `Search existing users in Example by name, email, or team.
Does NOT create or modify users — search only.

Args:
  - query (string): match against names/emails
  - limit (number): 1-100, default 20
  - offset (number): pagination offset, default 0
  - response_format ('markdown'|'json'): default markdown

Returns: summary + trimmed user records ({id, name, email, team?, active}),
plus has_more / next_offset for pagination.

Use when: "find the marketing team", "look up denise's account".
Don't use when: you need to create a user (use example_create_user).
Errors: "No users found matching '<query>'"; "Error: Rate limit exceeded".`,
    inputSchema: UserSearchInput.shape,
    outputSchema: {
      total: z.number(), count: z.number(), offset: z.number(),
      users: z.array(z.object({
        id: z.string(), name: z.string(), email: z.string(),
        team: z.string().optional(), active: z.boolean(),
      })),
      has_more: z.boolean(), next_offset: z.number().optional(),
    },
    annotations: {
      readOnlyHint: true, destructiveHint: false,
      idempotentHint: true, openWorldHint: true,
    },
  },
  async (params) => {
    try {
      const data = await apiGet("users/search", {
        q: params.query, limit: params.limit, offset: params.offset,
      });
      const users = data.users ?? [];
      const total = data.total ?? 0;

      if (!users.length) {
        return { content: [{ type: "text", text: `No users found matching '${params.query}'` }] };
      }

      // Shape the payload — never return raw upstream JSON.
      const output = {
        total,
        count: users.length,
        offset: params.offset,
        users: users.map((u: any) => ({
          id: u.id, name: u.name, email: u.email,    // id + human name together
          ...(u.team ? { team: u.team } : {}),
          active: u.active ?? true,
        })),
        has_more: total > params.offset + users.length,
        ...(total > params.offset + users.length
          ? { next_offset: params.offset + users.length } : {}),
      };

      const text =
        params.response_format === "json"
          ? JSON.stringify(output, null, 2)
          : [`# Users matching '${params.query}' (${total} found, ${users.length} shown)`,
             "", ...output.users.map((u) => `## ${u.name} (${u.id})\n- ${u.email}`)].join("\n");

      return {
        content: [{ type: "text", text }],
        structuredContent: output,   // modern pattern: structured + text together
      };
    } catch (error) {
      // Actionable error inside the result, not a thrown protocol fault.
      return {
        isError: true,
        content: [{ type: "text", text: handleApiError(error) }],
      };
    }
  }
);
```

`inputSchema` takes the Zod **shape** (`.shape`), not the wrapped object.

## Transport: stateless streamable HTTP (remote default)

A fresh transport per request avoids request-ID collisions and keeps the server
horizontally scalable.

```typescript
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express from "express";

const app = express();
app.use(express.json());

app.post("/mcp", async (req, res) => {
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,   // stateless
    enableJsonResponse: true,
  });
  res.on("close", () => transport.close());
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});

app.listen(3000);
```

Local HTTP hardening: bind `127.0.0.1`, validate `Origin`, enable DNS-rebinding
protection.

## Transport: stdio (local default)

```typescript
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const transport = new StdioServerTransport();
await server.connect(transport);
```

Over stdio, **never** `console.log` — stdout is the protocol channel. Use
`console.error` (stderr) for all logging.

## Build & test

```bash
npm run build
npx @modelcontextprotocol/inspector   # exercise tools interactively
```

## Checklist

- [ ] Every tool: `title`, `description`, `inputSchema`, `annotations`
- [ ] Descriptions state what/when/returns + use-when/don't-use-when + errors
- [ ] List tools have `limit` and return `has_more` + `next_offset`
- [ ] Responses shaped (summary + items), IDs paired with names
- [ ] `outputSchema` + `structuredContent` where data is structured
- [ ] Errors returned in-result and actionable; no internals leaked
- [ ] Secrets from env; stdio logs to stderr only
