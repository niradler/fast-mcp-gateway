"""Tests for the gateway Plugin system (independent of any specific plugin)."""

from __future__ import annotations

import pytest

from mcp_gateway.hooks import Hooks


def test_plugin_contributions_defaults_are_empty() -> None:
    from mcp_gateway.plugins import PluginContributions

    c = PluginContributions()
    assert c.hooks == Hooks()
    assert c.middleware == []
    assert c.admin_router is None
    assert c.mounts == []
    assert c.register_tools is None


def test_minimal_plugin_satisfies_protocol() -> None:
    from mcp_gateway.plugins import Plugin, PluginContributions

    class NoopPlugin:
        name = "noop"

        def contributions(self) -> PluginContributions:
            return PluginContributions()

        async def setup(self) -> None: ...

        async def teardown(self) -> None: ...

    plugin: Plugin = NoopPlugin()
    assert isinstance(plugin, Plugin)
    assert plugin.name == "noop"
    assert isinstance(plugin.contributions(), PluginContributions)


def test_merge_hooks_concatenates_each_seam_in_order() -> None:
    from mcp_gateway.hooks import merge_hooks

    async def a(ctx):
        return None

    async def b(ctx):
        return None

    base = Hooks(pre_tool_call=[a])
    extra = Hooks(pre_tool_call=[b])
    merged = merge_hooks(base, extra)

    assert merged.pre_tool_call == [a, b]
    # inputs untouched
    assert base.pre_tool_call == [a]
    assert extra.pre_tool_call == [b]


def test_merge_hooks_empty_returns_empty() -> None:
    from mcp_gateway.hooks import merge_hooks

    assert merge_hooks() == Hooks()


@pytest.mark.asyncio
async def test_create_gateway_applies_plugin_contributions() -> None:
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from mcp_gateway.app import create_gateway
    from mcp_gateway.plugins import PluginContributions
    from mcp_gateway.store.sqlite import SqliteStore

    events: list[str] = []

    async def pre(ctx):  # type: ignore[no-untyped-def]
        return None

    registered: list[str] = []

    def register_tools(mcp) -> None:  # type: ignore[no-untyped-def]
        registered.append(mcp.name)

    router = APIRouter()

    @router.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "1"}

    class DemoPlugin:
        name = "demo"

        def contributions(self) -> PluginContributions:
            return PluginContributions(
                hooks=Hooks(pre_tool_call=[pre]),
                admin_router=router,
                register_tools=register_tools,
            )

        async def setup(self) -> None:
            events.append("setup")

        async def teardown(self) -> None:
            events.append("teardown")

    store = SqliteStore(":memory:")
    gateway = create_gateway(store, plugins=[DemoPlugin()])

    assert registered == [gateway.mcp.name]
    routes = {getattr(r, "path", "") for r in gateway.admin_router.routes}
    assert any("/demo/ping" in p for p in routes)

    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    with TestClient(app):
        pass
    assert events == ["setup", "teardown"]
