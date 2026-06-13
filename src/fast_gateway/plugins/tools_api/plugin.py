"""ToolsApiPlugin: REST routes to list, describe, and invoke the gateway's tools.

The routes drive the parent FastMCP server through an in-process client — no
HTTP loopback — so every call still passes the full governance chain (hooks,
access policy, group scoping, confirmation). Mounted under the admin prefix
(``<admin_prefix>/tools``), they inherit whatever auth guards the admin surface.
"""

from __future__ import annotations

from typing import Any, cast

import mcp.types as mt
from fastapi import APIRouter, HTTPException, status
from fastmcp import Client, FastMCP
from pydantic import BaseModel, Field

from fast_gateway.access import current_group, list_full_catalog
from fast_gateway.plugins import GatewayContext, PluginContributions


class ToolSummary(BaseModel):
    """Compact listing entry for one gateway tool."""

    name: str
    title: str | None = None
    description: str | None = None


class ToolDetail(ToolSummary):
    """Full schema view of one gateway tool."""

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None


class ToolCallRequest(BaseModel):
    """Invocation payload: tool arguments plus optional group scope and timeout."""

    arguments: dict[str, Any] = Field(default_factory=dict)
    group: str | None = Field(
        default=None,
        description="Scope the call to a group, exactly like the /mcp/g/{group} endpoint.",
    )
    timeout: float | None = Field(default=None, gt=0)


class ToolCallResponse(BaseModel):
    """MCP call result passed through verbatim: in-band errors set ``is_error``."""

    is_error: bool
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None = None


class ToolsApiPlugin:
    """Gateway plugin exposing list / describe / invoke REST routes for MCP tools.

    Lets non-MCP clients (dashboards, scripts, services) interact with the
    governed catalog over plain HTTP. Denials and upstream tool failures are
    reported in-band via ``is_error``, mirroring MCP wire semantics.
    """

    name = "tools"

    def contributions(self, context: GatewayContext) -> PluginContributions:
        return PluginContributions(admin_router=_build_tools_router(context.mcp))


async def _scoped_tools(mcp: FastMCP, group: str | None) -> list[mt.Tool]:
    """List tools for the REST surface: always the full governed catalog.

    This discovery API is meant to show every available tool (a dashboard), so it
    opts out of ``list_mode='meta'`` hiding via ``list_full_catalog`` while keeping
    the access-policy and group narrowing applied by the middleware.
    """
    group_token = current_group.set(group)
    full_token = list_full_catalog.set(True)
    try:
        async with Client(mcp) as client:
            return cast("list[mt.Tool]", await client.list_tools())
    finally:
        list_full_catalog.reset(full_token)
        current_group.reset(group_token)


def _to_detail(tool: mt.Tool) -> ToolDetail:
    annotations = tool.annotations.model_dump(mode="json") if tool.annotations else None
    return ToolDetail(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_schema=tool.inputSchema,
        output_schema=tool.outputSchema,
        annotations=annotations,
    )


def _build_tools_router(mcp: FastMCP) -> APIRouter:
    """REST routes over the gateway's governed tool catalog."""
    router = APIRouter(tags=["tools"])

    @router.get("", response_model=list[ToolSummary])
    async def list_tools(group: str | None = None) -> list[ToolSummary]:
        """List the tools visible through the gateway, optionally scoped to a group."""
        tools = await _scoped_tools(mcp, group)
        return [ToolSummary(name=t.name, title=t.title, description=t.description) for t in tools]

    @router.get("/{name}", response_model=ToolDetail)
    async def describe_tool(name: str, group: str | None = None) -> ToolDetail:
        """Return the full schema of one tool; group-denied tools read as not found."""
        tools = await _scoped_tools(mcp, group)
        for tool in tools:
            if tool.name == name:
                return _to_detail(tool)
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No tool named {name!r}.")

    @router.post("/{name}/call", response_model=ToolCallResponse)
    async def call_tool(name: str, request: ToolCallRequest) -> ToolCallResponse:
        """Invoke a tool through the full governance chain; errors are in-band."""
        token = current_group.set(request.group)
        try:
            async with Client(mcp) as client:
                result = await client.call_tool(
                    name,
                    request.arguments,
                    timeout=request.timeout,
                    raise_on_error=False,
                )
        finally:
            current_group.reset(token)
        return ToolCallResponse(
            is_error=result.is_error,
            content=[block.model_dump(mode="json") for block in result.content],
            structured_content=result.structured_content,
        )

    return router
