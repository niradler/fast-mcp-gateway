# fast-mcp-gateway

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
many upstream MCP servers  ──►  fast-mcp-gateway  ──►  one governed /mcp endpoint
   github, slack, jira…          (namespaced + filtered + policy-checked)
```

> [!NOTE]
> **Status: alpha, under active development.** Server registry, the proxy builder,
> the full hook pipeline, groups with allow/deny, group-scoped endpoints, and the
> plugin system are implemented and tested. The `search_tools` / `describe_tool`
> meta-tools and the bundled reference hooks are still in progress — see the
> [roadmap](#roadmap).

## Why

Point an LLM at a dozen MCP servers directly and you get a dozen connections, a dozen
auth schemes, no namespacing, no central policy, and no way to hide a dangerous tool.
`fast-mcp-gateway` puts one endpoint in front of them all:

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

## Getting started

### Prerequisites

- Python **3.11+**
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install

```bash
uv add fast-mcp-gateway        # or: pip install fast-mcp-gateway
```

### Quickstart

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
from mcp_gateway import Hooks, ToolCallResult, ToolDecision


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
from mcp_gateway import create_gateway, SqliteStore

gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    plugins=[MyAuditPlugin(), MyRateLimitPlugin()],
)
```

A plugin implements the `Plugin` protocol: a `name`, a `contributions(context)` method
returning `PluginContributions`, and `setup` / `teardown` coroutines. The
`GatewayContext` it receives exposes the `store`, the parent `mcp`, and a `reload`
callable.

### Optional: agent-os plugin

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
pip install "fast-mcp-gateway[agt]"
```

```python
from mcp_gateway import create_gateway, SqliteStore
from mcp_gateway.integrations.agt import AgtAgentOsPlugin, AgtAgentOsSettings

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
> The `agt` extra is sourced from the agent-governance-toolkit GitHub monorepo until
> `agent-os-kernel` 4.x is published to PyPI. The gateway and the plugin system work
> fully **without** it — only this one integration needs it.

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
| 4 | `search_tools` / `describe_tool` meta-tools + catalog cache | in progress |
| 5 | Reference hooks (audit, allow/deny, confirmation), docs, packaging | planned |

## License

[MIT](LICENSE)
