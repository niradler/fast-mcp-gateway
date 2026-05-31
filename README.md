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
> **Status: 0.0.2, under active development.** APIs may change. Implemented and tested:
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
  meta-tools into one named extension with `setup` / `teardown`.
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
uv add "fast-gateway[cli]"      # or: pip install "fast-gateway[cli]"
fast-gateway serve --config examples/gateway.json
# MCP endpoint  : http://127.0.0.1:8000/mcp/
# Admin API     : http://127.0.0.1:8000/admin
# HIL approvals : http://127.0.0.1:8000/admin/hil
```

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

### OAuth-protected upstreams

Many hosted MCP servers (Datadog, etc.) require an OAuth login. Register the server with
`--oauth`, then run the browser login **once** — tokens are cached on disk and refreshed
automatically thereafter, so the gateway connects unattended after that.

```bash
fast-gateway add datadog https://<your-dd-mcp-endpoint> --oauth --scope read
fast-gateway login datadog          # opens the browser, completes OAuth, caches tokens
fast-gateway logout datadog         # clear the cached tokens for a server
```

Run `login` up front to avoid a browser popup during a daemon reload. Tokens live under
`~/.fast-gateway/oauth` (override with `oauth_token_dir` in the config or
`$FAST_GATEWAY_OAUTH_DIR`); the cache is shared between the CLI and the daemon, but both
must resolve the same directory — pass `--config` to `login`/`logout` when you customise
`oauth_token_dir`. For headless hosts, run `login` on a machine with a browser, or use the
upstream's API-key header fallback via `--header`.

> [!WARNING]
> **Security: the `/mcp` endpoint is unauthenticated.** The daemon holds upstream OAuth
> refresh tokens, so **do not expose the mapped port to untrusted networks**. Set
> `admin_token` in your config to protect the admin API. Inbound MCP authentication and
> encryption-at-rest for the token cache are planned follow-ups — see the
> [roadmap](#roadmap).

> [!NOTE]
> OAuth is a **Mode-B-only feature** (`OAuthPlugin`), wired automatically by `build_app`
> (the CLI / `fast-gateway serve` path). It is **not** registered in a Mode-A embedded
> mount (`create_gateway` called directly). OAuth requires a human at a terminal to complete
> the browser authorization-code flow, so it is not appropriate for a headless library
> embedding. In Mode A, inject auth tokens via a `pre_mcp_connect` hook in `ConnectSettings`
> instead — see the [Quickstart](#quickstart) example.

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

## Admin API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` / `POST` | `/admin/servers` | list / register servers |
| `GET` / `PATCH` / `DELETE` | `/admin/servers/{id}` | read / update / remove |
| `GET` | `/admin/servers/{id}/tools` | live tool introspection |
| `POST` | `/admin/servers/{id}/test` | connect + handshake check |
| `GET` / `POST` | `/admin/groups` | list / create groups |
| `GET` / `PATCH` / `DELETE` | `/admin/groups/{id}` | read / update / remove |
| `PUT` | `/admin/groups/{id}/servers` | set membership |
| `POST` | `/admin/reload` | rebuild mounts from the store |

CRUD writes to the `Store`; `POST /admin/reload` (or `await gateway.reload()`) rebuilds
the proxy mounts. There is no live hot-swap in v1 — simple and lean.

> [!WARNING]
> The `/admin` API is **unauthenticated by default** and mutates the registry —
> registering upstreams, rewriting allow/deny lists, injecting connection headers, and
> triggering reload. The host app **must** protect it. Pass FastAPI dependencies via
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
