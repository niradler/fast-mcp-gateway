"""Turns the registry into mounted proxies on the parent gateway server.

FastMCP has no ``unmount``; the builder snapshots ``mcp.providers`` at construction
and resets to that baseline on every ``reload`` before re-mounting, so dropped or
disabled servers disappear and reloads are idempotent.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import FastMCP
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.proxy import FastMCPProxy

from fast_mcp_gateway.access import AccessPolicy
from fast_mcp_gateway.catalog import collect_catalog
from fast_mcp_gateway.connect import build_client_factory
from fast_mcp_gateway.hooks import Hooks
from fast_mcp_gateway.store.base import Store

logger = logging.getLogger("fast_mcp_gateway.builder")


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

    async def reload(self) -> None:
        """Rebuild proxy mounts, access policy, and tool catalog from the current registry.

        Serialized via ``_reload_lock`` so concurrent reloads cannot interleave store
        reads, provider mutation, and catalog replacement. A ``tools/list`` landing
        during catalog rewrite may see a transient partial view; a retry returns the full set.
        """
        async with self._reload_lock:
            servers = await self.store.list_servers()
            groups = await self.store.list_groups()

            if self.policy is not None:
                # keep: rebuild from ALL servers (even disabled) for split_namespace
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

        logger.info(
            "Gateway reloaded: %d server(s) mounted, %d tool(s) cataloged.",
            mounted,
            len(catalog),
        )
