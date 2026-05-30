"""Builds the persisted tool catalog by introspecting the upstreams.

The catalog is a snapshot of every enabled upstream's tools, rebuilt on each
``GatewayBuilder.reload``. It is the single source of truth for the gateway's
``tools/list`` and for the ``search_tools`` / ``describe_tool`` meta-tools, so the
gateway never has to fan out to upstreams on the request path (only on reload).

Two conversions live here:

- :func:`collect_catalog` — connect to each enabled server, list its tools, and
  turn them into namespaced :class:`CatalogTool` rows. Failures are isolated per
  server so one unreachable upstream does not wipe the rest of the catalog.
- :func:`catalog_tool_to_fastmcp` — rebuild a FastMCP :class:`Tool` from a stored
  row so ``HookMiddleware`` can answer ``tools/list`` from the snapshot.

The namespaced name is ``"<server.name>_<bare>"`` — the single-underscore join
FastMCP uses for ``mount(namespace=...)`` and that :mod:`mcp_gateway.access`
relies on for namespace splitting.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import mcp.types
from fastmcp.tools.base import Tool
from fastmcp.utilities.components import get_fastmcp_metadata
from mcp.types import ToolAnnotations

from mcp_gateway.connect import build_client_factory
from mcp_gateway.hooks import Hooks
from mcp_gateway.models import CatalogTool, ServerRecord

logger = logging.getLogger("mcp_gateway.catalog")


def catalog_tool_from_mcp(server: ServerRecord, tool: mcp.types.Tool) -> CatalogTool:
    """Convert one upstream MCP tool into a namespaced :class:`CatalogTool`."""
    fastmcp_meta = get_fastmcp_metadata(tool.meta)
    annotations = tool.annotations.model_dump(exclude_none=True) if tool.annotations else None
    return CatalogTool(
        server_id=server.id,
        namespace=server.name,
        name=f"{server.name}_{tool.name}",
        bare_name=tool.name,
        title=tool.title,
        description=tool.description,
        tags=list(fastmcp_meta.get("tags", [])),
        parameters=tool.inputSchema or {},
        output_schema=tool.outputSchema,
        annotations=annotations,
    )


def catalog_tool_to_fastmcp(tool: CatalogTool) -> Tool:
    """Rebuild a FastMCP :class:`Tool` from a stored catalog row (for ``tools/list``).

    Only the listing-relevant fields are restored; the result is never executed
    (calls route through the mounted proxy), so ``run`` is intentionally absent.
    """
    return Tool(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        tags=set(tool.tags),
        parameters=tool.parameters,
        output_schema=tool.output_schema,
        annotations=ToolAnnotations(**tool.annotations) if tool.annotations else None,
    )


async def _introspect_server(server: ServerRecord, hooks: Hooks) -> list[CatalogTool]:
    """List one upstream's tools and namespace them. Raises on connection failure."""
    factory = build_client_factory(server, hooks)
    client = await factory()
    async with client:
        upstream_tools = await client.list_tools()
    return [catalog_tool_from_mcp(server, tool) for tool in upstream_tools]


async def collect_catalog(servers: Sequence[ServerRecord], hooks: Hooks) -> list[CatalogTool]:
    """Introspect every enabled server concurrently and return the combined catalog.

    Servers are introspected in parallel, so reload latency is bounded by the
    slowest single upstream rather than the sum. A server that fails to connect or
    list its tools is logged and skipped, so a single bad upstream contributes
    nothing rather than breaking the whole reload.
    """
    enabled = [server for server in servers if server.enabled]
    results = await asyncio.gather(
        *(_introspect_server(server, hooks) for server in enabled),
        return_exceptions=True,
    )
    catalog: list[CatalogTool] = []
    for server, result in zip(enabled, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Catalog introspection failed for server %r; skipping.", server.name)
            continue
        catalog.extend(result)
    return catalog
