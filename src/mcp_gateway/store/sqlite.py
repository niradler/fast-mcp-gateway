"""Default single-file SQLite store.

Zero-setup persistence for the registry. Implements the :class:`Store` protocol
over a single long-lived ``aiosqlite`` connection (so ``:memory:`` databases survive
for the lifetime of the store). JSON-encoded columns hold the list/dict fields.

Domain errors raised by this store, kept backend-agnostic so the admin API can map
them without importing ``sqlite3``:

- ``KeyError`` — no record with the given id.
- ``ValueError`` — a uniqueness constraint (duplicate name) was violated.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from collections.abc import Sequence

import aiosqlite

from mcp_gateway.models import (
    CatalogTool,
    GroupCreate,
    GroupPatch,
    GroupRecord,
    ServerCreate,
    ServerPatch,
    ServerRecord,
    Transport,
)

logger = logging.getLogger("mcp_gateway.store.sqlite")

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

_CREATE_CATALOG = """
CREATE TABLE IF NOT EXISTS catalog_tools (
    name          TEXT PRIMARY KEY,
    server_id     TEXT NOT NULL,
    namespace     TEXT NOT NULL,
    bare_name     TEXT NOT NULL,
    title         TEXT,
    description   TEXT,
    tags          TEXT NOT NULL DEFAULT '[]',
    parameters    TEXT NOT NULL DEFAULT '{}',
    output_schema TEXT,
    annotations   TEXT
)
"""

_CREATE_CATALOG_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts
USING fts5(name, bare_name, description, tags)
"""

_COLUMNS = "id, name, transport, url, static_headers, allow, deny, timeout_seconds, enabled, tags"
_GROUP_COLUMNS = "id, name, member_server_ids, allow, deny"
_CATALOG_COLUMNS = (
    "name, server_id, namespace, bare_name, title, description, "
    "tags, parameters, output_schema, annotations"
)

_FTS_TOKEN = re.compile(r"[a-z0-9]+")


def _fts_match_query(query: str) -> str | None:
    """Turn a free-text query into a safe FTS5 MATCH expression, or ``None``.

    Tokens are reduced to ``[a-z0-9]`` runs (so no FTS5 operator can slip in) and
    OR'd together as prefix terms — ``"add num"`` becomes ``add* OR num*``. Returns
    ``None`` when the query has no usable tokens.
    """
    tokens = _FTS_TOKEN.findall(query.lower())
    if not tokens:
        return None
    return " OR ".join(f"{token}*" for token in tokens)


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


def _rank_by_overlap(tools: Sequence[CatalogTool], query: str) -> list[CatalogTool]:
    """Rank ``tools`` by weighted token overlap with ``query`` (name matches weigh more).

    Used only when SQLite FTS5 is unavailable. Tools with no token hit are dropped.
    """
    terms = set(_FTS_TOKEN.findall(query.lower()))
    if not terms:
        return list(tools)

    scored: list[tuple[int, str, CatalogTool]] = []
    for tool in tools:
        name = tool.name.lower()
        haystack = f"{tool.description or ''} {' '.join(tool.tags)}".lower()
        score = sum(2 * (term in name) + (term in haystack) for term in terms)
        if score:
            scored.append((score, tool.name, tool))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tool for _, _, tool in scored]


def _row_to_catalog_tool(row: aiosqlite.Row) -> CatalogTool:
    return CatalogTool(
        name=row["name"],
        server_id=row["server_id"],
        namespace=row["namespace"],
        bare_name=row["bare_name"],
        title=row["title"],
        description=row["description"],
        tags=json.loads(row["tags"]),
        parameters=json.loads(row["parameters"]),
        output_schema=json.loads(row["output_schema"]) if row["output_schema"] else None,
        annotations=json.loads(row["annotations"]) if row["annotations"] else None,
    )


def _catalog_values(tool: CatalogTool) -> tuple[object, ...]:
    return (
        tool.name,
        tool.server_id,
        tool.namespace,
        tool.bare_name,
        tool.title,
        tool.description,
        json.dumps(tool.tags),
        json.dumps(tool.parameters),
        json.dumps(tool.output_schema) if tool.output_schema is not None else None,
        json.dumps(tool.annotations) if tool.annotations is not None else None,
    )


class SqliteStore:
    """Persists servers, groups, and the tool catalog in a single SQLite file."""

    def __init__(self, path: str = "gateway.db") -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._fts_enabled = False

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteStore is not initialized; call initialize() first.")
        return self._db

    async def initialize(self) -> None:
        """Open the connection and create tables if they do not exist.

        Probes for SQLite FTS5 (the catalog search index). If the build lacks it,
        ``search_catalog`` transparently falls back to an in-Python ranked scan.
        """
        if self._db is None:
            self._db = await aiosqlite.connect(self.path)
            self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_SERVERS)
        await self._db.execute(_CREATE_GROUPS)
        await self._db.execute(_CREATE_CATALOG)
        try:
            await self._db.execute(_CREATE_CATALOG_FTS)
            self._fts_enabled = True
        except sqlite3.OperationalError:
            self._fts_enabled = False
            logger.warning("SQLite FTS5 unavailable; catalog search falls back to a scan.")
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

    async def replace_catalog(self, tools: Sequence[CatalogTool]) -> None:
        """Wipe and repopulate the catalog (and its FTS index) in one transaction."""
        placeholders = ", ".join(["?"] * 10)
        rows = [_catalog_values(tool) for tool in tools]
        await self._conn.execute("DELETE FROM catalog_tools")
        if rows:
            await self._conn.executemany(
                f"INSERT INTO catalog_tools ({_CATALOG_COLUMNS}) VALUES ({placeholders})", rows
            )
        if self._fts_enabled:
            await self._conn.execute("DELETE FROM catalog_fts")
            if tools:
                await self._conn.executemany(
                    "INSERT INTO catalog_fts (name, bare_name, description, tags) "
                    "VALUES (?, ?, ?, ?)",
                    [(t.name, t.bare_name, t.description or "", " ".join(t.tags)) for t in tools],
                )
        await self._conn.commit()

    async def list_catalog(self) -> list[CatalogTool]:
        cursor = await self._conn.execute(
            f"SELECT {_CATALOG_COLUMNS} FROM catalog_tools ORDER BY name"
        )
        rows = await cursor.fetchall()
        return [_row_to_catalog_tool(row) for row in rows]

    async def get_catalog_tool(self, name: str) -> CatalogTool | None:
        cursor = await self._conn.execute(
            f"SELECT {_CATALOG_COLUMNS} FROM catalog_tools WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        return _row_to_catalog_tool(row) if row is not None else None

    async def search_catalog(self, query: str, limit: int = 10) -> list[CatalogTool]:
        """Rank catalog tools against ``query``; FTS5 (bm25) when available, else scan.

        An empty query browses the catalog in name order. Results never include
        permission filtering — the caller applies allow/deny and group scoping.
        """
        if limit <= 0:
            return []

        match = _fts_match_query(query)
        if match is None:
            cursor = await self._conn.execute(
                f"SELECT {_CATALOG_COLUMNS} FROM catalog_tools ORDER BY name LIMIT ?", (limit,)
            )
            return [_row_to_catalog_tool(row) for row in await cursor.fetchall()]

        if self._fts_enabled:
            return await self._search_fts(match, limit)
        return await self._search_scan(query, limit)

    async def _search_fts(self, match: str, limit: int) -> list[CatalogTool]:
        cursor = await self._conn.execute(
            "SELECT t.* FROM catalog_fts f JOIN catalog_tools t ON t.name = f.name "
            "WHERE catalog_fts MATCH ? ORDER BY bm25(catalog_fts) LIMIT ?",
            (match, limit),
        )
        return [_row_to_catalog_tool(row) for row in await cursor.fetchall()]

    async def _search_scan(self, query: str, limit: int) -> list[CatalogTool]:
        """FTS5-free fallback: load the catalog and rank by weighted token overlap."""
        tools = await self.list_catalog()
        ranked = _rank_by_overlap(tools, query)
        return ranked[:limit]
