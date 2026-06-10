# Live validation harness

End-to-end harnesses that boot the gateway (and a local upstream) as **real uvicorn
processes** and drive them over **real HTTP** with a real `fastmcp.Client` — the
production path, not a test double. Each exits non-zero on failure and tears its
processes down. They complement `make check` (the deterministic unit/integration gate);
these are the *live* gate, run on demand before a release.

| Script | What it proves | Needs |
| --- | --- | --- |
| `probe_live_mcp.py` | Which public MCP servers are reachable (connectivity scout) | network |
| `validate_live.py` | CRUD, namespaced proxying, reload, a live proxied tool call, all five hook seams, allow/deny, groups, search meta-tools, admin auth, perf, security | network + ports 8000/9100 |
| `validate_plugins.py` | Every `PluginContributions` field live: registered tool, around-call middleware, `pre_tool_call` hook, admin router, ASGI mount, lifecycle `setup` | port 8001 |
| `validate_tools_api.py` | `ToolsApiPlugin` REST routes live: list (+group scoping), describe (404 hides denied), invoke of a real proxied tool, config-policy + group denials in-band, latency | ports 8003/9102 |
| `validate_agentos.py` | The optional agent-os policy plugin allows/denies a real proxied tool call over HTTP (skips if the `agt` extra is absent) | port 8002/9100, `uv sync --extra agt` |

```bash
uv run python scripts/probe_live_mcp.py
uv run python scripts/validate_live.py
uv run python scripts/validate_plugins.py
uv run python scripts/validate_tools_api.py
uv sync --extra agt && uv run python scripts/validate_agentos.py
```

The upstreams driven are the local `examples/echo_upstream.py` (ground truth for what the
gateway injects/transforms) plus public internet MCP servers (real proxying). Public
checks are reported but never fatal, so upstream flakiness does not gate a run. uvicorn
logs are written to the system temp dir; `*.db` artifacts are gitignored.
