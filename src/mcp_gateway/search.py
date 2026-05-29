"""Local meta-tools for exploring large tool fleets without dumping the catalog.

Registers two tools on the parent gateway server:

- ``search_tools(query)`` — keyword lookup over the aggregated catalog.
- ``describe_tool(name)`` — full schema for one tool (progressive disclosure).
"""

from __future__ import annotations

from fastmcp import FastMCP


def register_search_tools(mcp: FastMCP) -> None:
    """Register the search/describe meta-tools on ``mcp``.

    NOTE: scaffolding stub — no tools registered yet. Milestone 4 adds the catalog
    index, the two meta-tools, and a cache.
    """
    # TODO(Milestone 4): build catalog index + register search_tools / describe_tool.
    return None
