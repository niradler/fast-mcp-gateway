"""Admin CRUD router for upstream servers, backed by the :class:`Store`.

``/test`` and ``/tools`` open a live upstream connection (``pre_mcp_connect`` hooks apply)
without mounting it on the gateway. ``KeyError`` maps to 404, ``ValueError`` to 409.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from fast_mcp_gateway.connect import build_client_factory
from fast_mcp_gateway.hooks import Hooks
from fast_mcp_gateway.models import ServerCreate, ServerPatch, ServerRecord
from fast_mcp_gateway.store.base import Store


def build_servers_router(store: Store, hooks: Hooks) -> APIRouter:
    """Return an ``APIRouter`` exposing server CRUD backed by ``store``."""
    router = APIRouter(prefix="/servers", tags=["servers"])

    async def _require_server(server_id: str) -> ServerRecord:
        server = await store.get_server(server_id)
        if server is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No server with id {server_id!r}.")
        return server

    @router.get("", response_model=list[ServerRecord])
    async def list_servers() -> list[ServerRecord]:
        return await store.list_servers()

    @router.post("", response_model=ServerRecord, status_code=status.HTTP_201_CREATED)
    async def create_server(data: ServerCreate) -> ServerRecord:
        try:
            return await store.create_server(data)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.get("/{server_id}", response_model=ServerRecord)
    async def get_server(server_id: str) -> ServerRecord:
        return await _require_server(server_id)

    @router.patch("/{server_id}", response_model=ServerRecord)
    async def update_server(server_id: str, patch: ServerPatch) -> ServerRecord:
        try:
            return await store.update_server(server_id, patch)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No server with id {server_id!r}."
            ) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_server(server_id: str) -> None:
        try:
            await store.delete_server(server_id)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No server with id {server_id!r}."
            ) from exc

    @router.get("/{server_id}/tools")
    async def list_server_tools(server_id: str) -> list[dict[str, Any]]:
        """Live introspection of an upstream's tools."""
        server = await _require_server(server_id)
        factory = build_client_factory(server, hooks)
        client = await factory()
        try:
            async with client:
                tools = await client.list_tools()
        except Exception as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Failed to list tools from upstream: {exc}"
            ) from exc
        return [tool.model_dump(mode="json") for tool in tools]

    @router.post("/{server_id}/test")
    async def test_server(server_id: str) -> dict[str, Any]:
        """Connect to the upstream and perform the MCP handshake."""
        server = await _require_server(server_id)
        factory = build_client_factory(server, hooks)
        client = await factory()
        try:
            async with client:
                tools = await client.list_tools()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "tool_count": len(tools)}

    return router
