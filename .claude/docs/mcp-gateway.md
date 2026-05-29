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

## Key decisions

- **FastMCP v3.3.1, not v2** (overrides plan decision #10). v3 API names recorded in
  CLAUDE.md (`create_proxy`, `mount(..., namespace=)`, middleware paths). Revert =
  pin `fastmcp>=2,<3`.
- **requires-python >=3.11** (plan/uv-init default was 3.13) for PyPI reach. Dev venv
  still 3.13 via `.python-version`.
- **MIT license**, author Nir Adler / komodor email. GitHub URLs guessed as
  `niradler/fast-mcp-gateway` — confirm/adjust.
- Type checker: **mypy --strict** as the gate (not `ty` — still pre-1.0, risky on
  pydantic). ruff covers the fast dev loop.

## Open issues / confirm with Nir

- v2 vs v3 — proceeding on v3; confirm OK.
- GitHub repo URL in pyproject (`niradler/...`) — placeholder.
- `main.py` (uv-init hello-world stub) at repo root is unused — needs removal (delete
  is a destructive op; give Nir the command).
- Windows: `make` not native — flagged in README/CLAUDE.

## Next (Milestone 1)

Server CRUD against `SqliteStore` (implement the store) + builder (registry →
`create_proxy` + `mount(namespace=)`) + `reload()` + wire `pre_mcp_connect` into
`build_client_factory`. Then Milestone 2 (HookMiddleware semantics).
