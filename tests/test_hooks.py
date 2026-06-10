"""Tests for the hook middleware dispatch, focused on the confirmation flow:
a ``pre_tool_call`` returning ``REQUIRE_CONFIRMATION`` must trigger the confirmation
hooks, and a rejection (or absence of any handler) must deny the call."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from fast_gateway.access import AccessPolicy
from fast_gateway.hooks import (
    ConfirmationContext,
    HookMiddleware,
    Hooks,
    ToolCallResult,
    ToolDecision,
)
from fast_gateway.models import ServerRecord


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
# tool_error seam
# ---------------------------------------------------------------------------


def make_error_recorder() -> tuple[list[tuple[str, Exception]], Any]:
    seen: list[tuple[str, Exception]] = []

    async def record(context: Any, error: Exception) -> None:
        seen.append((context.message.name, error))

    return seen, record


async def test_tool_error_fires_on_upstream_failure() -> None:
    seen, record = make_error_recorder()

    async def failing_call_next(context: Any) -> str:
        raise RuntimeError("upstream exploded")

    middleware = HookMiddleware(Hooks(tool_error=[record]))
    with pytest.raises(RuntimeError, match="upstream exploded"):
        await middleware.on_call_tool(make_context(), failing_call_next)

    assert len(seen) == 1
    assert seen[0][0] == "deploy"
    assert isinstance(seen[0][1], RuntimeError)


async def test_tool_error_fires_on_deny() -> None:
    seen, record = make_error_recorder()

    async def deny(context: Any) -> ToolCallResult:
        return ToolCallResult(decision=ToolDecision.DENY, reason="nope")

    middleware = HookMiddleware(Hooks(pre_tool_call=[deny], tool_error=[record]))
    with pytest.raises(ToolError):
        await middleware.on_call_tool(make_context(), call_next)

    assert len(seen) == 1
    assert isinstance(seen[0][1], ToolError)


async def test_tool_error_fires_on_rejected_confirmation() -> None:
    seen, record = make_error_recorder()

    async def reject(ctx: ConfirmationContext) -> bool:
        return False

    middleware = HookMiddleware(
        Hooks(pre_tool_call=[require_confirmation], confirmation=[reject], tool_error=[record])
    )
    with pytest.raises(ToolError):
        await middleware.on_call_tool(make_context(), call_next)

    assert len(seen) == 1


async def test_tool_error_fires_on_policy_block() -> None:
    seen, record = make_error_recorder()
    policy = AccessPolicy()
    policy.rebuild([make_server_record("svc", deny=["*"])], [])

    middleware = HookMiddleware(Hooks(tool_error=[record]), policy=policy)
    with pytest.raises(ToolError, match="not permitted"):
        await middleware.on_call_tool(make_context("svc_anything"), call_next)

    assert len(seen) == 1


async def test_tool_error_not_fired_on_success() -> None:
    seen, record = make_error_recorder()
    middleware = HookMiddleware(Hooks(tool_error=[record]))
    result = await middleware.on_call_tool(make_context(), call_next)

    assert result == "called"
    assert seen == []


async def test_failing_tool_error_hook_never_masks_the_error() -> None:
    async def bad_hook(context: Any, error: Exception) -> None:
        raise ValueError("hook bug")

    async def failing_call_next(context: Any) -> str:
        raise RuntimeError("original")

    middleware = HookMiddleware(Hooks(tool_error=[bad_hook]))
    with pytest.raises(RuntimeError, match="original"):
        await middleware.on_call_tool(make_context(), failing_call_next)


async def test_tool_error_hook_cannot_swallow_the_error() -> None:
    async def swallow(context: Any, error: Exception) -> None:
        return None

    async def failing_call_next(context: Any) -> str:
        raise RuntimeError("still raised")

    middleware = HookMiddleware(Hooks(tool_error=[swallow]))
    with pytest.raises(RuntimeError, match="still raised"):
        await middleware.on_call_tool(make_context(), failing_call_next)


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


# ---------------------------------------------------------------------------
# connect_error seam (via catalog introspection)
# ---------------------------------------------------------------------------


async def test_connect_error_fires_per_failed_server() -> None:
    from unittest.mock import AsyncMock, patch

    from fast_gateway.catalog import collect_catalog

    seen: list[tuple[str, Exception]] = []

    async def record(server: ServerRecord, error: Exception) -> None:
        seen.append((server.name, error))

    servers = [make_server_record("broken"), make_server_record("healthy")]

    async def introspect(server: ServerRecord, hooks: Hooks) -> list[Any]:
        if server.name == "broken":
            raise RuntimeError("connection refused")
        return []

    with patch("fast_gateway.catalog._introspect_server", new=AsyncMock(side_effect=introspect)):
        _, failed = await collect_catalog(servers, Hooks(connect_error=[record]))

    assert len(seen) == 1
    assert seen[0][0] == "broken"
    assert isinstance(seen[0][1], RuntimeError)
    assert failed == {"broken"}


async def test_failing_connect_error_hook_does_not_break_collection() -> None:
    from unittest.mock import AsyncMock, patch

    from fast_gateway.catalog import collect_catalog

    async def bad_hook(server: ServerRecord, error: Exception) -> None:
        raise ValueError("hook bug")

    with patch(
        "fast_gateway.catalog._introspect_server",
        new=AsyncMock(side_effect=RuntimeError("down")),
    ):
        catalog, failed = await collect_catalog(
            [make_server_record("svc")], Hooks(connect_error=[bad_hook])
        )

    assert catalog == []
    assert failed == {"svc"}
