"""Tests for the hook middleware dispatch, focused on the confirmation flow:
a ``pre_tool_call`` returning ``REQUIRE_CONFIRMATION`` must trigger the confirmation
hooks, and a rejection (or absence of any handler) must deny the call."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from mcp_gateway.hooks import (
    ConfirmationContext,
    HookMiddleware,
    Hooks,
    ToolCallResult,
    ToolDecision,
)


def make_context(name: str = "deploy", arguments: dict[str, Any] | None = None) -> Any:
    """A duck-typed stand-in for MiddlewareContext (only .message.{name,arguments})."""
    return SimpleNamespace(message=SimpleNamespace(name=name, arguments=arguments or {}))


async def call_next(context: Any) -> str:
    return "called"


async def require_confirmation(context: Any) -> ToolCallResult:
    return ToolCallResult(decision=ToolDecision.REQUIRE_CONFIRMATION, reason="needs approval")


async def test_require_confirmation_triggers_confirmation_hook() -> None:
    seen: list[ConfirmationContext] = []

    async def approve(ctx: ConfirmationContext) -> bool:
        seen.append(ctx)
        return True

    middleware = HookMiddleware(Hooks(pre_tool_call=[require_confirmation], confirmation=[approve]))
    result = await middleware.on_call_tool(make_context(), call_next)

    assert result == "called"
    assert len(seen) == 1
    assert seen[0].tool_name == "deploy"
    assert seen[0].reason == "needs approval"


async def test_rejected_confirmation_denies_call() -> None:
    async def reject(ctx: ConfirmationContext) -> bool:
        return False

    middleware = HookMiddleware(Hooks(pre_tool_call=[require_confirmation], confirmation=[reject]))
    with pytest.raises(ToolError):
        await middleware.on_call_tool(make_context(), call_next)


async def test_require_confirmation_without_hook_fails_safe() -> None:
    middleware = HookMiddleware(Hooks(pre_tool_call=[require_confirmation]))
    with pytest.raises(ToolError):
        await middleware.on_call_tool(make_context(), call_next)


async def test_deny_decision_blocks_call() -> None:
    async def deny(context: Any) -> ToolCallResult:
        return ToolCallResult(decision=ToolDecision.DENY, reason="nope")

    middleware = HookMiddleware(Hooks(pre_tool_call=[deny]))
    with pytest.raises(ToolError):
        await middleware.on_call_tool(make_context(), call_next)


async def test_continue_passes_through_and_post_hook_transforms() -> None:
    async def post(context: Any, response: Any) -> str:
        return f"{response}+post"

    middleware = HookMiddleware(Hooks(post_tool_call=[post]))
    result = await middleware.on_call_tool(make_context(), call_next)

    assert result == "called+post"
