"""Tests for PolicyPlugin: deny / confirm / audit governance bundled as a plugin."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from fast_gateway.app import Gateway, create_gateway
from fast_gateway.hooks import ConfirmationContext, Hooks
from fast_gateway.plugins import GatewayContext, Plugin
from fast_gateway.plugins.policy import PolicyPlugin
from fast_gateway.store.sqlite import SqliteStore

_OPEN_STORES: list[SqliteStore] = []


@pytest.fixture(autouse=True)
async def _close_open_stores() -> AsyncIterator[None]:
    yield
    while _OPEN_STORES:
        await _OPEN_STORES.pop().close()


def _context() -> GatewayContext:
    async def _reload() -> None:
        return None

    return GatewayContext(store=SqliteStore(":memory:"), mcp=FastMCP("t"), reload=_reload)


async def _gateway(plugin: PolicyPlugin, hooks: Hooks | None = None) -> Gateway:
    store = SqliteStore(":memory:")
    await store.initialize()
    _OPEN_STORES.append(store)
    plugins: list[Plugin] = [plugin]
    gateway = create_gateway(store, hooks, plugins=plugins)

    @gateway.mcp.tool
    def danger_zap() -> str:
        return "zapped"

    @gateway.mcp.tool
    def safe_echo(value: str) -> str:
        return value

    return gateway


def test_contributions_empty_policy_adds_only_audit() -> None:
    contributions = PolicyPlugin().contributions(_context())
    assert contributions.hooks.pre_tool_call == []
    assert len(contributions.hooks.post_tool_call) == 1


def test_contributions_without_audit_is_empty() -> None:
    contributions = PolicyPlugin(audit=False).contributions(_context())
    assert contributions.hooks.pre_tool_call == []
    assert contributions.hooks.post_tool_call == []


def test_contributions_deny_and_confirm_register_pre_hooks() -> None:
    contributions = PolicyPlugin(deny=["a_*"], confirm=["b_*"], audit=False).contributions(
        _context()
    )
    assert len(contributions.hooks.pre_tool_call) == 2


async def test_denied_tool_is_blocked() -> None:
    gateway = await _gateway(PolicyPlugin(deny=["danger_*"], audit=False))
    with pytest.raises(ToolError, match="denied by policy"):
        await gateway.call_tool("danger_zap", {})


async def test_unmatched_tool_passes_through() -> None:
    gateway = await _gateway(PolicyPlugin(deny=["danger_*"], audit=False))
    result = await gateway.call_tool("safe_echo", {"value": "hi"})
    assert result.data == "hi"


async def test_confirm_without_handler_fails_safe() -> None:
    gateway = await _gateway(PolicyPlugin(confirm=["danger_*"], audit=False))
    with pytest.raises(ToolError, match="requires confirmation"):
        await gateway.call_tool("danger_zap", {})


async def test_confirm_with_approving_handler_proceeds() -> None:
    async def _approve(context: ConfirmationContext) -> bool:
        return True

    gateway = await _gateway(
        PolicyPlugin(confirm=["danger_*"], audit=False), Hooks(confirmation=[_approve])
    )
    result = await gateway.call_tool("danger_zap", {})
    assert result.data == "zapped"


async def test_confirm_with_rejecting_handler_blocks() -> None:
    async def _reject(context: ConfirmationContext) -> bool:
        return False

    gateway = await _gateway(
        PolicyPlugin(confirm=["danger_*"], audit=False), Hooks(confirmation=[_reject])
    )
    with pytest.raises(ToolError):
        await gateway.call_tool("danger_zap", {})
