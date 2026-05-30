"""The ``Store`` protocol: the gateway's only persistence dependency.

Any object implementing this protocol can back the gateway. The default
implementation is :class:`mcp_gateway.store.sqlite.SqliteStore`; Postgres, Redis,
or in-memory stores are drop-in replacements with no core changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from mcp_gateway.models import (
    CatalogTool,
    GroupCreate,
    GroupPatch,
    GroupRecord,
    ServerCreate,
    ServerPatch,
    ServerRecord,
)


@runtime_checkable
class Store(Protocol):
    """Persistence contract for the server/group registry.

    All methods are async so the gateway can run against networked backends
    without blocking the event loop.
    """

    async def initialize(self) -> None:
        """Prepare the backend (open connections, create schema). Called once at
        gateway startup before any other method."""
        ...

    async def list_servers(self) -> list[ServerRecord]: ...

    async def get_server(self, server_id: str) -> ServerRecord | None: ...

    async def create_server(self, data: ServerCreate) -> ServerRecord: ...

    async def update_server(self, server_id: str, patch: ServerPatch) -> ServerRecord: ...

    async def delete_server(self, server_id: str) -> None: ...

    async def list_groups(self) -> list[GroupRecord]: ...

    async def get_group(self, group_id: str) -> GroupRecord | None: ...

    async def create_group(self, data: GroupCreate) -> GroupRecord: ...

    async def update_group(self, group_id: str, patch: GroupPatch) -> GroupRecord: ...

    async def delete_group(self, group_id: str) -> None: ...

    async def replace_catalog(self, tools: Sequence[CatalogTool]) -> None:
        """Atomically replace the persisted tool catalog with ``tools``.

        Called by ``GatewayBuilder.reload`` after introspecting the upstreams.
        Backends that support full-text search rebuild their index here."""
        ...

    async def list_catalog(self) -> list[CatalogTool]:
        """Return the full persisted catalog (namespaced, unfiltered)."""
        ...

    async def search_catalog(self, query: str, limit: int = 10) -> list[CatalogTool]:
        """Return catalog tools matching ``query``, best match first, capped at ``limit``.

        An empty/whitespace query returns the catalog (bounded by ``limit``) so callers
        can browse. Permission filtering (allow/deny, groups) is applied by the caller,
        not here."""
        ...

    async def get_catalog_tool(self, name: str) -> CatalogTool | None:
        """Return one catalog tool by its namespaced ``name``, or ``None``."""
        ...
