"""Turns the registry into mounted proxies on the parent gateway server.

For each enabled server record the builder creates a FastMCP proxy (via
``create_proxy`` with our client factory) and mounts it under the server's name as a
namespace. ``reload`` rebuilds all mounts from the current store contents — the
gateway's coarse update model (no live hot-swap in v1).
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from mcp_gateway.hooks import Hooks
from mcp_gateway.store.base import Store

logger = logging.getLogger("mcp_gateway.builder")


class GatewayBuilder:
    """Builds and rebuilds proxy mounts on a parent FastMCP server."""

    def __init__(self, mcp: FastMCP, store: Store, hooks: Hooks) -> None:
        self.mcp = mcp
        self.store = store
        self.hooks = hooks

    async def reload(self) -> None:
        """Rebuild all proxy mounts from the current registry.

        NOTE: scaffolding stub. Milestone 1 reads enabled servers from the store,
        builds a proxy per server with ``build_client_factory`` + allow/deny wiring,
        and mounts each under ``server.name`` as a namespace.
        """
        raise NotImplementedError("GatewayBuilder.reload — Milestone 1")
