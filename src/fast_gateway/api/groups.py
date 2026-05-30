"""Admin CRUD router for groups, backed by the :class:`Store`.

Store error contract → HTTP mapping:

- ``KeyError`` (no such id) → 404
- ``ValueError`` (duplicate name) → 409
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from fast_gateway.models import GroupCreate, GroupPatch, GroupRecord
from fast_gateway.store.base import Store


def build_groups_router(store: Store) -> APIRouter:
    """Return an ``APIRouter`` exposing group CRUD backed by ``store``."""
    router = APIRouter(prefix="/groups", tags=["groups"])

    async def _require_group(group_id: str) -> GroupRecord:
        group = await store.get_group(group_id)
        if group is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No group with id {group_id!r}.")
        return group

    @router.get("", response_model=list[GroupRecord])
    async def list_groups() -> list[GroupRecord]:
        return await store.list_groups()

    @router.post("", response_model=GroupRecord, status_code=status.HTTP_201_CREATED)
    async def create_group(data: GroupCreate) -> GroupRecord:
        try:
            return await store.create_group(data)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.get("/{group_id}", response_model=GroupRecord)
    async def get_group(group_id: str) -> GroupRecord:
        return await _require_group(group_id)

    @router.patch("/{group_id}", response_model=GroupRecord)
    async def update_group(group_id: str, patch: GroupPatch) -> GroupRecord:
        try:
            return await store.update_group(group_id, patch)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No group with id {group_id!r}."
            ) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_group(group_id: str) -> None:
        try:
            await store.delete_group(group_id)
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No group with id {group_id!r}."
            ) from exc

    @router.put("/{group_id}/servers", response_model=GroupRecord)
    async def set_group_servers(group_id: str, member_server_ids: list[str]) -> GroupRecord:
        """Replace a group's server membership."""
        try:
            return await store.update_group(
                group_id, GroupPatch(member_server_ids=member_server_ids)
            )
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No group with id {group_id!r}."
            ) from exc

    return router
