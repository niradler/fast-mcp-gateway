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

## State (Milestone 4 — search meta-tools + SQLite catalog: DONE)

**Decision (Nir): the catalog cache IS the SQLite store, not an in-memory layer.** The
persisted catalog is the single source of truth for both `tools/list` and search —
refreshed on `reload`, surviving restart, and giving FTS5 search "for free" (the staleness
profile is identical to a reload-invalidated in-memory cache, so it's a strict win).

- **`models.CatalogTool`:** one upstream tool captured in the snapshot — namespaced `name`
  (`"<ns>_<bare>"`), `bare_name`, `server_id`, `parameters`/`output_schema`/`tags`/
  `annotations` — enough to rebuild the MCP wire form without re-querying upstreams.
- **`Store` protocol + `SqliteStore`:** `replace_catalog` / `list_catalog` /
  `search_catalog` / `get_catalog_tool`. New `catalog_tools` table + **FTS5** virtual table
  `catalog_fts(name, bare_name, description, tags)`; search ranks by `bm25`. FTS5 presence
  is probed at `initialize()`; if absent, `search_catalog` falls back to an in-Python
  weighted-overlap scan (`_rank_by_overlap`). Free-text queries are sanitized to `[a-z0-9]`
  prefix tokens OR'd together (no FTS5 operator injection); empty/token-less queries browse.
- **`catalog.py`:** `collect_catalog` introspects every enabled upstream **concurrently**
  (`asyncio.gather`, per-server failures isolated — a dead upstream contributes nothing,
  reload latency bounded by the slowest single upstream). `catalog_tool_to_fastmcp` rebuilds
  a FastMCP `Tool` for the list path (base `Tool` is instantiable; `run` is never called).
- **`builder.reload()`:** after mounting, `collect_catalog(servers, hooks)` →
  `store.replace_catalog(...)`. So reload now does bounded network I/O (introspection).
- **`HookMiddleware.on_list_tools`:** when a `catalog` provider is wired it serves the
  snapshot instead of a live upstream fan-out (falls back to `call_next` when unset — keeps
  pre-M4 tests intact). `app._catalog_tools` merges `mcp.local_provider.list_tools()` (the
  local meta-tools) with the persisted upstream snapshot, so the meta-tools stay
  discoverable in `tools/list` (regression caught in e2e + `test_tools_list_merges_*`).
- **`search.py`:** `search_tools(query, limit)` (compact name/description/tags) and
  `describe_tool(name)` (full schema) read the store, then apply `AccessPolicy.filter_tools`
  for the request's group — a denied/out-of-scope tool reads as "not found" (no leak).
- **Tests:** `test_search.py` (9, via in-process `Client`), `test_store.py` +13 catalog
  tests. Fixed an interpreter-shutdown hang: aiosqlite worker threads are **non-daemon**, so
  the `store` fixtures and search tests must `close()` their stores. Full suite **135 pass**.
- **E2E:** `.claude/scripts/m4_e2e.py` — live uvicorn upstream (`add`/`subtract`/`delete_all`,
  registered `deny=["delete_*"]`). Proves reload introspects + persists the catalog;
  `tools/list` (from snapshot) shows `math_add`/`math_subtract` + meta-tools, hides
  `math_delete_all`; `search_tools`/`describe_tool` respect the policy with no leak.

Also fixed `tests/test_routing.py` ASGI `send` annotations (`dict` → `MutableMapping`) to
satisfy the gate after a starlette type tightening.

## Known follow-ups (security)

- **(a) Inbound MCP auth / open-proxy / confused-deputy risk.** The `/mcp` endpoint
  is currently unauthenticated. Any client that can reach the port can call all proxied
  tools, including those requiring upstream OAuth credentials held by the daemon. A
  future `inbound_auth` hook or FastMCP middleware guard is needed before exposing the
  daemon on a shared network.
- **(b) Token encryption-at-rest.** OAuth refresh tokens are stored as plain JSON files
  in the token cache directory (mode 0700, owner-only). A future enhancement should
  encrypt them at rest (e.g. via a `key_value` backend backed by a secrets vault or
  system keyring).

## OAuth plugin refactor (post-Milestone 6)

OAuth behavior moved from core `connect.py` into `src/fast_gateway/plugins/oauth.py`
as `OAuthPlugin` (Mode B only). Root cause: OAuth requires a human at a terminal to
complete the browser auth-code flow — running it inside the embedded Mode-A mount is
blocking and inappropriate for headless library use.

Changes: `default_oauth_token_dir` and `build_oauth` deleted from `connect.py` and
re-homed in `plugins/oauth.py`. `ConnectSettings` gains a generic `auth: Any | None`
field; `resolve_connect_settings` returns a 3-tuple `(headers, timeout, auth)`;
`_build_transport` gains an `auth` parameter and passes it to both transport
constructors. `OAuthPlugin._attach_oauth` hook returns `ConnectSettings(auth=...)` for
OAuth servers and `None` otherwise. `build_app` registers `OAuthPlugin()` unconditionally
alongside the HIL plugin. `cli.py` builds its login transport using `build_oauth` directly.
`create_gateway` (Mode A) receives nothing OAuth-related.

## Milestone 6 — Local CLI + Docker + browser HIL (DONE)

**Final state (verified):** full gate green — `ruff format`/`ruff check`/`check_slop`
clean, `mypy --strict` clean (48 src files), **223 tests pass**. Live smoke of
`fast-gateway serve` confirmed `/docs` 200, `/admin/servers` 200, `/admin/hil` 200 (HIL UI),
`/mcp/` 406 (correct MCP response to a bare GET). CLI `add`→reload→`list` and `group
create`/`list` exercised end-to-end against the running daemon. Fixed: subagent had used
em-dashes in `typer.echo`/log strings — ASCII-ified to avoid Windows-console
`UnicodeEncodeError` (docstring em-dashes left, matching repo style).

New files: `config.py`, `reference.py`, `factory.py`, `cli.py`, `hil/{__init__,plugin,
pending,views}.py`; tests `test_config/reference/hil/factory/cli.py`; `Dockerfile`,
`docker-compose.yml`, `.dockerignore`, `examples/gateway.toml`. pyproject: `cli` extra
(`typer`/`uvicorn`/`httpx`) + `[project.scripts] fast-gateway`. `__init__` exports the new
public API. README: "Run it locally (CLI & Docker)" section + browser-HIL + roadmap/status.

### Original plan (IN PROGRESS at the time)

**Goal (Nir):** ship a CLI + Docker so a user runs the gateway locally, registers all
their MCP servers once, and points Claude Code (and other coding agents) at the single
gateway endpoint — catalog auto-updates as servers are added, plus policy enforcement
and browser-based human-in-the-loop approvals.

### Decisions (confirmed by Nir, this session)

1. **Transport stays HTTP/SSE only.** No stdio/subprocess transport. Local stdio servers
   must be bridged to HTTP (document `fastmcp run --transport http` / `mcp-proxy`).
2. **Config model:** *policies live in a file*; a gateway config file (`gateway.toml`)
   holds settings. **Server/group CRUD is imperative** (CLI command or admin API) →
   persisted in the default SQLite. The config file does NOT declare servers/groups.
3. **Agent wiring is manual.** The user adds the gateway's `/mcp` endpoint to Claude Code
   the same way he adds any MCP server today. CLI `connect`/`info` just prints the exact
   endpoint + a paste-ready snippet (no config rewriting, no import). Auto-update of the
   agent's tool list comes free from one stable endpoint + `tools/list_changed` on reload.
4. **Build style:** clean, simple, consistent. Implementation delegated to the python
   agent. CLI uses a modern CLI lib — **Typer**.

### Build plan (modules)

- `config.py` — `tomllib` loaders → `GatewayConfig` (name, db, host, port, mcp_path,
  admin_prefix, admin_token, policy_file, hil settings) + `LocalPolicy` (deny, confirm
  globs, audit). Policy from its own file or inline `[policy]`.
- `reference.py` — reference hook factories: `audit_hook()`, `deny_hook(patterns)`,
  `confirm_hook(patterns)` (pre_tool_call → REQUIRE_CONFIRMATION). Pure, TDD.
- `hil/` plugin — `HumanApprovalPlugin`: `confirmation` hook creates a pending approval
  (uuid → `asyncio.Future[bool]`), opens the browser to an approval page showing the
  tool + args + reason, blocks until Approve/Deny (timeout → deny, fail-safe). Mounts an
  admin router (`/admin/hil`) serving the list + detail + decision routes. Fits the plugin
  contract (admin_router + confirmation hook + setup/teardown).
- `factory.py` — `build_gateway_from_config(config)` assembles store + reference hooks +
  HIL plugin → `create_gateway` → FastAPI app. Used by `serve`.
- `cli.py` — Typer app talking to the admin API via httpx: `serve` (boots uvicorn from
  config), `add`/`list`/`remove`/`enable`/`disable` (servers), `group …`, `reload`,
  `connect`/`info` (print endpoint + Claude Code snippet).
- ~~`builder.reload()` — emit `tools/list_changed`~~ **DEFERRED (RCA below).**

### Decision: tools/list_changed push deferred (not a fragile internals hack)

FastMCP v3.3.1 exposes **no** supported API to broadcast `notifications/tools/list_changed`
to active sessions from an off-session reload (the admin `/reload` runs on a different
connection than the MCP client sessions). `ServerSession.send_tool_list_changed()` exists
in low-level `mcp`, but reaching the live sessions means digging into the private
`StreamableHTTPSessionManager` internals — version-coupled and brittle. **Verdict: don't
build it.** The goal ("agent updated when a server is added") is met by the stable-endpoint
property: the gateway is ONE endpoint, so adding an upstream needs **zero agent
reconfiguration**; the agent picks up new tools on its next `tools/list`/reconnect (how MCP
clients refresh on session init). Real-time push is a clean enhancement to revisit if/when
FastMCP exposes a supported broadcast. README + CLI `connect` framing reflects this honestly.

### Packaging

- pyproject: `[project.scripts] fast-gateway = "fast_gateway.cli:app"`; `cli` extra
  (`typer`, `uvicorn[standard]`, `httpx`). Core stays lean.
- `Dockerfile` + `docker-compose.yml` + `.dockerignore` (gateway + CLI; HIL
  auto-open off in-container, approval_base_url set to the host-reachable URL).

### Orchestration

python-expert subagents build modules + tests sequentially (shared `.venv`/caches make
parallel runs race). Subagents do NOT edit `__init__.py`/`pyproject.toml`/README — the
main thread owns those central edits. Each subagent must pass the full gate
(`ruff`, `mypy --strict`, `tools/check_slop.py`, `pytest`). No inline comments.

## Plugin folders + programmatic access (June 2026 session)

Branch `feat/plugin-folders-and-programmatic-api` (PR #5).

- **Folder-per-plugin layout:** every plugin is `plugins/<name>/` with `__init__.py`
  re-exports + `plugin.py` (convention in CLAUDE.md). Four bundled plugins: tools_api,
  hil, oauth, agentos. A plugin must carry real logic; plain deny/confirm/audit stays
  as the reference hooks (`build_app` composes them directly).
- **Programmatic in-process access (no HTTP loopback):** `Gateway.client()` returns
  `fastmcp.Client(self.mcp)`; `Gateway.call_tool(name, args, group=, timeout_seconds=)`
  and `Gateway.list_tools(group=)` are one-shot conveniences. Group scoping works by
  setting `access.current_group` BEFORE entering the client context (the in-process
  server task copies the caller's context at `__aenter__`). Tests: `test_gateway_client.py`.
- **`ToolsApiPlugin`** (`plugins/tools_api/`, name="tools"): REST bridge under
  `/admin/tools` — GET list (`?group=`), GET `/{name}` schema (404 hides denied), POST
  `/{name}/call` (errors in-band via `is_error`, mirroring MCP wire semantics; uses
  `raise_on_error=False`). Wired in `build_app`; the admin bearer guard covers it
  (test-proven). Live validator: `scripts/validate_tools_api.py`.
- **agentos additions:** `enable_mcp_security_scan` (tool-poisoning scan on
  `pre_list_tools`, drops flagged tools when `fail_closed`) and `enable_rate_limiting`
  (per-group sliding window on `pre_tool_call`). Audit verdict: the rest of the toolkit
  needs an agent runtime and does not fit a stateless proxy. Live-validated via
  `examples/poisoned_upstream.py` + env toggles on `examples/agentos_gateway.py`.
- aiohttp pinned past CVE-2026-34993/47265 (3.14.1); `pip-audit` clean.
- **Test gotcha:** holding the gateway lifespan open across an async pytest fixture yield
  crashes at teardown (anyio cancel scope exits in a different task). Enter the lifespan
  INSIDE each test via an `asynccontextmanager` helper instead (see `test_tools_api_plugin.py`).
- Governed-policy-without-upstreams test trick: register servers `enabled=False` (reload
  skips mounting/introspection but `policy.rebuild` sees ALL servers), then seed the
  catalog with `store.replace_catalog` AFTER reload.

## Upstream auth expansion + HIL API + startup perf (June 2026 session)

Same branch. Decisions made with Nir, implemented and live-validated
(`scripts/validate_upstream_auth.py`, 18/18).

- **Secret refs (core, not a plugin):** `secret_refs.py` resolves `${env:VAR}` /
  `${file:path}` inside `static_headers` values at connect time (`connect.py`). Chosen
  over `ServerAuth.BEARER/HEADER` enum fields — reuses the existing header mechanism,
  covers any scheme/multiple headers, zero new model fields. Registry/admin API only
  ever hold the reference.
- **OAuth client_credentials (in `plugins/oauth/`, importable standalone):**
  `ServerAuth.OAUTH_CLIENT_CREDENTIALS` + `oauth_token_url`/`oauth_client_id`/
  `oauth_client_secret` (validator REJECTS raw secrets — must be a secret ref).
  `ClientCredentialsAuth` (httpx.Auth, client_secret_post, in-memory token cache,
  expiry skew 60s, one forced refresh on 401). `client_credentials_hook()` keeps one
  provider per server-config so reconnects don't hammer the token endpoint; Mode A
  uses the hook directly, `OAuthPlugin` covers Mode B. httpx now an explicit dep.
- **Store:** 3 new nullable sqlite columns (auto-migrated); `update_server` now
  re-validates the merged record (was `model_copy`, which skips validators) so a patch
  can never persist a row that fails to load later.
- **HIL:** `HumanApprovalPlugin(notifier=...)` — async `(approval, url)` callable,
  browser-open is the default; notifier failure logs + keeps waiting. JSON API
  alongside HTML: `GET /admin/hil/pending`, `GET /admin/hil/pending/{id}`,
  `POST .../approve|deny` (declared before the HTML `/{approval_id}` catch-all —
  route order matters).
- **Startup perf (Nir's ask):** root cause = lifespan blocking on `collect_catalog`
  fan-out (one dead upstream stalls boot up to its timeout). Builder split into
  `rebuild_mounts` (pure in-memory; mounts are lazy) + `refresh_catalog` +
  `refresh_server(id)`. `create_gateway(startup_catalog="refresh"|"background"|"skip")`
  — library default `refresh` (back-compat), daemon config default **background**
  (serves instantly with last-known catalog; `Gateway.startup_catalog_task` exposes the
  in-flight refresh for tests/await). New `POST /admin/servers/{id}/refresh`
  re-introspects one server; `store.replace_server_catalog` touches only its rows.
- CLI: `add --oauth-token-url/--oauth-client-id/--oauth-client-secret` (mutually
  exclusive with `--oauth`); header refs need no CLI change.
- Also fixed: `/admin/servers/{id}/test` and `/tools` ran `await factory()` outside
  their try block — a connect-settings error (unresolvable ref) was a 500, now
  reported in-band / as 502.
- **Error hook seams (Nir's ask):** `Hooks.tool_error` (ctx, exc — fires on policy
  block, deny, rejected confirmation, upstream failure; `on_call_tool` wraps the whole
  body) and `Hooks.connect_error` (server, exc — fires once per failed server from
  `collect_catalog`, i.e. reload/startup/per-server refresh; live-call connect failures
  surface via `tool_error` instead, deliberately no double-fire). Both observe-only:
  dispatch helpers on `Hooks` log hook exceptions and never mask the original error.
  Reference `audit_error_hook` / `audit_connect_error_hook` wired by `build_app` under
  `policy.audit` — audit now covers failures, not just successes.

## Earlier Next (Milestone 5)

Reference hooks (audit, allow/deny, confirmation), docs, packaging dry-run — do NOT publish.

NOTE: a parallel agent is building the **plugins** feature (`plugins.py`, `test_plugins.py`,
`test_agt_plugin.py`, `merge_hooks`, plugin wiring in `create_gateway`). M4 coexists with it,
but those two **test files currently have mypy errors** (their WIP) — the full `mypy` gate is
red solely on `test_plugins.py` + `test_agt_plugin.py`; M4 code/tests are clean.
