# MCP Gateway — working notes

## What we're building

Lean FastMCP-based MCP gateway: a parent FastMCP server proxying registered upstreams
under namespaces, mounted on FastAPI, with a hook-based extension model. Full plan was
provided by Nir (philosophy, architecture, reuse-vs-build, hooks, Store, milestones).

## State (Milestone 0 — scaffold: DONE)

- `uv` project, **src layout** (`src/mcp_gateway/`), PyPI-ready `pyproject.toml`
  (hatchling, classifiers, urls, MIT license, `py.typed`).
- Tooling: ruff (lint+format), mypy --strict, pytest+asyncio+cov. `Makefile` with
  install/dev/lint/format/typecheck/test/check/build/publish/run/clean.
- Package skeleton per plan §6: `app.py`, `hooks.py`, `connect.py`, `builder.py`,
  `search.py`, `models.py`, `store/{base,sqlite}.py`, `api/{servers,groups}.py`.
- `create_gateway()` assembles an **empty** FastMCP + HookMiddleware + admin router;
  `Gateway.install(app)` mounts `/admin` + `/mcp`. Admin routes wired → return `501`.
- Stubs raise `NotImplementedError("... — Milestone N")`.
- Tests: smoke (imports, gateway assembles, admin routes registered, 501 response).
- Docs: `README.md`, `CLAUDE.md`, `examples/basic_app.py`.

## Key decisions (all confirmed by Nir)

- **FastMCP v3.3.x, latest** (overrides plan decision #10). v3 API names recorded in
  CLAUDE.md (`create_proxy`, `mount(..., namespace=)`, middleware paths).
- **requires-python >=3.11** for PyPI reach. Dev venv 3.13 via `.python-version`.
- **MIT license**; project is niradler's personal pyproject author is
  name-only .
- Type checker: **mypy --strict** as the gate (not `ty` — still pre-1.0, risky on
  pydantic). ruff covers the fast dev loop.
- **Repo:** https://github.com/niradler/fast-mcp-gateway — PUBLIC, default branch
  `main`. Pushed: scaffold + .gitattributes (LF normalization).

## Resolved

- v2 vs v3 → v3 (latest). `main.py` stub removed. Windows `make` present (4.4.1).
- VSCode interpreter points at C:\Python312, not `.venv` — Nir to select
  `.venv\Scripts\python.exe` to clear the fastapi/fastmcp "not installed" hints.

## State (Milestone 1 — server CRUD + builder + reload + connect: DONE)

- `SqliteStore`: full server CRUD over one long-lived `aiosqlite` connection
  (`:memory:` survives for the store's life). JSON columns for dict/list fields.
  Domain errors: `KeyError` (missing) → 404, `ValueError` (dup name) → 409. Groups
  still M3 stubs. Added `initialize()` to the `Store` protocol.
- `build_client_factory`: async factory. `resolve_connect_settings` runs
  `pre_mcp_connect`, layering hook headers over static (dynamic wins) + timeout
  override; builds `StreamableHttpTransport`/`SSETransport` → `ProxyClient(timeout=)`.
- `GatewayBuilder.reload`: snapshots baseline `mcp.providers` at init; each reload
  resets to baseline then mounts `FastMCPProxy(client_factory=...)` per **enabled**
  server under `namespace=server.name`. Idempotent; drops removed/disabled servers.
  (FastMCP has no `unmount`; `mcp.providers` is the mount registry.)
- `api/servers.py`: real CRUD + `/{id}/test` (handshake → `{ok, tool_count}`) +
  `/{id}/tools` (live `list_tools`, 502 on failure). Router now takes `hooks`.
- `app.py`: `create_gateway` composes a startup lifespan → `store.initialize()` +
  `builder.reload()` then the MCP app lifespan. `Gateway._lifespan` holds it.
- Tests: `test_store` (9), `test_connect` (6), `test_builder` (3), `test_servers_api`
  (9) + updated smoke. **36 pass**, mypy --strict clean, ruff clean.
- E2E proof: `.claude/scripts/m1_e2e.py` — real HTTP upstream → register → reload →
  `/tools` `['add']`, `/test` ok, gateway MCP exposes `math_add`, call → 5.

### Decision flagged (open for Nir)

Per-server **allow/deny is NOT enforced yet** — fields persist, but filtering is
deferred to M3 per the README roadmap (the builder's old stub note said M1). Say if
you want it pulled forward.

## State (Milestone 2 — pre_list_tools catalog filtering: DONE)

- **Contract change:** `ListToolsHook` is now `(context, catalog) -> catalog`
  (`Callable[[MiddlewareContext[Any], Sequence[Tool]], Awaitable[Sequence[Tool]]]`),
  mirroring `post_tool_call`'s `(context, result) -> result`. The old
  `(context) -> None` shape couldn't see or return the tool list. `Tool` imported
  from `fastmcp.tools.base` (matches FastMCP's own middleware signature).
- `HookMiddleware.on_list_tools`: gets the catalog via `call_next`, then threads it
  through each `pre_list_tools` hook in registration order — a hook can drop or
  rename tools. No native FastMCP tool filtering exists, so this is where catalog
  shaping lives.
- Tests: `test_hooks.py` +4 (drop, pass-through, ordered drop+rename chain, no-hooks)
  using the duck-typed context + `SimpleNamespace` tool stand-ins. **40 pass**, mypy
  --strict + ruff clean.
- E2E: `.claude/scripts/m2_e2e.py` — real `add` upstream → gateway with a
  `pre_list_tools` hook hiding `math_add` → `Client(gateway.mcp).list_tools()`
  confirms it's absent.

## Decisions for later milestones (confirmed by Nir)

- **M3 group exposure = Option B (group-scoped endpoints).** Alongside the full
  `/mcp`, each group is served at `/mcp/g/{group}` showing only that group's member
  servers with the group's allow/deny applied on top of per-server allow/deny. One
  shared parent FastMCP server + proxies (no per-group duplication); a `/mcp/g/{group}`
  dispatch stashes the group (ContextVar) and delegates to the same MCP app; the
  `HookMiddleware` reads `get_http_request()` path to scope the catalog and block
  out-of-scope/denied calls. FastMCP has **no** native glob filtering (only a
  `tool_names` rename map on `mount`), so allow/deny is enforced in `HookMiddleware`.

## Next (Milestone 3)

Groups + tool allow/deny + per-group membership (Option B endpoints). See decision
above. Then M4 (search/describe meta-tools + catalog cache), M5 (reference hooks,
docs, packaging).
