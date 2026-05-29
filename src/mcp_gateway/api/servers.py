"""Admin CRUD router for upstream servers.

Endpoints are wired and appear in the OpenAPI schema now; handlers return 501 until
Milestone 1 connects them to the store. Built as a factory so the store is injected
explicitly rather than read from global state.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from mcp_gateway.models import ServerCreate, ServerPatch, ServerRecord
from mcp_gateway.store.base import Store

_NOT_IMPLEMENTED = "Server registry CRUD lands in Milestone 1."


def build_servers_router(store: Store) -> APIRouter:
    """Return an ``APIRouter`` exposing server CRUD backed by ``store``."""
    router = APIRouter(prefix="/servers", tags=["servers"])

    @router.get("", response_model=list[ServerRecord])
    async def list_servers() -> list[ServerRecord]:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.post("", response_model=ServerRecord, status_code=status.HTTP_201_CREATED)
    async def create_server(data: ServerCreate) -> ServerRecord:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.get("/{server_id}", response_model=ServerRecord)
    async def get_server(server_id: str) -> ServerRecord:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.patch("/{server_id}", response_model=ServerRecord)
    async def update_server(server_id: str, patch: ServerPatch) -> ServerRecord:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_server(server_id: str) -> None:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.get("/{server_id}/tools")
    async def list_server_tools(server_id: str) -> list[dict[str, object]]:
        """Live introspection of an upstream's tools (Milestone 1)."""
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.post("/{server_id}/test")
    async def test_server(server_id: str) -> dict[str, object]:
        """Connect to the upstream and perform the MCP handshake (Milestone 1)."""
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    return router
