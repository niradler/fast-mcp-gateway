"""Builds the persisted tool catalog by introspecting the upstreams.

Catalog rows are namespaced as ``"<server.name>_<bare>"`` — the join FastMCP uses for
``mount(namespace=...)`` and that :mod:`fast_gateway.access` relies on. The catalog
is the gateway's only source for ``tools/list`` and search meta-tools on the request path;
fan-out to upstreams happens only on reload.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import mcp.types
from fastmcp.tools.base import Tool
from fastmcp.utilities.components import get_fastmcp_metadata
from mcp.types import ToolAnnotations

from fast_gateway.connect import build_client_factory
from fast_gateway.hooks import Hooks
from fast_gateway.models import CatalogTool, ServerRecord

logger = logging.getLogger("fast_gateway.catalog")


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


async def collect_catalog(
    servers: Sequence[ServerRecord], hooks: Hooks
) -> tuple[list[CatalogTool], set[str]]:
    """Introspect all enabled servers concurrently; return ``(catalog, failed_ids)``.

    Failures are isolated per server — a transient blip does not wipe the rest.
    Each failure fires the ``connect_error`` hooks; the caller uses ``failed_ids``
    to retain last-known rows for unreachable upstreams.
    """
    enabled = [server for server in servers if server.enabled]
    results = await asyncio.gather(
        *(_introspect_server(server, hooks) for server in enabled),
        return_exceptions=True,
    )
    catalog: list[CatalogTool] = []
    failed_ids: set[str] = set()
    for server, result in zip(enabled, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "Catalog introspection failed for server %r; retaining last-known tools. Cause: %s",
                server.name,
                result,
                exc_info=result,
            )
            if isinstance(result, Exception):
                await hooks.dispatch_connect_error(server, result)
            failed_ids.add(server.id)
            continue
        catalog.extend(result)
    return catalog, failed_ids
