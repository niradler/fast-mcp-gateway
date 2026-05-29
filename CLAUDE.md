# CLAUDE.md — fast-mcp-gateway

Project-level guidance for working in this repo. Read alongside the global instructions.

## What this is

A lean Python package that mounts on FastAPI and turns a registry of upstream MCP
servers into one governed, namespaced MCP endpoint. The gateway is a **parent FastMCP
server** that proxies each registered upstream under a namespace, exposed as an ASGI
app, plus a FastAPI admin router for registry CRUD.

**Guiding principle: reuse, don't rebuild.** FastMCP already does proxying, transport
bridging, composition/namespacing, tool filtering/renaming, and protocol middleware.
We build only what it lacks: the **Store** (registry), **groups**, the **builder**
(registry → proxy mounts), the **hook runner**, and **search meta-tools**.

**Everything cross-cutting is a hook**, not a subsystem. Auth, policy, HIL, redaction,
audit, and cost limits are all plain async functions passed to `create_gateway` in a
`Hooks` container. Do not add bespoke per-feature logic to the core — push it to hooks.

## Stack & tooling

- **uv** — environment + packaging (`uv sync`, `uv run`, `uv build`, `uv publish`).
- **src layout** — code in `src/mcp_gateway/`; import name `mcp_gateway`, dist name
  `fast-mcp-gateway`. Chosen for clean PyPI publishing.
- **ruff** — lint + format. **mypy --strict** — types (gate). **pytest** + pytest-asyncio.
- Target floor: **Python 3.11+** (`requires-python`); dev interpreter pinned via
  `.python-version` (3.13).

## Key decision: FastMCP v3, not v2

The original plan locked FastMCP **v2** (decision #10). We are on **v3.3.1** (latest,
installed). v3 supports everything needed, with renamed APIs — use these:

- `FastMCP.as_proxy(...)` → `from fastmcp.server import create_proxy` /
  `from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient`
- `parent.mount(proxy, prefix="x")` → `parent.mount(proxy, namespace="x")`
  (v3 `mount` also accepts `as_proxy=True` and `tool_names={...}` for renaming)
- Middleware: `from fastmcp.server.middleware import Middleware, MiddlewareContext`
  (`on_call_tool`, `on_list_tools`, …) — unchanged in spirit.
- ASGI mounting: `mcp.http_app(path=...)` — unchanged; pass `app.lifespan` to FastAPI.

If we ever pin back to v2, revert these and set `fastmcp>=2,<3`.

## Commands

```bash
make install     # uv sync (venv + deps incl. dev group)
make check       # lint + format-check + typecheck + test  (CI gate; run before done)
make test        # pytest
make format      # ruff format + safe lint fixes
make run         # run examples/basic_app.py with uvicorn --reload
make build       # sdist + wheel
```

On Windows `make` isn't built in — use WSL/Git Bash, `scoop install make`, or run the
underlying `uv run ...` commands directly.

## Layout

```
src/mcp_gateway/
  app.py            # create_gateway() -> Gateway(mcp, mcp_app, admin_router, builder)
  hooks.py          # Hooks container + HookMiddleware (on_call_tool / on_list_tools)
  connect.py        # build_client_factory() — runs pre_mcp_connect, builds ProxyClient
  builder.py        # GatewayBuilder: registry -> create_proxy + namespace mount; reload()
  search.py         # register_search_tools() — search_tools / describe_tool meta-tools
  models.py         # pydantic schemas (ServerRecord/Create/Patch, GroupRecord, ...)
  store/base.py     # Store protocol
  store/sqlite.py   # default SqliteStore
  api/servers.py    # admin CRUD router (servers)
  api/groups.py     # admin CRUD router (groups)
examples/basic_app.py
tests/
```

## Conventions

- Python style (adapts the global code-style to Python): **full type hints** on every
  param and return; **snake_case** functions/vars, **PascalCase** classes, no noise
  suffixes (`Store` not `StoreService`); imports at top, grouped stdlib/external/internal.
- **No inline comments** unless behavior genuinely surprises a reader; docstrings on
  public functions/classes explain intent.
- Async-first: all I/O is `async`. Constructor-injected deps (e.g. routers are built by
  `build_*_router(store)` factories) — no global state, no DI framework.
- Central config / structured logging when added: one config module, one logger
  (`logging.getLogger("mcp_gateway.*")`), never `print`.
- Add a dep with `uv add` (latest stable); dev deps with `uv add --dev`.

## Current state

Milestone 0 (scaffold) is in place: package skeleton, `Store` protocol + `SqliteStore`
stub, `create_gateway()` mounting an **empty** FastMCP on FastAPI, admin routes wired
(returning `501`), tooling, tests, docs. Most handlers/builder/hooks bodies raise
`NotImplementedError` with a `Milestone N` marker — implement per the README roadmap.

Working notes and next steps live in `.claude/docs/`.
