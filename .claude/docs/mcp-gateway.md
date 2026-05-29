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

## State (Milestone 3B — per-server allow/deny enforcement: DONE)

- **New module `src/mcp_gateway/access.py`:** `AccessPolicy` + `current_group: ContextVar[str|None]`.
  - `rebuild(servers, groups)`: pre-compiles `_ServerRules` (allow/deny list) per namespace
    and `_GroupRules` (member namespaces + allow/deny) per group. Namespace list sorted
    longest-first for correct longest-prefix splitting.
  - `split_namespace(tool_name)`: longest-prefix match against known namespaces with
    separator `_<bare>`. Returns `(None, name)` for non-namespaced tools (always allowed).
  - `_rule_allows(bare, allow, deny)`: deny wins; empty allow = allow all.
  - `allows(tool_name, group=None)`: server rules first, then group gate (membership +
    group allow/deny on top). Unknown group → False.
  - `filter_tools(tools, group=None)`: list comprehension over `allows`.
  - Group fields are fully implemented now; M3c only adds the routing shim that sets
    `current_group`.
- **`HookMiddleware` (`hooks.py`):** gains `policy: AccessPolicy | None = None`.
  - `on_list_tools`: policy filter BEFORE user hooks (preserves original namespaced names
    for correct splitting; user hooks may rename after).
  - `on_call_tool`: policy check BEFORE pre_tool_call hooks; raises `ToolError("Tool X is not permitted.")` on deny.
- **`GatewayBuilder` (`builder.py`):** gains `policy: AccessPolicy | None = None`;
  `reload()` fetches both `list_servers()` + `list_groups()` and calls `policy.rebuild()`
  on the FULL server list (all servers, not just enabled) before remounting.
- **`app.py`:** `create_gateway` constructs `AccessPolicy()`, passes it to both
  `HookMiddleware` and `GatewayBuilder`.
- **Tests:** 89 total (27 new). `test_access.py` (18), `test_hooks.py` +7, `test_builder.py` +6.
  mypy --strict clean. ruff clean.
- **E2E:** `.claude/scripts/m3b_e2e.py` — upstream with `add/sub/delete_all`, registered
  with `deny=["delete_*"]`. Confirmed: `math_delete_all` absent from `list_tools`, and
  calling it raises `ToolError: Tool 'math_delete_all' is not permitted.`.

## State (Milestone 3C — group-scoped endpoints, Option B: DONE)

- **New module `src/mcp_gateway/routing.py`:** `GroupDispatch` ASGI shim + `split_group_path`.
  - Mounted at `{mcp_path}/g` (before the full `{mcp_path}` mount so the longer prefix
    wins) in `Gateway.install`. Serves the SAME shared `mcp_app` — no per-group proxy
    duplication.
  - **Key correction:** modern Starlette `Mount` does NOT strip the matched prefix from
    `scope["path"]`; it records the prefix in `scope["root_path"]` and the downstream
    router resolves `route_path = path - root_path`. The shim therefore *folds the
    `{group}` segment into `root_path`* (rather than rewriting `path`), so the shared MCP
    app's router resolves to the transport root. It sets `current_group` for the request
    and resets it in `finally`. (First attempt rewrote `path` → 404; fixed.)
  - Lifespan: the real lifespan stays owned by the host app via `gateway.lifespan`;
    Starlette `Mount` never forwards `lifespan` scopes to mounts, but the shim answers
    the lifespan protocol as a defensive no-op anyway.
- **`app.py`:** `Gateway` gains `_transport_path`; `install()` gains `group_segment="g"`
  and mounts `GroupDispatch(mcp_app, transport_path)` ahead of the full mount.
- **Tests:** `test_routing.py` (shim path-folding incl. no-trailing-slash, ContextVar
  set/reset, no scope mutation, lifespan no-op) + `test_access.py` `__`-in-bare-name
  split/allow-deny tests (per Nir's note: FastMCP joins ns+tool with a single `_`, and
  longest-prefix matching splits at the namespace boundary — never on an arbitrary `__`).
- **E2E:** `.claude/scripts/m3c_e2e.py` — LIVE uvicorn (2 upstreams `math`/`text` + group
  `analytics` member=math, group deny `delete_*`). Proves: full `/mcp` shows all 5 tools;
  `/mcp/g/analytics` shows only `math_add`/`math_sub` (text out of scope, `math_delete_all`
  group-denied); unknown group → empty; denied/out-of-scope calls error; full `/mcp` still
  calls `math_add`→5 (ContextVar reset, no scope leak).

**Milestone 3 is complete** (3a server allow/deny, 3b enforcement, 3c group endpoints).

## Next (Milestone 4)

`search_tools` / `describe_tool` meta-tools + a reload-invalidated catalog cache. Then
M5 (reference hooks, docs, packaging dry-run — do NOT publish).

NOTE: a parallel agent is building the **plugins** feature (`src/mcp_gateway/plugins.py`,
`tests/test_plugins.py`, `merge_hooks` in `hooks.py`, plugin wiring in `create_gateway`).
M3C coexists cleanly with it; the combined gate is green (107 pass).
