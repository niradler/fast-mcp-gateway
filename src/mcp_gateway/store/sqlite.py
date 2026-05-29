"""Default single-file SQLite store.

Zero-setup persistence for the registry. Implements the :class:`Store` protocol.

NOTE: scaffolding stub. Method bodies land in Milestone 1; the class exists now so
``create_gateway`` can accept a real default store and the wiring is type-checked.
"""

from __future__ import annotations

from mcp_gateway.models import (
    GroupCreate,
    GroupRecord,
    ServerCreate,
    ServerPatch,
    ServerRecord,
)


class SqliteStore:
    """Persists servers and groups in a single SQLite file."""

    def __init__(self, path: str = "gateway.db") -> None:
        self.path = path

    async def initialize(self) -> None:
        """Create tables if they do not exist. Called once at startup."""
        raise NotImplementedError("SqliteStore.initialize — Milestone 1")

    async def list_servers(self) -> list[ServerRecord]:
        raise NotImplementedError("SqliteStore.list_servers — Milestone 1")

    async def get_server(self, server_id: str) -> ServerRecord | None:
        raise NotImplementedError("SqliteStore.get_server — Milestone 1")

    async def create_server(self, data: ServerCreate) -> ServerRecord:
        raise NotImplementedError("SqliteStore.create_server — Milestone 1")

    async def update_server(self, server_id: str, patch: ServerPatch) -> ServerRecord:
        raise NotImplementedError("SqliteStore.update_server — Milestone 1")

    async def delete_server(self, server_id: str) -> None:
        raise NotImplementedError("SqliteStore.delete_server — Milestone 1")

    async def list_groups(self) -> list[GroupRecord]:
        raise NotImplementedError("SqliteStore.list_groups — Milestone 3")

    async def get_group(self, group_id: str) -> GroupRecord | None:
        raise NotImplementedError("SqliteStore.get_group — Milestone 3")

    async def create_group(self, data: GroupCreate) -> GroupRecord:
        raise NotImplementedError("SqliteStore.create_group — Milestone 3")

    async def upsert_group(self, group: GroupRecord) -> GroupRecord:
        raise NotImplementedError("SqliteStore.upsert_group — Milestone 3")

    async def delete_group(self, group_id: str) -> None:
        raise NotImplementedError("SqliteStore.delete_group — Milestone 3")
