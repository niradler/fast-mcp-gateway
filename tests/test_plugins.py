"""Tests for the gateway Plugin system (independent of any specific plugin)."""

from __future__ import annotations

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
