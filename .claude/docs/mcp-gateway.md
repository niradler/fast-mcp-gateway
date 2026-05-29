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
- **MIT license**; project is niradler's personal (not Komodor). pyproject author is
  name-only (no personal email yet — can add later).
- Type checker: **mypy --strict** as the gate (not `ty` — still pre-1.0, risky on
  pydantic). ruff covers the fast dev loop.
- **Repo:** https://github.com/niradler/fast-mcp-gateway — PUBLIC, default branch
  `main`. Pushed: scaffold + .gitattributes (LF normalization).

## Resolved

- v2 vs v3 → v3 (latest). `main.py` stub removed. Windows `make` present (4.4.1).
- VSCode interpreter points at C:\Python312, not `.venv` — Nir to select
  `.venv\Scripts\python.exe` to clear the fastapi/fastmcp "not installed" hints.

## Next (Milestone 1)

Server CRUD against `SqliteStore` (implement the store) + builder (registry →
`create_proxy` + `mount(namespace=)`) + `reload()` + wire `pre_mcp_connect` into
`build_client_factory`. Then Milestone 2 (HookMiddleware semantics).
