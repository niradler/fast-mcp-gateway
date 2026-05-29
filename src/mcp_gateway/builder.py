"""Turns the registry into mounted proxies on the parent gateway server.

For each enabled server record the builder creates a FastMCP proxy (a
``FastMCPProxy`` driven by our :func:`build_client_factory`) and mounts it under the
server's name as a namespace. ``reload`` rebuilds all mounts from the current store
contents — the gateway's coarse update model (no live hot-swap in v1).

FastMCP has no ``unmount``; mounted servers are appended to ``mcp.providers`` (a
plain list, with the gateway's own static provider(s) first). The builder snapshots
that baseline at construction and, on every ``reload``, resets the list to the
baseline before re-mounting — so reloads are idempotent and dropped/disabled servers
disappear.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.proxy import FastMCPProxy

from mcp_gateway.access import AccessPolicy
from mcp_gateway.connect import build_client_factory
from mcp_gateway.hooks import Hooks
from mcp_gateway.store.base import Store

logger = logging.getLogger("mcp_gateway.builder")


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

    async def reload(self) -> None:
        """Rebuild all proxy mounts from the current registry.

        Resets to the baseline providers, then mounts a proxy per enabled server
        under its name as a namespace.  Also rebuilds the access policy from the
        full server + group lists so namespace splitting and allow/deny rules are
        current after every reload.
        """
        servers = await self.store.list_servers()
        groups = await self.store.list_groups()

        if self.policy is not None:
            # Rebuild from ALL servers (not just enabled) so every namespace is
            # known for split_namespace, even if the server is currently disabled.
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

        logger.info("Gateway reloaded: %d server(s) mounted.", mounted)
