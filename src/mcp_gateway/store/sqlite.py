"""Default single-file SQLite store.

Zero-setup persistence for the registry. Implements the :class:`Store` protocol
over a single long-lived ``aiosqlite`` connection (so ``:memory:`` databases survive
for the lifetime of the store). JSON-encoded columns hold the list/dict fields.

Both server and group CRUD are fully implemented.

Domain errors raised by this store, kept backend-agnostic so the admin API can map
them without importing ``sqlite3``:

- ``KeyError`` — no record with the given id.
- ``ValueError`` — a uniqueness constraint (duplicate name) was violated.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import aiosqlite

from mcp_gateway.models import (
    GroupCreate,
    GroupPatch,
    GroupRecord,
    ServerCreate,
    ServerPatch,
    ServerRecord,
    Transport,
)

_CREATE_SERVERS = """
CREATE TABLE IF NOT EXISTS servers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    transport       TEXT NOT NULL,
    url             TEXT NOT NULL,
    static_headers  TEXT NOT NULL DEFAULT '{}',
    allow           TEXT NOT NULL DEFAULT '[]',
    deny            TEXT NOT NULL DEFAULT '[]',
    timeout_seconds REAL NOT NULL DEFAULT 30.0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    tags            TEXT NOT NULL DEFAULT '[]'
)
"""

_CREATE_GROUPS = """
CREATE TABLE IF NOT EXISTS groups (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    member_server_ids TEXT NOT NULL DEFAULT '[]',
    allow             TEXT NOT NULL DEFAULT '[]',
    deny              TEXT NOT NULL DEFAULT '[]'
)
"""

_COLUMNS = "id, name, transport, url, static_headers, allow, deny, timeout_seconds, enabled, tags"
_GROUP_COLUMNS = "id, name, member_server_ids, allow, deny"


def _row_to_server(row: aiosqlite.Row) -> ServerRecord:
    return ServerRecord(
        id=row["id"],
        name=row["name"],
        transport=Transport(row["transport"]),
        url=row["url"],
        static_headers=json.loads(row["static_headers"]),
        allow=json.loads(row["allow"]),
        deny=json.loads(row["deny"]),
        timeout_seconds=row["timeout_seconds"],
        enabled=bool(row["enabled"]),
        tags=json.loads(row["tags"]),
    )


def _server_values(record: ServerRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.name,
        record.transport.value,
        record.url,
        json.dumps(record.static_headers),
        json.dumps(record.allow),
        json.dumps(record.deny),
        record.timeout_seconds,
        int(record.enabled),
        json.dumps(record.tags),
    )


def _row_to_group(row: aiosqlite.Row) -> GroupRecord:
    return GroupRecord(
        id=row["id"],
        name=row["name"],
        member_server_ids=json.loads(row["member_server_ids"]),
        allow=json.loads(row["allow"]),
        deny=json.loads(row["deny"]),
    )


def _group_values(record: GroupRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.name,
        json.dumps(record.member_server_ids),
        json.dumps(record.allow),
        json.dumps(record.deny),
    )


class SqliteStore:
    """Persists servers and groups in a single SQLite file."""

    def __init__(self, path: str = "gateway.db") -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteStore is not initialized; call initialize() first.")
        return self._db

    async def initialize(self) -> None:
        """Open the connection and create tables if they do not exist."""
        if self._db is None:
            self._db = await aiosqlite.connect(self.path)
            self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_SERVERS)
        await self._db.execute(_CREATE_GROUPS)
        await self._db.commit()

    async def close(self) -> None:
        """Close the underlying connection. Safe to call when never initialized."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def list_servers(self) -> list[ServerRecord]:
        cursor = await self._conn.execute(f"SELECT {_COLUMNS} FROM servers ORDER BY name")
        rows = await cursor.fetchall()
        return [_row_to_server(row) for row in rows]

    async def get_server(self, server_id: str) -> ServerRecord | None:
        cursor = await self._conn.execute(
            f"SELECT {_COLUMNS} FROM servers WHERE id = ?", (server_id,)
        )
        row = await cursor.fetchone()
        return _row_to_server(row) if row is not None else None

    async def create_server(self, data: ServerCreate) -> ServerRecord:
        record = ServerRecord(id=uuid.uuid4().hex, **data.model_dump())
        try:
            await self._conn.execute(
                f"INSERT INTO servers ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _server_values(record),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A server named {record.name!r} already exists.") from exc
        return record

    async def update_server(self, server_id: str, patch: ServerPatch) -> ServerRecord:
        existing = await self.get_server(server_id)
        if existing is None:
            raise KeyError(server_id)
        updated = existing.model_copy(update=patch.model_dump(exclude_unset=True))
        try:
            await self._conn.execute(
                "UPDATE servers SET name = ?, transport = ?, url = ?, static_headers = ?, "
                "allow = ?, deny = ?, timeout_seconds = ?, enabled = ?, tags = ? WHERE id = ?",
                (*_server_values(updated)[1:], server_id),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A server named {updated.name!r} already exists.") from exc
        return updated

    async def delete_server(self, server_id: str) -> None:
        cursor = await self._conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        await self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(server_id)

    async def list_groups(self) -> list[GroupRecord]:
        cursor = await self._conn.execute(f"SELECT {_GROUP_COLUMNS} FROM groups ORDER BY name")
        rows = await cursor.fetchall()
        return [_row_to_group(row) for row in rows]

    async def get_group(self, group_id: str) -> GroupRecord | None:
        cursor = await self._conn.execute(
            f"SELECT {_GROUP_COLUMNS} FROM groups WHERE id = ?", (group_id,)
        )
        row = await cursor.fetchone()
        return _row_to_group(row) if row is not None else None

    async def create_group(self, data: GroupCreate) -> GroupRecord:
        record = GroupRecord(id=uuid.uuid4().hex, **data.model_dump())
        try:
            await self._conn.execute(
                f"INSERT INTO groups ({_GROUP_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
                _group_values(record),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A group named {record.name!r} already exists.") from exc
        return record

    async def update_group(self, group_id: str, patch: GroupPatch) -> GroupRecord:
        existing = await self.get_group(group_id)
        if existing is None:
            raise KeyError(group_id)
        updated = existing.model_copy(update=patch.model_dump(exclude_unset=True))
        try:
            await self._conn.execute(
                "UPDATE groups SET name = ?, member_server_ids = ?, allow = ?, deny = ? "
                "WHERE id = ?",
                (*_group_values(updated)[1:], group_id),
            )
            await self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A group named {updated.name!r} already exists.") from exc
        return updated

    async def delete_group(self, group_id: str) -> None:
        cursor = await self._conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        await self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(group_id)
