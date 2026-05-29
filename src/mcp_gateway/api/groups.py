"""Admin CRUD router for groups.

Endpoints are wired and appear in the OpenAPI schema now; handlers return 501 until
Milestone 3 connects them to the store.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from mcp_gateway.models import GroupCreate, GroupRecord
from mcp_gateway.store.base import Store

_NOT_IMPLEMENTED = "Groups land in Milestone 3."


def build_groups_router(store: Store) -> APIRouter:
    """Return an ``APIRouter`` exposing group CRUD backed by ``store``."""
    router = APIRouter(prefix="/groups", tags=["groups"])

    @router.get("", response_model=list[GroupRecord])
    async def list_groups() -> list[GroupRecord]:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.post("", response_model=GroupRecord, status_code=status.HTTP_201_CREATED)
    async def create_group(data: GroupCreate) -> GroupRecord:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.get("/{group_id}", response_model=GroupRecord)
    async def get_group(group_id: str) -> GroupRecord:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.patch("/{group_id}", response_model=GroupRecord)
    async def update_group(group_id: str, group: GroupRecord) -> GroupRecord:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_group(group_id: str) -> None:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    @router.put("/{group_id}/servers", response_model=GroupRecord)
    async def set_group_servers(group_id: str, member_server_ids: list[str]) -> GroupRecord:
        """Replace a group's membership (Milestone 3)."""
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)

    return router
