"""``search_tools`` and ``describe_tool`` meta-tools for exploring large tool fleets.

Both read the FTS5-ranked catalog snapshot; no upstream fan-out. Policy and group
scoping are applied after ranking ``_CANDIDATE_CAP`` candidates, so a heavily restricted
caller in a large fleet may miss allowed tools ranked below the cap. Denied tools read
as "not found" (no existence leak).
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from fast_gateway.access import AccessPolicy, current_group
from fast_gateway.models import CatalogTool
from fast_gateway.store.base import Store

_MAX_LIMIT = 50
_CANDIDATE_CAP = 200
_DESCRIPTION_PREVIEW = 160


def _allowed(tools: list[CatalogTool], policy: AccessPolicy | None) -> list[CatalogTool]:
    """Drop tools the caller may not see, scoped to the current request's group."""
    if policy is None:
        return tools
    return policy.filter_tools(tools, group=current_group.get())


def _preview(description: str | None) -> str:
    """Compact one-line description for search hits (full text via describe_tool)."""
    if not description:
        return ""
    first_line = description.strip().splitlines()[0] if description.strip() else ""
    if len(first_line) > _DESCRIPTION_PREVIEW:
        return first_line[: _DESCRIPTION_PREVIEW - 1].rstrip() + "…"
    return first_line


def register_search_tools(mcp: FastMCP, store: Store, policy: AccessPolicy | None = None) -> None:
    """Register the ``search_tools`` / ``describe_tool`` meta-tools on ``mcp``."""

    @mcp.tool(
        name="search_tools",
        description=(
            "Search the gateway's tools by keyword and return compact matches "
            "(name, one-line description, tags). Use describe_tool for a tool's full "
            "schema. An empty query lists available tools."
        ),
    )
    async def search_tools(query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        capped = max(1, min(limit, _MAX_LIMIT))
        candidates = await store.search_catalog(query, limit=_CANDIDATE_CAP)
        visible = _allowed(candidates, policy)[:capped]
        return [
            {"name": t.name, "description": _preview(t.description), "tags": t.tags}
            for t in visible
        ]

    @mcp.tool(
        name="describe_tool",
        description=(
            "Return the full schema for one tool by its exact name: description, "
            "input/output JSON schema, tags, and annotations."
        ),
    )
    async def describe_tool(name: str) -> dict[str, Any]:
        tool = await store.get_catalog_tool(name)
        if tool is None or not _allowed([tool], policy):
            raise ToolError(f"No tool named {name!r}.")
        return {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "tags": tool.tags,
            "input_schema": tool.parameters,
            "output_schema": tool.output_schema,
            "annotations": tool.annotations,
        }
