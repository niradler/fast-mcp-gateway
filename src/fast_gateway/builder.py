"""Turns the registry into mounted proxies on the parent gateway server.

FastMCP has no ``unmount``; the builder snapshots ``mcp.providers`` at construction
and resets to that baseline on every rebuild before re-mounting, so dropped or
disabled servers disappear and reloads are idempotent. Mount rebuilding is pure
in-memory work; only catalog refresh fans out to upstreams.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import FastMCP
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.proxy import FastMCPProxy

from fast_gateway.access import AccessPolicy
from fast_gateway.catalog import collect_catalog
from fast_gateway.connect import build_client_factory
from fast_gateway.hooks import Hooks
from fast_gateway.store.base import Store

logger = logging.getLogger("fast_gateway.builder")


class GatewayBuilder:
    """Builds and rebuilds proxy mounts on a parent FastMCP server."""

    def __init__(
        self, mcp: FastMCP, store: Store, hooks: Hooks, policy: AccessPolicy | None = None
    ) -> None:
        self.mcp = mcp
        self.store = store
        self.hooks = hooks
        self.policy = policy
        self._baseline_providers: list[Provider] = list(mcp.providers)
        self._reload_lock = asyncio.Lock()

    async def rebuild_mounts(self) -> None:
        """Rebuild proxy mounts and access policy from the registry — no upstream I/O.

        Proxy clients are lazy (built per session by the client factory), so this is
        cheap regardless of how many servers are registered: the gateway can start
        serving immediately and refresh the catalog separately.
        """
        async with self._reload_lock:
            await self._rebuild_mounts_locked()

    async def _rebuild_mounts_locked(self) -> int:
        servers = await self.store.list_servers()
        groups = await self.store.list_groups()

        if self.policy is not None:
            self.policy.rebuild(servers, groups)

        self.mcp.providers[:] = self._baseline_providers

        mounted = 0
        for server in servers:
            if not server.enabled:
                continue
            factory = build_client_factory(server, self.hooks)
            proxy = FastMCPProxy(client_factory=factory, name=server.name)
            self.mcp.mount(proxy, namespace=server.name)
            mounted += 1
        return mounted

    async def refresh_catalog(self) -> list[str]:
        """Re-introspect every enabled upstream and replace the persisted catalog.

        Returns the names of servers whose introspection failed (empty when all
        healthy); their last-known catalog rows are retained.
        """
        async with self._reload_lock:
            servers = await self.store.list_servers()
            degraded = await self._refresh_catalog_locked()
        id_to_name = {s.id: s.name for s in servers}
        return [id_to_name[sid] for sid in degraded if sid in id_to_name]

    async def _refresh_catalog_locked(self) -> set[str]:
        servers = await self.store.list_servers()
        catalog, failed_ids = await collect_catalog(servers, self.hooks)
        if failed_ids:
            retained = [t for t in await self.store.list_catalog() if t.server_id in failed_ids]
            if retained:
                logger.warning(
                    "Retaining %d last-known tool(s) for %d server(s) that failed "
                    "introspection during reload.",
                    len(retained),
                    len(failed_ids),
                )
            catalog = catalog + retained
        await self.store.replace_catalog(catalog)
        logger.info("Catalog refreshed: %d tool(s) cataloged.", len(catalog))
        return failed_ids

    async def refresh_server(self, server_id: str) -> bool:
        """Remount from the registry, then re-introspect only *server_id*.

        The on-demand alternative to a full ``reload`` — adding or changing one
        server does not fan out to every other upstream. Returns ``False`` when
        introspection failed (last-known rows are kept) and raises ``KeyError``
        for an unknown id. A disabled server just has its catalog rows dropped.
        """
        async with self._reload_lock:
            server = await self.store.get_server(server_id)
            if server is None:
                raise KeyError(server_id)
            await self._rebuild_mounts_locked()
            if not server.enabled:
                await self.store.replace_server_catalog(server_id, [])
                return True
            catalog, failed_ids = await collect_catalog([server], self.hooks)
            if failed_ids:
                return False
            await self.store.replace_server_catalog(server_id, catalog)
            return True

    async def reload(self) -> list[str]:
        """Rebuild proxy mounts, access policy, and tool catalog from the current registry.

        Returns the names of servers whose introspection failed (empty when all healthy).
        Serialized via ``_reload_lock`` so concurrent reloads cannot interleave store reads,
        provider mutation, and catalog replacement.
        """
        async with self._reload_lock:
            servers = await self.store.list_servers()
            mounted = await self._rebuild_mounts_locked()
            failed_ids = await self._refresh_catalog_locked()

        id_to_name = {s.id: s.name for s in servers}
        degraded = [id_to_name[sid] for sid in failed_ids if sid in id_to_name]

        logger.info("Gateway reloaded: %d server(s) mounted.", mounted)
        return degraded
