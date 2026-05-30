"""Governed multi-upstream gateway: per-upstream auth, allow/deny, groups, admin auth.

A more realistic setup than ``basic_app``. It shows the four governance levers the
gateway gives you without writing any bespoke subsystem:

- **Per-upstream credentials** injected at connect time from the environment, via a
  ``pre_mcp_connect`` hook — secrets never live in the registry.
- **Per-server allow/deny** glob filters on the tools each upstream exposes.
- **A group** (``readonly``) that further narrows the catalog and is served at its own
  ``/mcp/g/readonly`` endpoint.
- **Admin API behind auth**: every ``/admin`` route is guarded by a FastAPI dependency
  passed to :meth:`Gateway.install`.

Run with::

    DOCS_TOKEN=... ISSUES_TOKEN=... ADMIN_TOKEN=... \
        uv run uvicorn examples.governed_gateway:app --reload

Then:

- Admin API + OpenAPI docs:  http://127.0.0.1:8000/docs
  (send ``Authorization: Bearer $ADMIN_TOKEN`` on admin routes)
- Full MCP endpoint:         http://127.0.0.1:8000/mcp/
- Group-scoped endpoint:     http://127.0.0.1:8000/mcp/g/readonly/

The seeded upstream URLs are placeholders; introspection of an unreachable upstream is
tolerated (logged, not fatal), so the app still starts for you to explore the wiring.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from mcp_gateway import (
    ConnectContext,
    ConnectSettings,
    GroupCreate,
    Hooks,
    ServerCreate,
    SqliteStore,
    Store,
    create_gateway,
)


async def inject_auth(context: ConnectContext) -> ConnectSettings | None:
    """``pre_mcp_connect``: attach a per-upstream bearer token from the environment.

    Each upstream looks up ``<NAME>_TOKEN`` (e.g. ``DOCS_TOKEN``); absent a token the
    upstream is reached unauthenticated. Keeping credentials here — out of the persisted
    registry — is why ``ServerCreate.static_headers`` is reserved for non-secret headers.
    """
    token = os.environ.get(f"{context.server.name.upper()}_TOKEN")
    if token:
        return ConnectSettings(headers={"Authorization": f"Bearer {token}"})
    return None


async def require_admin(authorization: Annotated[str, Header()] = "") -> None:
    """FastAPI dependency: gate every admin route behind a static bearer token.

    Swap this for real OAuth/JWT verification in production; the point is that the admin
    API performs no authentication of its own — :meth:`Gateway.install` wires whatever
    dependency you pass onto all of its routes.
    """
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected or authorization != f"Bearer {expected}":
        detail = "Admin authentication required."
        raise HTTPException(status_code=401, detail=detail)


async def seed_registry(store: Store) -> None:
    """Register two upstreams and a read-only group the first time the app runs.

    ``allow``/``deny`` are tool-name globs applied per server (``deny`` wins). The group
    layers its own deny on top and exposes only its member servers at ``/mcp/g/readonly``.
    """
    if await store.list_servers():
        return
    docs = await store.create_server(
        ServerCreate(
            name="docs",
            url="https://docs.example.com/mcp",
            allow=["search*", "fetch*"],
        )
    )
    issues = await store.create_server(
        ServerCreate(
            name="issues",
            url="https://issues.example.com/mcp",
            deny=["*delete*", "*admin*"],
        )
    )
    await store.create_group(
        GroupCreate(
            name="readonly",
            member_server_ids=[docs.id, issues.id],
            deny=["*write*", "*delete*", "*create*"],
        )
    )


store = SqliteStore("gateway.db")
gateway = create_gateway(store=store, hooks=Hooks(pre_mcp_connect=[inject_auth]))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the gateway lifespan, then seed the registry and reload once on first boot."""
    async with gateway.lifespan(app):
        await seed_registry(store)
        await gateway.reload()
        yield


app = FastAPI(title="Governed MCP Gateway", lifespan=lifespan)
gateway.install(app, admin_dependencies=[Depends(require_admin)])
