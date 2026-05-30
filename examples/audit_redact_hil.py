"""Audit, redaction, and human-in-the-loop — all as plain hooks.

Three cross-cutting concerns wired onto the tool-call seams, none of them a subsystem:

- **Audit** (``post_tool_call``): log every tool call that completes.
- **Redaction** (``post_tool_call``): strip credential-shaped strings out of responses
  before they reach the caller.
- **Human-in-the-loop** (``pre_tool_call`` + ``confirmation``): a ``pre_tool_call`` hook
  flags destructive tools as ``REQUIRE_CONFIRMATION``; a ``confirmation`` hook then
  grants or denies. If no confirmation hook is registered, the call is denied
  (fail-safe).

Run with::

    HIL_AUTO_APPROVE=issues_delete_item uv run uvicorn examples.audit_redact_hil:app --reload

The confirmation hook here approves only tools named in ``HIL_AUTO_APPROVE`` so the
example stays non-interactive; in production it would call out to Slack, a web UI, or a
ticket and block on a human's answer.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import mcp.types as mt
from fastapi import FastAPI
from fastmcp.server.middleware import MiddlewareContext

from mcp_gateway import (
    ConfirmationContext,
    Hooks,
    SqliteStore,
    ToolCallResult,
    ToolDecision,
    create_gateway,
)

logger = logging.getLogger("examples.audit_redact_hil")

_SECRET_PATTERN = re.compile(r"ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")
_DESTRUCTIVE_HINTS = ("delete", "drop", "remove", "purge")


def _redact(text: str) -> str:
    """Replace credential-shaped substrings with a placeholder."""
    return _SECRET_PATTERN.sub("[REDACTED]", text)


async def audit_call(ctx: MiddlewareContext[mt.CallToolRequestParams], response: Any) -> Any:
    """``post_tool_call``: record the completed call, then pass the response through."""
    logger.info("tool call completed: %s", ctx.message.name)
    return response


async def redact_secrets(ctx: MiddlewareContext[mt.CallToolRequestParams], response: Any) -> Any:
    """``post_tool_call``: redact secrets from string content of the response.

    Handles a plain string and the common MCP content-block shape (an object with a
    ``content`` list of blocks exposing ``.text``); anything else passes through
    untouched. The agentos plugin's ``enable_credential_redaction`` is the
    production-grade version of this.
    """
    if isinstance(response, str):
        return _redact(response)
    content = getattr(response, "content", None)
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                block.text = _redact(text)
        logger.info("scanned %s response for secrets", ctx.message.name)
    return response


async def flag_destructive(
    ctx: MiddlewareContext[mt.CallToolRequestParams],
) -> ToolCallResult | None:
    """``pre_tool_call``: require confirmation for tools whose name looks destructive."""
    name = ctx.message.name.lower()
    if any(hint in name for hint in _DESTRUCTIVE_HINTS):
        return ToolCallResult(
            decision=ToolDecision.REQUIRE_CONFIRMATION,
            reason=f"{ctx.message.name} is potentially destructive and needs approval.",
        )
    return None


async def approve(ctx: ConfirmationContext) -> bool:
    """``confirmation``: the human-in-the-loop gate (here, an env allowlist).

    Returns True to let the call proceed, False to deny it. Default is deny, so a tool
    not explicitly approved is blocked.
    """
    allowlist = {name for name in os.environ.get("HIL_AUTO_APPROVE", "").split(",") if name}
    approved = ctx.tool_name in allowlist
    logger.info("confirmation for %s: %s", ctx.tool_name, "approved" if approved else "denied")
    return approved


gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    hooks=Hooks(
        pre_tool_call=[flag_destructive],
        confirmation=[approve],
        post_tool_call=[audit_call, redact_secrets],
    ),
)

app = FastAPI(title="Audit / Redact / HIL Gateway", lifespan=gateway.lifespan)
gateway.install(app)
