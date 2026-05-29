"""Tests for the hook middleware dispatch, focused on the confirmation flow:
a ``pre_tool_call`` returning ``REQUIRE_CONFIRMATION`` must trigger the confirmation
hooks, and a rejection (or absence of any handler) must deny the call."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from mcp_gateway.access import AccessPolicy
from mcp_gateway.hooks import (
    ConfirmationContext,
    HookMiddleware,
    Hooks,
    ToolCallResult,
    ToolDecision,
)
from mcp_gateway.models import ServerRecord


def make_server_record(
    name: str,
    *,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> ServerRecord:
    return ServerRecord(
        id=name,
        name=name,
        url=f"https://{name}.example.com/mcp",
        allow=allow or [],
        deny=deny or [],
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


# ---------------------------------------------------------------------------
# on_list_tools tests
# ---------------------------------------------------------------------------


def make_tool(name: str) -> Any:
    """Lightweight stand-in for a Tool — hooks only touch `.name`."""
    return SimpleNamespace(name=name)


async def list_tools_call_next(context: Any) -> list[Any]:
    return [make_tool("math_add"), make_tool("math_sub"), make_tool("math_mul")]


async def test_pre_list_tools_drops_a_tool() -> None:
    async def drop_sub(context: Any, tools: Any) -> Any:
        return [t for t in tools if t.name != "math_sub"]

    middleware = HookMiddleware(Hooks(pre_list_tools=[drop_sub]))
    result = await middleware.on_list_tools(make_context(), list_tools_call_next)

    names = [t.name for t in result]
    assert "math_sub" not in names
    assert "math_add" in names
    assert "math_mul" in names


async def test_pre_list_tools_pass_through() -> None:
    async def identity(context: Any, tools: Any) -> Any:
        return tools

    middleware = HookMiddleware(Hooks(pre_list_tools=[identity]))
    result = await middleware.on_list_tools(make_context(), list_tools_call_next)

    assert [t.name for t in result] == ["math_add", "math_sub", "math_mul"]


async def test_pre_list_tools_chained() -> None:
    """First hook drops math_add; second renames math_sub to calc_subtract."""

    async def drop_add(context: Any, tools: Any) -> Any:
        return [t for t in tools if t.name != "math_add"]

    async def rename_sub(context: Any, tools: Any) -> Any:
        return [SimpleNamespace(name="calc_subtract") if t.name == "math_sub" else t for t in tools]

    middleware = HookMiddleware(Hooks(pre_list_tools=[drop_add, rename_sub]))
    result = await middleware.on_list_tools(make_context(), list_tools_call_next)

    names = [t.name for t in result]
    assert names == ["calc_subtract", "math_mul"]


async def test_on_list_tools_no_hooks() -> None:
    middleware = HookMiddleware(Hooks())
    result = await middleware.on_list_tools(make_context(), list_tools_call_next)

    assert [t.name for t in result] == ["math_add", "math_sub", "math_mul"]


# ---------------------------------------------------------------------------
# Policy enforcement in on_list_tools / on_call_tool
# ---------------------------------------------------------------------------


async def test_policy_filters_list_tools() -> None:
    """Policy filter runs before user hooks: a denied namespaced tool is dropped."""
    policy = AccessPolicy()
    policy.rebuild([make_server_record("math", allow=["add", "sub"])], [])

    # call_next returns add + mul; mul is denied by the allow list
    async def call_next_with_mul(context: Any) -> list[Any]:
        return [make_tool("math_add"), make_tool("math_mul")]

    middleware = HookMiddleware(Hooks(), policy=policy)
    result = await middleware.on_list_tools(make_context(), call_next_with_mul)

    names = [t.name for t in result]
    assert "math_add" in names
    assert "math_mul" not in names


async def test_policy_filter_then_user_hook_run_in_order() -> None:
    """Policy filter runs first, user pre_list_tools hooks run after on what remains."""
    policy = AccessPolicy()
    policy.rebuild([make_server_record("math", allow=["add"])], [])

    seen: list[str] = []

    async def recording_hook(context: Any, tools: Any) -> Any:
        seen.extend(t.name for t in tools)
        return tools

    async def call_next_multi(context: Any) -> list[Any]:
        return [make_tool("math_add"), make_tool("math_mul")]

    middleware = HookMiddleware(Hooks(pre_list_tools=[recording_hook]), policy=policy)
    await middleware.on_list_tools(make_context(), call_next_multi)

    # User hook only sees what the policy left — math_mul was already dropped
    assert "math_add" in seen
    assert "math_mul" not in seen


async def test_no_policy_list_tools_unchanged() -> None:
    """Without a policy, on_list_tools returns the full catalog."""
    middleware = HookMiddleware(Hooks())
    result = await middleware.on_list_tools(make_context(), list_tools_call_next)
    assert len(result) == 3


async def test_policy_blocks_denied_call_tool() -> None:
    """on_call_tool raises ToolError for a tool the policy disallows."""
    policy = AccessPolicy()
    policy.rebuild([make_server_record("math", deny=["mul"])], [])

    middleware = HookMiddleware(Hooks(), policy=policy)
    with pytest.raises(ToolError):
        await middleware.on_call_tool(make_context(name="math_mul"), call_next)


async def test_policy_allows_permitted_call_tool() -> None:
    """on_call_tool proceeds normally for an allowed tool."""
    policy = AccessPolicy()
    policy.rebuild([make_server_record("math", deny=["mul"])], [])

    middleware = HookMiddleware(Hooks(), policy=policy)
    result = await middleware.on_call_tool(make_context(name="math_add"), call_next)
    assert result == "called"


async def test_no_policy_call_tool_unchanged() -> None:
    """Without a policy, on_call_tool passes through every tool."""
    middleware = HookMiddleware(Hooks())
    result = await middleware.on_call_tool(make_context(name="math_mul"), call_next)
    assert result == "called"
