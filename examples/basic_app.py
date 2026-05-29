"""Minimal example: mount the gateway on a FastAPI app.

Run with:

    uv run uvicorn examples.basic_app:app --reload

Then:
- Admin API + OpenAPI docs:  http://127.0.0.1:8000/docs
- MCP endpoint:              http://127.0.0.1:8000/mcp/

Auth and any other cross-cutting concern is just a hook — see ``inject_auth`` below.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from mcp_gateway import ConnectContext, ConnectSettings, Hooks, SqliteStore, create_gateway


async def inject_auth(context: ConnectContext) -> ConnectSettings | None:
    """Example ``pre_mcp_connect`` hook: attach a bearer token per upstream."""
    if context.server.name == "github":
        token = os.environ.get("GH_TOKEN")
        if token:
            return ConnectSettings(headers={"Authorization": f"Bearer {token}"})
    return None


gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    hooks=Hooks(pre_mcp_connect=[inject_auth]),
)

app = FastAPI(title="MCP Gateway", lifespan=gateway.lifespan)
gateway.install(app)
