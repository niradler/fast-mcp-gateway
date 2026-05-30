"""Hooks: the gateway's single extension mechanism for auth, policy, HIL, audit, etc.

Five seams: ``pre_mcp_connect`` (client factory), ``pre_list_tools``, ``pre_tool_call``,
``confirmation`` (HIL — fail-safe: deny if none registered), and ``post_tool_call``.
Hooks chain in registration order; :class:`Hooks` groups them, :class:`HookMiddleware`
dispatches them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import Tool
from pydantic import BaseModel

from fast_mcp_gateway.access import AccessPolicy, current_group
from fast_mcp_gateway.models import ServerRecord


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


class ConfirmationContext(BaseModel):
    """Passed to ``confirmation`` hooks when a tool call requires approval."""

    tool_name: str
    arguments: dict[str, Any] = {}
    reason: str | None = None


CatalogProvider = Callable[[], Awaitable[Sequence[Tool]]]

ConnectHook = Callable[[ConnectContext], Awaitable[ConnectSettings | None]]
ListToolsHook = Callable[[MiddlewareContext[Any], Sequence[Tool]], Awaitable[Sequence[Tool]]]
PreToolCallHook = Callable[
    [MiddlewareContext[mt.CallToolRequestParams]], Awaitable[ToolCallResult | None]
]
ConfirmationHook = Callable[[ConfirmationContext], Awaitable[bool]]
PostToolCallHook = Callable[[MiddlewareContext[mt.CallToolRequestParams], Any], Awaitable[Any]]


@dataclass
class Hooks:
    """Container of hook functions, passed at ``create_gateway``.

    All hooks are async, and each field is a list run in registration order.
    ``pre_list_tools`` receives the current tool catalog and returns a possibly
    filtered or transformed catalog; a ``confirmation`` hook returns True to approve
    a call or False to reject it.
    """

    pre_mcp_connect: list[ConnectHook] = field(default_factory=list)
    pre_list_tools: list[ListToolsHook] = field(default_factory=list)
    pre_tool_call: list[PreToolCallHook] = field(default_factory=list)
    confirmation: list[ConfirmationHook] = field(default_factory=list)
    post_tool_call: list[PostToolCallHook] = field(default_factory=list)


def merge_hooks(*groups: Hooks) -> Hooks:
    """Combine several :class:`Hooks` into one, concatenating each seam in order.

    Base hooks come first, then each plugin's hooks in registration order, so
    earlier-registered hooks run first within every seam. Input ``Hooks`` are not
    mutated.
    """
    merged = Hooks()
    for group in groups:
        merged.pre_mcp_connect.extend(group.pre_mcp_connect)
        merged.pre_list_tools.extend(group.pre_list_tools)
        merged.pre_tool_call.extend(group.pre_tool_call)
        merged.confirmation.extend(group.confirmation)
        merged.post_tool_call.extend(group.post_tool_call)
    return merged


class HookMiddleware(Middleware):
    """Dispatches list/tool hooks around upstream calls."""

    def __init__(
        self,
        hooks: Hooks,
        policy: AccessPolicy | None = None,
        catalog: CatalogProvider | None = None,
    ) -> None:
        self.hooks = hooks
        self.policy = policy
        self.catalog = catalog

    async def on_list_tools(
        self, context: MiddlewareContext[Any], call_next: Callable[..., Any]
    ) -> Any:
        """Serve the catalog snapshot (no fan-out) or live aggregation, apply the policy
        filter, then thread each ``pre_list_tools`` hook. Policy runs before user hooks
        so namespace splitting sees the original namespaced names.
        """
        if self.catalog is not None:
            tools: Sequence[Tool] = await self.catalog()
        else:
            tools = await call_next(context)
        if self.policy is not None:
            tools = self.policy.filter_tools(tools, group=current_group.get())
        for hook in self.hooks.pre_list_tools:
            tools = await hook(context, tools)
        return tools

    async def on_call_tool(
        self, context: MiddlewareContext[mt.CallToolRequestParams], call_next: Callable[..., Any]
    ) -> Any:
        message = context.message
        if self.policy is not None and not self.policy.allows(message.name, current_group.get()):
            raise ToolError(f"Tool {message.name!r} is not permitted.")
        for hook in self.hooks.pre_tool_call:
            result = await hook(context)
            if result is None:
                continue
            if result.arguments is not None:
                message.arguments = result.arguments
            if result.decision is ToolDecision.DENY:
                raise ToolError(result.reason or f"Tool '{message.name}' denied by policy.")
            if result.decision is ToolDecision.REQUIRE_CONFIRMATION:
                await self._require_confirmation(message, result)

        response = await call_next(context)

        for post_hook in self.hooks.post_tool_call:
            response = await post_hook(context, response)
        return response

    async def _require_confirmation(self, message: Any, result: ToolCallResult) -> None:
        """Run the confirmation hooks; deny the call if any rejects or none exists."""
        if not self.hooks.confirmation:
            raise ToolError(
                f"Tool '{message.name}' requires confirmation but no confirmation "
                f"hook is registered."
            )

        confirmation_context = ConfirmationContext(
            tool_name=message.name,
            arguments=message.arguments or {},
            reason=result.reason,
        )
        for hook in self.hooks.confirmation:
            approved = await hook(confirmation_context)
            if not approved:
                raise ToolError(result.reason or f"Tool '{message.name}' was not confirmed.")
