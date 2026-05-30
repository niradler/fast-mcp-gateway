"""Tests for the ``search_tools`` / ``describe_tool`` meta-tools.

The tools are exercised end to end through an in-process FastMCP ``Client`` against
a store whose catalog is seeded directly (no upstream needed). They verify keyword
search, progressive disclosure via ``describe_tool``, and that the access policy
scopes results so denied tools are neither searchable nor describable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from mcp_gateway.access import AccessPolicy
from mcp_gateway.app import create_gateway
from mcp_gateway.models import CatalogTool, ServerRecord
from mcp_gateway.search import register_search_tools
from mcp_gateway.store.sqlite import SqliteStore

# Stores opened by ``_gateway`` are held alive by the FastMCP server/Client, so they are
# not garbage-collected between tests; their aiosqlite worker threads are non-daemon and
# would block interpreter shutdown. Close them after each test.
_OPEN_STORES: list[SqliteStore] = []


@pytest.fixture(autouse=True)
async def _close_open_stores() -> AsyncIterator[None]:
    yield
    while _OPEN_STORES:
        await _OPEN_STORES.pop().close()


def _catalog() -> list[CatalogTool]:
    return [
        CatalogTool(
            server_id="s1",
            namespace="math",
            name="math_add",
            bare_name="add",
            title="Add",
            description="Add two numbers together.",
            tags=["arith"],
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
            output_schema={"type": "integer"},
        ),
        CatalogTool(
            server_id="s1",
            namespace="math",
            name="math_delete_all",
            bare_name="delete_all",
            description="Delete every stored number.",
            tags=["danger"],
        ),
        CatalogTool(
            server_id="s2",
            namespace="text",
            name="text_upper",
            bare_name="upper",
            description="Uppercase a string.",
            tags=["string"],
        ),
    ]


def _server(name: str, server_id: str, deny: list[str] | None = None) -> ServerRecord:
    return ServerRecord(
        id=server_id,
        name=name,
        url=f"https://{name}.example.com/mcp",
        deny=deny or [],
    )


async def _gateway(policy: AccessPolicy | None) -> FastMCP:
    """A FastMCP with the meta-tools registered over a pre-seeded catalog."""
    store = SqliteStore(":memory:")
    await store.initialize()
    await store.replace_catalog(_catalog())
    _OPEN_STORES.append(store)
    mcp: FastMCP = FastMCP("search-test")
    register_search_tools(mcp, store, policy)
    return mcp


async def _search(client: Client[Any], query: str = "", limit: int = 10) -> list[dict[str, Any]]:
    result = await client.call_tool("search_tools", {"query": query, "limit": limit})
    return cast("list[dict[str, Any]]", result.data)


# ---------------------------------------------------------------------------
# search_tools
# ---------------------------------------------------------------------------


async def test_search_finds_by_name() -> None:
    mcp = await _gateway(policy=None)
    async with Client(mcp) as client:
        hits = await _search(client, "add")
    assert [h["name"] for h in hits] == ["math_add"]
    assert hits[0]["description"] == "Add two numbers together."
    assert hits[0]["tags"] == ["arith"]


async def test_search_finds_by_description_token() -> None:
    mcp = await _gateway(policy=None)
    async with Client(mcp) as client:
        hits = await _search(client, "string")
    assert [h["name"] for h in hits] == ["text_upper"]


async def test_search_empty_query_browses_all() -> None:
    mcp = await _gateway(policy=None)
    async with Client(mcp) as client:
        hits = await _search(client, "")
    assert {h["name"] for h in hits} == {"math_add", "math_delete_all", "text_upper"}


async def test_search_respects_limit() -> None:
    mcp = await _gateway(policy=None)
    async with Client(mcp) as client:
        hits = await _search(client, "", limit=1)
    assert len(hits) == 1


async def test_search_excludes_denied_tools() -> None:
    policy = AccessPolicy()
    policy.rebuild([_server("math", "s1", deny=["delete_*"]), _server("text", "s2")], [])
    mcp = await _gateway(policy)
    async with Client(mcp) as client:
        hits = await _search(client, "delete")
    assert hits == []


# ---------------------------------------------------------------------------
# describe_tool
# ---------------------------------------------------------------------------


async def test_describe_returns_full_schema() -> None:
    mcp = await _gateway(policy=None)
    async with Client(mcp) as client:
        result = await client.call_tool("describe_tool", {"name": "math_add"})
    described = result.data
    assert described["name"] == "math_add"
    assert described["title"] == "Add"
    assert described["input_schema"]["properties"]["a"]["type"] == "integer"
    assert described["output_schema"] == {"type": "integer"}
    assert described["tags"] == ["arith"]


async def test_describe_unknown_tool_errors() -> None:
    mcp = await _gateway(policy=None)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="No tool named"):
            await client.call_tool("describe_tool", {"name": "nope"})


async def test_describe_denied_tool_reads_as_not_found() -> None:
    """A denied tool must not be describable, and must not leak its existence."""
    policy = AccessPolicy()
    policy.rebuild([_server("math", "s1", deny=["delete_*"]), _server("text", "s2")], [])
    mcp = await _gateway(policy)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="No tool named"):
            await client.call_tool("describe_tool", {"name": "math_delete_all"})


# ---------------------------------------------------------------------------
# tools/list is served from the snapshot but still surfaces local meta-tools
# ---------------------------------------------------------------------------


async def test_tools_list_merges_meta_tools_with_snapshot() -> None:
    """The gateway's own meta-tools stay discoverable alongside the upstream snapshot."""
    store = SqliteStore(":memory:")
    await store.initialize()
    await store.replace_catalog(_catalog())
    _OPEN_STORES.append(store)
    gateway = create_gateway(store)
    async with Client(gateway.mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"search_tools", "describe_tool"} <= names
    assert {"math_add", "math_delete_all", "text_upper"} <= names
