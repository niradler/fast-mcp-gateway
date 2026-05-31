"""Reference hook factories for the gateway's common cross-cutting concerns.

Each factory returns a ready-to-use hook that can be appended to the matching
seam in :class:`fast_gateway.hooks.Hooks` without any further wiring.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from fnmatch import fnmatchcase
from typing import Any

import mcp.types as mt
from fastmcp.server.middleware import MiddlewareContext

from fast_gateway.hooks import PostToolCallHook, PreToolCallHook, ToolCallResult, ToolDecision

logger = logging.getLogger("fast_gateway.reference")


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    """Return True if *name* matches at least one glob pattern."""
    return any(fnmatchcase(name, p) for p in patterns)


def audit_hook() -> PostToolCallHook:
    """Return a post_tool_call hook that logs completed tool calls at INFO."""

    async def _audit(ctx: MiddlewareContext[mt.CallToolRequestParams], response: Any) -> Any:
        logger.info("tool call completed: %s", ctx.message.name)
        return response

    return _audit


def deny_hook(patterns: Sequence[str]) -> PreToolCallHook:
    """Return a pre_tool_call hook that hard-denies tools matching any glob in *patterns*.

    Returns None (continue) when *patterns* is empty or no pattern matches.
    """

    async def _deny(
        ctx: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolCallResult | None:
        name = ctx.message.name
        if patterns and _matches_any(name, patterns):
            return ToolCallResult(
                decision=ToolDecision.DENY,
                reason=f"Tool '{name}' is denied by policy.",
            )
        return None

    return _deny


def confirm_hook(patterns: Sequence[str]) -> PreToolCallHook:
    """Return a pre_tool_call hook that requires confirmation for tools matching any glob.

    Returns None (continue) when *patterns* is empty or no pattern matches.
    """

    async def _confirm(
        ctx: MiddlewareContext[mt.CallToolRequestParams],
    ) -> ToolCallResult | None:
        name = ctx.message.name
        if patterns and _matches_any(name, patterns):
            return ToolCallResult(
                decision=ToolDecision.REQUIRE_CONFIRMATION,
                reason=f"{name} requires approval.",
            )
        return None

    return _confirm
