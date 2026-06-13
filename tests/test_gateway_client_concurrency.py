"""Proves group scoping isolates across CONCURRENT in-process gateway calls.

The risk: ``current_group`` is a ContextVar set before the in-process client
session. If concurrent differently-scoped calls shared one context, group
enforcement would cross-talk. This drives many overlapping calls under
distinct groups via ``asyncio.gather`` and asserts each is judged by its own
group, and that an ungrouped call after grouped ones is not silently scoped.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastmcp.exceptions import ToolError

from fast_gateway.app import Gateway, create_gateway
from fast_gateway.models import CatalogTool, GroupCreate, ServerCreate
from fast_gateway.store.sqlite import SqliteStore

_OPEN_STORES: list[SqliteStore] = []


@pytest.fixture(autouse=True)
async def _close_open_stores() -> AsyncIterator[None]:
    yield
    while _OPEN_STORES:
        await _OPEN_STORES.pop().close()


async def _gateway() -> Gateway:
    store = SqliteStore(":memory:")
    await store.initialize()
    _OPEN_STORES.append(store)
    math = await store.create_server(
        ServerCreate(name="math", url="https://math.example.com/mcp", enabled=False)
    )
    text = await store.create_server(
        ServerCreate(name="text", url="https://text.example.com/mcp", enabled=False)
    )
    await store.create_group(GroupCreate(name="only_math", member_server_ids=[math.id]))
    await store.create_group(GroupCreate(name="only_text", member_server_ids=[text.id]))
    gateway = create_gateway(store)
    await gateway.reload()
    await store.replace_catalog(
        [
            CatalogTool(server_id=math.id, namespace="math", name="math_add", bare_name="add"),
            CatalogTool(server_id=text.id, namespace="text", name="text_up", bare_name="up"),
        ]
    )
    return gateway


async def test_concurrent_list_tools_do_not_leak_group() -> None:
    gateway = await _gateway()

    async def names(group: str | None) -> set[str]:
        tools = await gateway.list_tools(group=group)
        return {t.name for t in tools}

    results = await asyncio.gather(
        *(
            names("only_math") if i % 3 == 0 else names("only_text") if i % 3 == 1 else names(None)
            for i in range(30)
        )
    )

    for i, got in enumerate(results):
        if i % 3 == 0:
            assert "math_add" in got, f"only_math missing its tool: {got}"
            assert "text_up" not in got, f"only_math leaked text: {got}"
        elif i % 3 == 1:
            assert "text_up" in got, f"only_text missing its tool: {got}"
            assert "math_add" not in got, f"only_text leaked math: {got}"
        else:
            assert {"math_add", "text_up"} <= got, f"ungrouped wrongly scoped: {got}"


async def test_concurrent_calls_enforced_per_call_group() -> None:
    gateway = await _gateway()

    @gateway.mcp.tool
    def math_add(a: int, b: int) -> int:
        return a + b

    @gateway.mcp.tool
    def text_up(value: str) -> str:
        return value.upper()

    async def allowed() -> int:
        result = await gateway.call_tool("math_add", {"a": 1, "b": 2}, group="only_math")
        return int(result.data)

    async def forbidden() -> str:
        with pytest.raises(ToolError, match="not permitted"):
            await gateway.call_tool("math_add", {"a": 1, "b": 2}, group="only_text")
        return "denied"

    outcomes = await asyncio.gather(*(allowed() if i % 2 == 0 else forbidden() for i in range(20)))
    assert outcomes == [3 if i % 2 == 0 else "denied" for i in range(20)]
