# fast-gateway

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org)
[![Built on FastMCP](https://img.shields.io/badge/built%20on-FastMCP%20v3-orange.svg)](https://gofastmcp.com)
[![FastAPI](https://img.shields.io/badge/mounts%20on-FastAPI-009688.svg)](https://fastapi.tiangolo.com)
[![Checked with mypy](https://img.shields.io/badge/types-mypy%20strict-2a6db2.svg)](https://mypy-lang.org)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-d7ff64.svg)](https://docs.astral.sh/ruff/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A lean Python package that mounts on **FastAPI** and turns a registry of upstream
**MCP servers** into one governed, namespaced MCP endpoint. The core stays thin;
everything cross-cutting — auth, policy, human-in-the-loop, redaction, audit, cost
limits — is a **hook function** you pass at mount time, or a **plugin** that bundles
several together.

```text
many upstream MCP servers  ──►  fast-gateway  ──►  one governed /mcp endpoint
   github, slack, jira…          (namespaced + filtered + policy-checked)
```

> [!NOTE]
> **Status: 0.0.3, under active development.** APIs may change. Implemented and tested:
> the server registry, proxy builder, full hook pipeline, groups with allow/deny,
> group-scoped endpoints, the plugin system, the `search_tools` / `describe_tool`
> meta-tools, the bundled reference hooks (audit / deny / confirm), the local
> `fast-gateway` CLI + config/policy files, the browser human-in-the-loop plugin, and a
> Docker image — see the [roadmap](#roadmap).

## Why

Point an LLM at a dozen MCP servers directly and you get a dozen connections, a dozen
auth schemes, no namespacing, no central policy, and no way to hide a dangerous tool.
`fast-gateway` puts one endpoint in front of them all:

- **One connection, many servers** — register upstreams in a store; the gateway
  proxies each under its own namespace (`github_*`, `slack_*`, …).
- **Governed** — filter which tools are exposed, gate calls behind policy or
  human approval, redact results, audit everything — all as hooks.
- **Reuse, don't rebuild** — [FastMCP](https://gofastmcp.com) already does proxying,
  transport bridging, composition/namespacing, and protocol middleware. This package
  builds only what it lacks: the registry, groups, the builder, the hook runner, and
  the plugin seam.

## Features

- **Server registry (Store)** — persistent CRUD over upstream MCP servers; ships with
  a zero-setup `SqliteStore`, swappable for Postgres / Redis / in-memory via one
  protocol.
- **Namespaced proxying** — each enabled server is mounted under its `name` as a
  prefix; `reload()` rebuilds the mounts from the registry.
- **Five hook seams** — `pre_mcp_connect`, `pre_list_tools`, `pre_tool_call`,
  `confirmation`, `post_tool_call`. Auth, policy, HIL, redaction, audit, and cost
  limits are all plain async functions.
- **Access control** — per-server and per-group **allow/deny** glob lists, enforced on
  both `list_tools` (hides) and `call_tool` (blocks).
- **Groups & group-scoped endpoints** — expose a curated subset of servers/tools at
  `/mcp/g/{group}`, served by the same shared MCP app (no per-group duplication).
- **Plugins** — bundle hooks, FastMCP middleware, an admin router, ASGI mounts, and
  meta-tools into one named extension with `setup` / `teardown`. Four ship in the box:
  tools REST API, browser HIL, upstream OAuth, and agent-os.
- **Programmatic tool access** — `gateway.call_tool(...)` / `gateway.list_tools()` /
  `gateway.client()` drive the gateway in-process through the full governance chain,
  no HTTP loopback.
- **Tools REST API** — `ToolsApiPlugin` exposes list / describe / invoke routes so
  non-MCP clients (dashboards, scripts) can use the governed catalog over plain HTTP.
- **Optional policy engine** — an `agt` extra wires Microsoft's
  [agent-governance-toolkit](https://github.com/microsoft/agent-governance-toolkit)
  (agent-os) in as a policy plugin.
- **Local CLI & Docker** — `fast-gateway serve / add / list / group / connect`, a TOML
  config + policy file, a **browser human-in-the-loop** approval page, and a Docker image
  for self-hosting.
- **Typed throughout** — `mypy --strict`, `py.typed`, full type hints.

## Architecture

```text
FastAPI app
 ├── /admin       → APIRouter (CRUD: servers, groups, reload)         [we build]
 ├── /mcp         → FastMCP.http_app()  (the gateway MCP server)      [FastMCP]
 │                    ├── mount(proxy_github, namespace="github")
 │                    ├── mount(proxy_slack,  namespace="slack")      ← namespacing
 │                    └── HookMiddleware + search meta-tools
 └── /mcp/g/{grp} → same MCP app, scoped to one group's servers/tools [we build]
```

The gateway is a **parent FastMCP server** that proxies each registered upstream and
mounts it under a namespace, exposed as an ASGI app you mount onto your own FastAPI app
alongside an admin router for CRUD. A `HookMiddleware` and an `AccessPolicy` wrap every
`list_tools` / `call_tool` request.

## Two ways to run it

The same gateway runs in two distinct modes — pick the one that fits:

| | **A. Embed (library)** | **B. Standalone (CLI/Docker)** |
| --- | --- | --- |
| Who | App developers | Anyone running it locally / self-hosted |
| How | `create_gateway(...)` + `gateway.install(your_fastapi_app)` | `fast-gateway serve --config gateway.json` |
| Governance | You write hooks / plugins in Python | JSON `policy` + bundled reference hooks |
| Human-in-the-loop | Your own `confirmation` hook | Built-in **browser** approval plugin |
| Where it lives | Inside your service, your routes, your auth | A daemon you point your agent at |
| Start here | [Quickstart](#quickstart) | [Run it locally](#run-it-locally-cli--docker) |

**Mode A** mounts the gateway *into your own API*, controlling every seam in Python — the path
to production. **Mode B** is a complete, ready-to-use local daemon: no Python, config-driven,
with the browser HIL and OAuth login already wired in — use it as-is to put your MCP servers
behind one governed endpoint. It is also the most direct way to *learn* fast-gateway: its entire
wiring is one small function, [`factory.build_app`](src/fast_gateway/factory.py), that composes
`create_gateway` with the bundled reference hooks and the HIL plugin. Experiment locally with
policy, groups, and approvals, then read `factory.build_app` to see exactly how to assemble your
own gateway when you move to Mode A.

## Getting started

### Prerequisites

- Python **3.11+**
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install

```bash
uv add fast-gateway        # or: pip install fast-gateway
```

### Quickstart

> **Mode A — embed in your FastAPI app.** Mount the gateway into your own service and wire
> governance in Python. For the no-code standalone daemon, see
> [Run it locally](#run-it-locally-cli--docker) (Mode B).

```python
import os
from fastapi import FastAPI
from fast_gateway import ConnectContext, ConnectSettings, Hooks, SqliteStore, create_gateway


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

Register an upstream server through the admin API, then reload:

```bash
curl -X POST http://127.0.0.1:8000/admin/servers \
  -H 'content-type: application/json' \
  -d '{"name": "math", "url": "http://127.0.0.1:9000/mcp/", "transport": "http"}'

curl -X POST http://127.0.0.1:8000/admin/reload
```

Its tools now appear at the gateway endpoint under the `math_` namespace.

### Run the bundled example

```bash
make run          # uv run uvicorn examples.basic_app:app --reload
# Admin + OpenAPI docs: http://127.0.0.1:8000/docs
# MCP endpoint:         http://127.0.0.1:8000/mcp/
```

## Run it locally (CLI & Docker)

> **Mode B — standalone daemon.** This section is the no-code path: a configured daemon you
> point your agent at. For embedding the gateway in your own FastAPI app, see
> [Quickstart](#quickstart) (Mode A) instead.

Don't want to write Python? Install the CLI extra and run the gateway as a local daemon,
then point your coding agent (Claude Code, etc.) at the one endpoint.

```bash
uv tool install --from fast-gateway 'fast-gateway[cli]'   # or: pipx install 'fast-gateway[cli]'
# or, from a checkout: uv tool install --from . 'fast-gateway[cli]'
fast-gateway serve
# Created default config at /Users/you/.fast-gateway/gateway.json   ← first run only
# MCP endpoint  : http://127.0.0.1:8000/mcp/
# Admin API     : http://127.0.0.1:8000/admin
# OpenAPI docs  : http://127.0.0.1:8000/docs
# OAuth status  :   - datadog: ready                               ← per OAuth upstream
```

`fast-gateway` auto-creates `~/.fast-gateway/gateway.json` (with sane policy + HIL
defaults) on first run; subsequent runs reuse it. Override with `--config <path>`,
`$FAST_GATEWAY_CONFIG`, or drop a `./gateway.json` next to your shell — the CLI checks
all three in that order.

Register your upstream MCP servers once, from another shell — they persist in the
SQLite registry and reload live:

```bash
fast-gateway add deepwiki https://mcp.deepwiki.com/mcp
fast-gateway add context7 https://mcp.context7.com/mcp --deny "*_delete_*"
fast-gateway list
fast-gateway group create readonly
fast-gateway group members readonly --server deepwiki --server context7
```

Then connect your agent **once** — and never reconfigure it again:

```bash
fast-gateway connect          # prints the `claude mcp add …` command + a .mcp.json block
# claude mcp add --transport http gateway http://127.0.0.1:8000/mcp/
```

Because the agent points at one stable endpoint, **adding more servers later needs no
agent changes** — the new tools flow through the same `/mcp` and appear the next time the
agent lists tools (on (re)connect). See `fast-gateway --help` for `remove`, `enable`,
`disable`, `reload`, and `group` commands.

> [!NOTE]
> Upstreams are **HTTP/SSE** only. To put a local **stdio** server (e.g. `npx …`) behind
> the gateway, bridge it to HTTP first — `fastmcp run your_server.py --transport http`
> or a tool like `mcp-proxy` — then `fast-gateway add` the resulting URL.

### Token / API-key upstreams (secret references)

Most upstreams just want a static bearer token or `X-API-Key` header. Don't put the
secret in the registry — header values support `${env:VAR}` and `${file:path}`
references, resolved at connect time. The registry (and the admin read API) only ever
see the reference; rotation needs no registry change:

```bash
fast-gateway add weather https://api.example.com/mcp \
  --header 'Authorization=Bearer ${env:WEATHER_TOKEN}'
fast-gateway add billing https://billing.example.com/mcp \
  --header 'X-API-Key=${file:/run/secrets/billing-key}'
```

An unresolvable reference fails loudly at connect time (visible via
`POST /admin/servers/{id}/test`) instead of sending the literal placeholder upstream.

### Headless OAuth: client credentials (machine-to-machine)

For server-to-server OAuth — no browser, no human — register the upstream with the
`client_credentials` grant. The gateway fetches tokens from the token endpoint on
demand, caches them in memory, and refreshes before expiry (and once on a 401):

```bash
fast-gateway add machine https://api.example.com/mcp \
  --oauth-token-url https://idp.example.com/oauth/token \
  --oauth-client-id my-client \
  --oauth-client-secret '${env:MACHINE_CLIENT_SECRET}'
```

The client secret **must** be a `${env:}`/`${file:}` reference — raw secrets are
rejected at registration so they never land in the registry. Works in both modes; in
Mode A either register `OAuthPlugin()` or add just the hook:

```python
from fast_gateway.plugins.oauth import client_credentials_hook

hooks = Hooks(pre_mcp_connect=[client_credentials_hook()])
```

### OAuth-protected upstreams (browser login)

Many hosted MCP servers (Datadog, etc.) require an interactive OAuth login. Register
the server with `--oauth`, then run the browser login **once** — tokens are cached on
disk and refreshed automatically thereafter, so the gateway connects unattended after that.

```bash
fast-gateway add datadog https://<your-dd-mcp-endpoint> --oauth --scope read
fast-gateway login datadog          # opens the browser, completes OAuth, caches tokens
fast-gateway logout datadog         # clear the cached tokens for a server
```

Run `login` up front to avoid a browser popup during a daemon reload. Tokens live under
`~/.fast-gateway/oauth` (override with `oauth_token_dir` in the config or
`$FAST_GATEWAY_OAUTH_DIR`); the cache is shared between the CLI and the daemon. The CLI
auto-discovers the same config the daemon uses, so a customised `oauth_token_dir` is
picked up automatically — no need to pass `--config` to `login`/`logout` unless you want
to point at a non-default file. For headless hosts, run `login` on a machine with a
browser, or use the upstream's API-key header fallback via `--header`. After a fresh
`fast-gateway add datadog … --oauth`, the CLI prints `Next: fast-gateway login datadog`
as a reminder, and `fast-gateway serve` reports per-server token status at startup so
you can spot a missing login before any agent traffic hits it.

> [!WARNING]
> **Security: the `/mcp` endpoint is unauthenticated.** The daemon holds upstream OAuth
> refresh tokens, so **do not expose the mapped port to untrusted networks**. Set
> `admin_token` in your config to protect the admin API. Inbound MCP authentication and
> encryption-at-rest for the token cache are planned follow-ups — see the
> [roadmap](#roadmap).

> [!NOTE]
> The **browser** OAuth flow is Mode-B-only (`OAuthPlugin`, wired automatically by
> `build_app`): it needs a human at a terminal, so it is not appropriate for a headless
> library embedding. In Mode A use secret-ref headers, the `client_credentials` grant,
> or inject auth via a `pre_mcp_connect` hook — see the [Quickstart](#quickstart) example.

### Human-in-the-loop, in the browser

The config's `policy` object drives governance with plain globs: `deny` hard-blocks tools,
`confirm` routes them through human approval, and `audit` logs every call.

```jsonc
// gateway.json
{
  "policy": {
    "confirm": ["*_delete_*", "*_write_*"],   // these need a human "yes"
    "audit": true
  },
  "hil": { "enabled": true, "auto_open_browser": true }
}
```

When an agent calls a `confirm`-matched tool, the gateway **opens a browser approval page**
showing the tool name, its arguments, and the reason, and **blocks the call** until you
click **Approve** or **Deny** (a timeout denies — fail-safe). The approval page lives at
`/admin/hil`; in a headless/Docker run the approval URL is logged for you to open manually.

Approvals are also fully programmable — a JSON API lives alongside the HTML pages, so a
Slack bot, custom UI, or CI job can decide instead of a browser tab:

```bash
curl  $GW/admin/hil/pending                        # list pending approvals (JSON)
curl  $GW/admin/hil/pending/{id}                   # one approval: tool, args, reason
curl -X POST $GW/admin/hil/pending/{id}/approve    # or .../deny
```

And how operators get *told* about a pending approval is pluggable: pass any async
callable as the notifier (browser-open is just the default). A failing notifier never
blocks the decision — the approval stays pending for the API/UI.

```python
from fast_gateway.plugins.hil import HumanApprovalPlugin, PendingApproval

async def notify_slack(approval: PendingApproval, url: str) -> None:
    await slack.post(f"Approve `{approval.tool_name}`? {url}")

plugins = [HumanApprovalPlugin(notifier=notify_slack)]
```

### Docker

```bash
cp examples/gateway.json gateway.json     # edit to taste
docker compose up --build                 # serves on http://localhost:8000
fast-gateway add deepwiki https://mcp.deepwiki.com/mcp   # CLI talks to the mapped port
```

The image ships the gateway + CLI; the registry DB persists on the `gateway-data` volume.
(stdio upstreams need their runtimes — bridge them to HTTP as noted above.)

## Hooks

A hook is an async function, grouped in a `Hooks` container and passed at mount time.
Each binds to the layer where it belongs:

| Hook | Binds to | Runs |
| --- | --- | --- |
| `pre_mcp_connect` | proxy client factory | before opening an upstream session |
| `pre_list_tools` | `HookMiddleware.on_list_tools` | on catalog requests |
| `pre_tool_call` | `HookMiddleware.on_call_tool` (pre) | before forwarding a call |
| `confirmation` | `on_call_tool` (when `REQUIRE_CONFIRMATION`) | human-in-the-loop approval |
| `post_tool_call` | `HookMiddleware.on_call_tool` (post) | after the upstream result |

Hooks chain in registration order. A `pre_tool_call` hook may **continue**, **mutate
args**, **deny**, or return **`REQUIRE_CONFIRMATION`** — which triggers the
`confirmation` hooks.

> [!IMPORTANT]
> Confirmation is **fail-safe**: if any confirmation hook rejects, or none is
> registered, the call is denied. Policy, guardrails, audit, and cost limits are all
> just hooks — nothing special in the core.

```python
from fast_gateway import Hooks, ToolCallResult, ToolDecision


async def block_deletes(ctx) -> ToolCallResult | None:
    if ctx.message.name.endswith("_delete_all"):
        return ToolCallResult(decision=ToolDecision.REQUIRE_CONFIRMATION, reason="destructive")
    return None


async def approve(ctx) -> bool:
    return await ask_a_human(ctx.tool_name, ctx.arguments)  # your HIL channel


hooks = Hooks(pre_tool_call=[block_deletes], confirmation=[approve])
```

## Access control

Every server record carries `allow` / `deny` glob lists; groups carry their own on top.
`deny` wins over `allow`; an empty `allow` means "allow all". The policy is enforced in
two places: hidden from `list_tools` and blocked at `call_tool`.

```jsonc
// POST /admin/servers
{ "name": "fs", "url": "...", "deny": ["delete_*", "*_admin"] }
```

### Groups & group-scoped endpoints

Create a group, set its membership, and a curated view is served at
`/mcp/g/{group}` — showing only that group's member servers with the group's
allow/deny applied **on top of** each server's own rules. One shared parent server
backs every group view; there is no per-group proxy duplication.

```text
/mcp                 → all enabled servers, every permitted tool
/mcp/g/analytics     → only the 'analytics' group's servers & tools
```

## Plugins

A **plugin** is a named bundle of extensions applied at `create_gateway` time. Where a
single hook is one function, a plugin can contribute hooks **and** FastMCP middleware
(for around-the-call control like circuit breaking or retry), an admin `APIRouter`,
ASGI sub-app mounts, meta-tool registration, and async `setup` / `teardown` bound to
the gateway lifespan.

```python
from fast_gateway import create_gateway, SqliteStore

gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    plugins=[MyAuditPlugin(), MyRateLimitPlugin()],
)
```

A plugin implements the `Plugin` protocol: a `name`, a `contributions(context)` method
returning `PluginContributions`, and `setup` / `teardown` coroutines. The
`GatewayContext` it receives exposes the `store`, the parent `mcp`, and a `reload`
callable. These authoring types are top-level exports:

```python
from fast_gateway import Plugin, PluginContributions, GatewayContext
```

### Bundled plugins

Each plugin lives in its own folder under
[`src/fast_gateway/plugins/`](src/fast_gateway/plugins/) — use them as-is or as
templates for your own:

| Plugin | Import | What it adds |
| --- | --- | --- |
| **tools** | `ToolsApiPlugin()` | REST routes to list / describe / invoke the governed tools (`/admin/tools`) |
| **hil** | `HumanApprovalPlugin(config, notifier=...)` | approval gate for calls flagged `REQUIRE_CONFIRMATION`: browser page + JSON decision API, pluggable notifier |
| **oauth** | `OAuthPlugin()` | upstream OAuth at connect time: browser flow (CLI/daemon) + headless `client_credentials` |
| **agentos** | `AgtAgentOsPlugin(settings)` | Microsoft agent-governance-toolkit policy engine (experimental, `agt` extra) |

Plain deny / confirm / audit governance needs no plugin — pass the reference hooks
(`deny_hook`, `confirm_hook`, `audit_hook`) directly. The Mode-B daemon
(`factory.build_app`) wires the reference hooks, tools, oauth, and (when enabled)
hil automatically; in Mode A you pass exactly the ones you want to `create_gateway`:

```python
from fast_gateway import Hooks, ToolsApiPlugin, create_gateway, deny_hook, SqliteStore

gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    hooks=Hooks(pre_tool_call=[deny_hook(["*_delete_*"])]),
    plugins=[ToolsApiPlugin()],
)
```

### Optional: agent-os plugin (experimental)

> [!WARNING]
> The `agt` integration is **experimental**. Its upstream dependency is not yet on
> PyPI, so it installs only inside a uv project (see the install note below).

The `agt` extra wires Microsoft's
[agent-governance-toolkit](https://github.com/microsoft/agent-governance-toolkit)
(agent-os) in as `AgtAgentOsPlugin`. Its core capability is the **policy engine**: it
evaluates policy for every tool call — scoped to the active group — and denies calls the
policy rejects. Additional agent-os capabilities are opt-in toggles on `AgtAgentOsSettings`
(which reuses agent-os's own config types — `DetectionConfig`, `IntentCategory`, `EgressRule`):

| Toggle | Seam | Effect |
| --- | --- | --- |
| `enable_prompt_injection` | `pre_tool_call` | deny calls whose arguments look like prompt injection |
| `enable_semantic_policy` (+ `semantic_deny`) | `pre_tool_call` | deny calls whose classified intent is dangerous |
| `enable_response_scan` | `post_tool_call` | block responses flagged unsafe (credential/PII/threat) |
| `enable_credential_redaction` | `post_tool_call` | redact secrets/PII out of responses |
| `enable_egress_policy` (+ `egress_rules`) | `pre_mcp_connect` | refuse upstreams whose URL is outside the allowlist |
| `enable_mcp_security_scan` | `pre_list_tools` | scan tool definitions for poisoning (hidden instructions, unicode tricks, schema abuse); drop flagged tools when `fail_closed` |
| `enable_rate_limiting` (+ `rate_limit_max_calls`, `rate_limit_window_seconds`) | `pre_tool_call` | per-group sliding-window call budget; deny on exhaustion |

```bash
uv add "fast-gateway[agt]"   # from within a uv project — honors the git source
```

```python
from fast_gateway import create_gateway, SqliteStore
from fast_gateway.plugins.agentos import AgtAgentOsPlugin, AgtAgentOsSettings

gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    plugins=[
        AgtAgentOsPlugin(
            AgtAgentOsSettings(
                policy_dir="./policies",
                fail_closed=True,
                enable_prompt_injection=True,
                enable_response_scan=True,
                enable_credential_redaction=True,
            )
        )
    ],
)
```

> [!NOTE]
> The `agt` extra is sourced from the agent-governance-toolkit GitHub monorepo (via uv
> `[tool.uv.sources]`) until `agent-os-kernel` 4.x is published to PyPI. Because of that
> git source, install it from within a uv project (`uv add "fast-gateway[agt]"`),
> which honors the source; a plain `pip install "fast-gateway[agt]"` cannot resolve
> the dependency and will fail until it lands on PyPI. Upstream, `agent-os-kernel` is
> being renamed/consolidated to `agent-governance-toolkit-core`. The gateway and the
> plugin system work fully **without** the extra — only this one integration needs it.

## Programmatic tool access

The host application can drive the gateway's tools directly — no HTTP request to
itself. `Gateway` exposes an **in-process** FastMCP client whose calls still pass the
full governance chain (hooks, access policy, group scoping, confirmation):

```python
result = await gateway.call_tool("github_create_issue", {"title": "bug"})
print(result.data)

tools = await gateway.list_tools(group="analytics")   # same narrowing as /mcp/g/analytics

async with gateway.client() as client:                # batch calls over one session
    await client.call_tool("math_add", {"a": 1, "b": 2})
    await client.call_tool("math_add", {"a": 3, "b": 4})
```

For non-Python (or out-of-process) consumers, `ToolsApiPlugin` exposes the same
governed surface over REST — see the Admin API table below.

## Admin API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` / `POST` | `/admin/servers` | list / register servers |
| `GET` / `PATCH` / `DELETE` | `/admin/servers/{id}` | read / update / remove |
| `GET` | `/admin/servers/{id}/tools` | live tool introspection |
| `POST` | `/admin/servers/{id}/test` | connect + handshake check |
| `POST` | `/admin/servers/{id}/refresh` | remount + re-introspect **one** server (no fan-out) |
| `GET` / `POST` | `/admin/groups` | list / create groups |
| `GET` / `PATCH` / `DELETE` | `/admin/groups/{id}` | read / update / remove |
| `PUT` | `/admin/groups/{id}/servers` | set membership |
| `POST` | `/admin/reload` | rebuild mounts from the store |
| `GET` | `/admin/tools` | list governed tools (`?group=` to scope) — `ToolsApiPlugin` |
| `GET` | `/admin/tools/{name}` | full tool schema — `ToolsApiPlugin` |
| `POST` | `/admin/tools/{name}/call` | invoke a tool through the governance chain — `ToolsApiPlugin` |
| `GET` | `/admin/hil/pending` (+ `/{id}`) | pending approvals as JSON — `HumanApprovalPlugin` |
| `POST` | `/admin/hil/pending/{id}/approve` / `.../deny` | decide programmatically — `HumanApprovalPlugin` |

CRUD writes to the `Store`; `POST /admin/reload` (or `await gateway.reload()`) rebuilds
the proxy mounts and re-introspects every upstream, while `/admin/servers/{id}/refresh`
touches just one. Startup does not block on upstreams by default in the daemon
(`startup_catalog: "background"` in the config): mounts come up instantly, `tools/list`
serves the last-known catalog, and a background task refreshes it. Set `"refresh"` to
block startup until every upstream answered, or `"skip"` to defer entirely
(`create_gateway(..., startup_catalog=...)` defaults to `"refresh"` in Mode A).

> [!WARNING]
> The `/admin` API is **unauthenticated by default** and mutates the registry —
> registering upstreams, rewriting allow/deny lists, injecting connection headers, and
> triggering reload; with `ToolsApiPlugin` it can also **invoke tools**. The host app
> **must** protect it. Pass FastAPI dependencies via
> `Gateway.install(app, admin_dependencies=[Depends(require_admin)])` to guard the admin
> router, and/or place it behind reverse-proxy or network-level auth.

## Store

The gateway's only persistence dependency is the `Store` protocol. `SqliteStore`
(single file, zero setup) ships as the default; Postgres / Redis / in-memory are
drop-in via `store=` with no core changes.

```python
class Store(Protocol):
    async def initialize(self) -> None: ...
    async def list_servers(self) -> list[ServerRecord]: ...
    async def create_server(self, data: ServerCreate) -> ServerRecord: ...
    # … plus get/patch/delete for servers and groups
```

## Development

```bash
make install     # uv sync (venv + deps incl. dev group)
make check       # lint + format-check + typecheck + tests  (CI gate; run before done)
make test        # pytest
make format      # ruff format + safe lint fixes
make build       # sdist + wheel
```

Tooling: [uv](https://docs.astral.sh/uv/) (env + packaging), **ruff** (lint + format),
**mypy --strict** (types, the gate), **pytest** + pytest-asyncio. Run `make help` for
all targets.

> [!TIP]
> On Windows, `make` is not built in — use it from WSL/Git Bash, install GNU Make
> (`scoop install make`), or run the underlying `uv run ...` commands directly.

## Roadmap

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0 | Package skeleton, `Store` protocol + `SqliteStore`, `create_gateway()` | done |
| 1 | Server CRUD + builder (registry → proxy mount) + `reload()` + `pre_mcp_connect` | done |
| 2 | `HookMiddleware`: `pre_tool_call` / `post_tool_call` / `pre_list_tools` | done |
| 3 | Groups + per-server/group allow-deny + group-scoped `/mcp/g/{group}` endpoints | done |
| — | Plugin system + agent-os policy integration | done |
| 4 | `search_tools` / `describe_tool` meta-tools + catalog cache | done |
| 5 | Reference hooks (audit, deny, confirmation), docs, packaging | done |
| 6 | Local CLI (`fast-gateway`), JSON config + policy, browser HIL, upstream OAuth, Docker | done |

## License

[MIT](LICENSE)
