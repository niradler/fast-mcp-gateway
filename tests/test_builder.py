"""Tests for ``GatewayBuilder.reload``: enabled servers get mounted, disabled ones
are skipped, and reloads are idempotent (no stale or duplicate mounts)."""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_gateway.builder import GatewayBuilder
from mcp_gateway.hooks import Hooks
from mcp_gateway.models import ServerCreate
from mcp_gateway.store.sqlite import SqliteStore


def server(name: str, *, enabled: bool = True) -> ServerCreate:
    return ServerCreate(name=name, url=f"https://{name}.example.com/mcp", enabled=enabled)


async def make_builder() -> tuple[FastMCP, GatewayBuilder, SqliteStore]:
    store = SqliteStore(":memory:")
    await store.initialize()
    mcp: FastMCP = FastMCP("test-gateway")
    builder = GatewayBuilder(mcp=mcp, store=store, hooks=Hooks())
    return mcp, builder, store


async def test_reload_mounts_only_enabled_servers() -> None:
    mcp, builder, store = await make_builder()
    baseline = len(mcp.providers)
    await store.create_server(server("alpha"))
    await store.create_server(server("beta", enabled=False))

    await builder.reload()

    assert len(mcp.providers) == baseline + 1


async def test_reload_is_idempotent() -> None:
    mcp, builder, store = await make_builder()
    baseline = len(mcp.providers)
    await store.create_server(server("alpha"))
    await store.create_server(server("gamma"))

    await builder.reload()
    after_first = len(mcp.providers)
    await builder.reload()

    assert after_first == baseline + 2
    assert len(mcp.providers) == after_first


async def test_reload_drops_removed_servers() -> None:
    mcp, builder, store = await make_builder()
    baseline = len(mcp.providers)
    created = await store.create_server(server("alpha"))
    await builder.reload()
    assert len(mcp.providers) == baseline + 1

    await store.delete_server(created.id)
    await builder.reload()

    assert len(mcp.providers) == baseline
