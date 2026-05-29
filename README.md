# fast-mcp-gateway

A small Python package that mounts on **FastAPI** and turns a registry of upstream
**MCP servers** into one governed, namespaced MCP endpoint. The core stays thin;
everything cross-cutting — auth, policy, human-in-the-loop, redaction, audit,
cost limits — is a **hook function** you pass at mount time.

> **Status:** early scaffold (Milestone 0). The structure, public API, admin routes,
> and tooling are in place; most handlers return `501` until later milestones land.
> See the [roadmap](#roadmap).

## Philosophy

- **Reuse, don't rebuild.** [FastMCP](https://gofastmcp.com) already does proxying,
  transport bridging, server composition/namespacing, tool filtering/renaming, and
  protocol-level middleware. We build only what it lacks.
- **Lean core.** What we own: a persistent server **registry (Store)**, **groups**,
  building proxies from the registry, a **hook runner**, and a **search meta-tool**.
- **Hooks are plain functions.** No plugin manager, no entry points, no auth classes.
  Auth is not a subsystem — it's a `pre_mcp_connect` hook.

## Architecture

```text
FastAPI app
 ├── /admin   → APIRouter (CRUD: servers, groups, tool policy)   [we build]
 └── /mcp     → FastMCP.http_app()  (the gateway MCP server)     [FastMCP]
                  ├── mount(proxy_github, namespace="github")
                  ├── mount(proxy_slack,  namespace="slack")     ← namespacing
                  └── search_tools()  (local meta-tool)
```

The gateway is a **parent FastMCP server** that proxies each registered upstream and
mounts it under a namespace, exposed as an ASGI app you mount into your FastAPI app
alongside an admin router for CRUD.

## Install

```bash
uv add fast-mcp-gateway        # or: pip install fast-mcp-gateway
```

Requires Python 3.11+.

## Quickstart

```python
import os
from fastapi import FastAPI
from mcp_gateway import ConnectContext, ConnectSettings, Hooks, SqliteStore, create_gateway


async def inject_auth(ctx: ConnectContext) -> ConnectSettings | None:
    # Auth is just a hook: return headers merged over the server's static headers.
    if ctx.server.name == "github":
        return ConnectSettings(headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}"})
    return None


gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    hooks=Hooks(pre_mcp_connect=[inject_auth]),
)

# The MCP server manages sessions via lifespan, so wire it on the host app:
app = FastAPI(lifespan=gateway.lifespan)
gateway.install(app)            # mounts /admin (CRUD) and /mcp (MCP endpoint)
```

Run the bundled example:

```bash
make run          # uv run uvicorn examples.basic_app:app --reload
# Admin + docs: http://127.0.0.1:8000/docs
# MCP endpoint: http://127.0.0.1:8000/mcp/
```

## Hooks

A hook is an async function, grouped in a `Hooks` container and passed at mount.
Each binds to the correct layer:

| Hook | Binds to | Runs |
|---|---|---|
| `pre_mcp_connect` | proxy client factory | before opening an upstream session |
| `pre_list_tools` | FastMCP middleware `on_list_tools` | on catalog requests |
| `pre_tool_call` | FastMCP middleware `on_call_tool` (pre) | before forwarding a call |
| `confirmation` | `on_call_tool` (when `REQUIRE_CONFIRMATION`) | human-in-the-loop approval |
| `post_tool_call` | FastMCP middleware `on_call_tool` (post) | after the upstream result |

Hooks chain in order. A `pre_tool_call` hook may continue, mutate args, deny, or
return `REQUIRE_CONFIRMATION` — which triggers the `confirmation` hooks (HIL). If any
confirmation hook rejects, or none is registered, the call is denied (fail-safe).
Policy, guardrails, audit, and cost limits are all just hooks — nothing special in
the core.

## Admin API

| Method | Path | Purpose |
|---|---|---|
| `GET/POST` | `/admin/servers` | list / register servers |
| `GET/PATCH/DELETE` | `/admin/servers/{id}` | read / update / remove |
| `GET` | `/admin/servers/{id}/tools` | live tool introspection |
| `POST` | `/admin/servers/{id}/test` | connect + handshake check |
| `GET/POST` | `/admin/groups` | list / create groups |
| `GET/PATCH/DELETE` | `/admin/groups/{id}` | read / update / remove |
| `PUT` | `/admin/groups/{id}/servers` | set membership |
| `POST` | `/admin/reload` | rebuild mounts from the store |

CRUD writes to the `Store`; `POST /admin/reload` (or `await gateway.reload()`)
rebuilds the proxy mounts. No live hot-swap in v1 — simple and lean.

## Store

The gateway's only persistence dependency is the `Store` protocol. `SqliteStore`
(single file, zero setup) ships as the default; Postgres / Redis / in-memory are
drop-in via `store=` with no core changes.

## Development

```bash
make install     # uv sync
make check       # lint + format-check + typecheck + tests (CI gate)
make test        # pytest
make format      # ruff format + safe fixes
make build       # sdist + wheel
```

Tooling: [uv](https://docs.astral.sh/uv/) (env + packaging), **ruff** (lint +
format), **mypy --strict** (types), **pytest** (tests). Run `make help` for all
targets.

> On Windows, `make` is not built in — use it from WSL/Git Bash, install GNU Make
> (`scoop install make`), or run the underlying `uv run ...` commands directly.

## Roadmap

| Phase | Deliverable |
|---|---|
| 0 | Package skeleton, `Store` protocol + `SqliteStore`, `create_gateway()` mounting an empty FastMCP on FastAPI — **scaffolded** |
| 1 | Server CRUD + builder (registry → proxy + namespace mount) + `reload()` + `pre_mcp_connect` |
| 2 | `HookMiddleware`: `pre_tool_call` / `post_tool_call` / `pre_list_tools` |
| 3 | Groups + tool allow/deny + per-group membership |
| 4 | `search_tools` / `describe_tool` meta-tools + catalog cache |
| 5 | Reference hooks (audit, allow/deny, confirmation), docs, packaging |

## License

[MIT](LICENSE)
