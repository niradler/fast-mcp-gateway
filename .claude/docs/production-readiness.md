# Production readiness — security & performance sweep

Date: 2026-06-10 · Branch: `feat/plugin-folders-and-programmatic-api` (PR #5)
Method: nir-collab security checklist over the PR surface + standing posture; live
measurements from the `scripts/validate_*.py` harness; `pip-audit` over the lockfile env.

## Fixed in this sweep

| Finding | Severity | Fix |
| --- | --- | --- |
| `aiohttp` 3.13.5 — CVE-2026-34993, CVE-2026-47265 | HIGH | bumped to 3.14.1 (`uv lock --upgrade-package aiohttp`); `pip-audit` now clean (`agent-os-kernel` skipped: git source, not on PyPI) |
| `/admin/tools` invoke route auth was assumed, not proven | — | added `test_admin_token_guards_tools_api_routes`: 401 without bearer on list AND call, 200 with token (FastAPI parent-router dependencies propagate to plugin routers) |
| No live e2e for ToolsApiPlugin | — | added `scripts/validate_tools_api.py` (15/15): real daemon + real upstream over sockets; list/group-scope/describe/invoke/deny-in-band/latency |
| agentos plugin missing the toolkit's two gateway-relevant defenses | MEDIUM | integrating `MCPSecurityScanner` (tool-poisoning scan, `pre_list_tools`) + `MCPSlidingRateLimiter` (per-group call budget, `pre_tool_call`) as opt-in toggles |

## Verified controls (evidence, not reasoning)

- **Admin auth** — bearer dependency guards every `/admin/*` route incl. nested plugin
  routers (unit test) and is exercised live in `validate_live.py`.
- **No existence leak** — policy-denied tools: absent from `tools/list`, absent from
  `search_tools`, 404 on REST describe, in-band deny on call (live 38/38 + 15/15).
- **Confirmation fail-safe** — no confirmation hook registered → deny; HIL timeout →
  deny; gateway teardown denies all in-flight approvals (`PendingRegistry.cancel_all`).
- **Group isolation** — `current_group` ContextVar set/reset per request and per
  in-process call; no leakage across requests (routing tests + `test_gateway_client`).
- **FTS injection** — free-text search queries sanitized to alnum prefix tokens;
  injection attempt leaves catalog intact (live check).
- **Input validation** — pydantic models at every admin boundary; tool arguments
  validated against the tool schema by FastMCP upstream; `timeout` must be > 0.
- **OAuth tokens** — daemon never opens a browser (`_NonInteractiveOAuth` raises);
  token dir created 0700 on POSIX; `FileTreeStore` keys sanitized.
- **Header hygiene** — hook-injected upstream Authorization never appears in the
  registry read API (live check: `static_headers` stays clean).
- **Safe defaults** — Mode-B daemon binds `127.0.0.1:8000` by default; exposure
  beyond localhost is an explicit operator choice.

## Open risks — accepted, with preconditions

| Risk | Severity | Mitigation today / precondition for production |
| --- | --- | --- |
| `/mcp` endpoint has no inbound auth — anyone who can reach the port can call every proxied tool, including ones using daemon-held OAuth credentials (confused deputy) | HIGH on shared networks | default bind is localhost; do NOT expose beyond localhost without reverse-proxy auth or network ACLs. Real fix (future): `inbound_auth` hook / FastMCP auth middleware. Tracked in mcp-gateway.md follow-ups. |
| OAuth refresh tokens plaintext at rest | MEDIUM | 0700 owner-only dir; future: keyring/vault-backed `key_value` store |
| No built-in rate limiting on core seams | MEDIUM | agentos `enable_rate_limiting` toggle covers Mode-A/agt users; core stays unlimited — front with a reverse proxy limiter when exposed |
| Admin API open when `admin_token` unset | MEDIUM | loud README warning; daemon supports `admin_token`; embedders pass `admin_dependencies` |
| No TLS termination in-process | LOW | standard deployment concern — terminate at reverse proxy / ingress |

## Performance (measured live, this machine)

| Path | Result |
| --- | --- |
| `tools/list` (catalog snapshot, no upstream fan-out) | 5.3 ms avg (n=20) |
| `search_tools` (SQLite FTS5) | 4.9 ms avg (n=20) |
| REST `GET /admin/tools` | 2.1 ms avg (n=20) |
| REST invoke of a real proxied upstream tool | 27.7 ms avg (n=20) |
| 15 concurrent proxied MCP calls | 15/15 ok, ~63 calls/s |
| `reload` (re-introspect all 8 upstreams, incl. public internet) | 4.7 s — admin-triggered only, never on the request path |

Design notes: the persisted catalog snapshot keeps `tools/list`/search O(1) in upstream
count; one-shot `gateway.call_tool` / REST invoke opens an in-process session per call
(~ms) — batch with `gateway.client()` when chaining calls.

## Dependency audit

`uv run --with pip-audit pip-audit`: **clean** after the aiohttp bump. Lockfile
committed; `agent-os-kernel` (git source) cannot be audited until it lands on PyPI —
revisit when `agent-governance-toolkit-core` publishes.
