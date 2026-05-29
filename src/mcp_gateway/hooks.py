"""Hooks: the gateway's single extension mechanism.

Everything bespoke — auth, policy, human-in-the-loop, redaction, audit, cost
limits — is a plain async function passed at ``create_gateway`` time. There is no
plugin manager and no auth subsystem. Hooks are grouped in :class:`Hooks` and bound
to the correct layer:

- ``pre_mcp_connect`` runs in the proxy client factory (see ``connect.py``).
- ``pre_list_tools`` / ``pre_tool_call`` / ``post_tool_call`` run in
  :class:`HookMiddleware`, a thin FastMCP middleware dispatcher.

Hooks chain in registration order.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import BaseModel

from mcp_gateway.models import ServerRecord


class ConnectContext(BaseModel):
    """Passed to ``pre_mcp_connect`` hooks before opening an upstream session."""

    server: ServerRecord


class ConnectSettings(BaseModel):
    """What a ``pre_mcp_connect`` hook may return to shape the upstream client."""

    headers: dict[str, str] = {}
    timeout_seconds: float | None = None


class ToolDecision(StrEnum):
    """Outcome of a ``pre_tool_call`` hook."""

    CONTINUE = "continue"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class ToolCallResult(BaseModel):
    """What a ``pre_tool_call`` hook returns: continue, mutate args, or block."""

    decision: ToolDecision = ToolDecision.CONTINUE
    arguments: dict[str, Any] | None = None
    reason: str | None = None


# Hook function signatures. All hooks are async.
ConnectHook = Callable[[ConnectContext], Awaitable[ConnectSettings | None]]
ListToolsHook = Callable[[MiddlewareContext[Any]], Awaitable[None]]
PreToolCallHook = Callable[[MiddlewareContext[Any]], Awaitable[ToolCallResult | None]]
PostToolCallHook = Callable[[MiddlewareContext[Any], Any], Awaitable[Any]]


@dataclass
class Hooks:
    """Container of hook functions, passed at ``create_gateway``."""

    pre_mcp_connect: list[ConnectHook] = field(default_factory=list)
    pre_list_tools: list[ListToolsHook] = field(default_factory=list)
    pre_tool_call: list[PreToolCallHook] = field(default_factory=list)
    post_tool_call: list[PostToolCallHook] = field(default_factory=list)


class HookMiddleware(Middleware):
    """Dispatches list/tool hooks around upstream calls.

    NOTE: scaffolding stub — currently a transparent pass-through that records the
    hooks. The deny / require-confirmation / redaction semantics land in Milestone 2.
    """

    def __init__(self, hooks: Hooks) -> None:
        self.hooks = hooks

    async def on_list_tools(
        self, context: MiddlewareContext[Any], call_next: Callable[..., Any]
    ) -> Any:
        # TODO(Milestone 2): run pre_list_tools to filter/transform the catalog.
        return await call_next(context)

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Callable[..., Any]
    ) -> Any:
        # TODO(Milestone 2): run pre_tool_call (deny/mutate/confirm), then
        # post_tool_call on the result for redaction/audit.
        return await call_next(context)
