"""End-to-end gateway over real upstreams, exercising every hook seam.

This is the example the live validation harness drives. It wires all five hook seams
and seeds a registry mixing a local ``echo`` upstream (ground truth for what the gateway
injects/transforms) with public internet MCP servers (real proxying/namespacing/search),
plus a read-only group and admin auth.

Run the upstream and this app in two shells::

    uv run uvicorn examples.echo_upstream:app --port 9100
    ADMIN_TOKEN=secret uv run uvicorn examples.live_gateway:app --port 8000

Then:

- Admin + OpenAPI docs:  http://127.0.0.1:8000/docs   (Bearer $ADMIN_TOKEN on /admin)
- Full MCP endpoint:     http://127.0.0.1:8000/mcp/
- Group-scoped endpoint:  http://127.0.0.1:8000/mcp/g/readonly/

Hook seams demonstrated:

- ``pre_mcp_connect`` — inject a bearer token per upstream (echo reflects it via ``whoami``).
- ``pre_list_tools`` — hide ``delete_*`` tools from discovery (still governed on call).
- ``pre_tool_call``  — mutate ``echo`` arguments and flag destructive tools for approval.
- ``confirmation``   — approve only tools named in ``HIL_AUTO_APPROVE`` (fail-safe deny).
- ``post_tool_call`` — audit every call and redact credential-shaped strings.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any

import mcp.types as mt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import Tool

from fast_gateway import (
    ConfirmationContext,
    ConnectContext,
    ConnectSettings,
    GroupCreate,
    Hooks,
    ServerCreate,
    SqliteStore,
    Store,
    ToolCallResult,
    ToolDecision,
    create_gateway,
)

logger = logging.getLogger("examples.live_gateway")

ECHO_URL = os.environ.get("ECHO_URL", "http://127.0.0.1:9100/mcp/")
ECHO_TOKEN = "echo-bearer-12345"
_SECRET_PATTERN = re.compile(r"ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")
_DESTRUCTIVE_HINTS = ("delete", "drop", "remove", "purge")

PUBLIC_SERVERS: list[tuple[str, str]] = [
    ("agno", "https://docs.agno.com/mcp"),
    ("deepwiki", "https://mcp.deepwiki.com/mcp"),
    ("huggingface", "https://huggingface.co/mcp"),
    ("context7", "https://mcp.context7.com/mcp"),
    ("gitmcp", "https://gitmcp.io/jlowin/fastmcp"),
]


async def inject_auth(context: ConnectContext) -> ConnectSettings | None:
    """``pre_mcp_connect``: attach a bearer token per upstream.

    The local ``echo`` upstream always gets a known token (so ``whoami`` can prove the
    header arrived); public upstreams read ``<NAME>_TOKEN`` from the environment.
    """
    if context.server.name == "echo":
        return ConnectSettings(headers={"Authorization": f"Bearer {ECHO_TOKEN}"})
    token = os.environ.get(f"{context.server.name.upper()}_TOKEN")
    if token:
        return ConnectSettings(headers={"Authorization": f"Bearer {token}"})
    return None


async def hide_destructive_from_listing(
    context: MiddlewareContext[Any], tools: list[Tool]
) -> list[Tool]:
    """``pre_list_tools``: drop ``*_delete*`` tools from discovery (calls still governed)."""
    return [t for t in tools if "delete" not in t.name.lower()]


async def shape_call(ctx: MiddlewareContext[mt.CallToolRequestParams]) -> ToolCallResult | None:
    """``pre_tool_call``: mutate ``echo`` args, and gate destructive tools on approval."""
    name = ctx.message.name.lower()
    if ctx.message.name == "echo_echo":
        args = dict(ctx.message.arguments or {})
        args["note"] = "via-gateway"
        return ToolCallResult(decision=ToolDecision.CONTINUE, arguments=args)
    if any(hint in name for hint in _DESTRUCTIVE_HINTS):
        return ToolCallResult(
            decision=ToolDecision.REQUIRE_CONFIRMATION,
            reason=f"{ctx.message.name} is potentially destructive and needs approval.",
        )
    return None


async def approve(ctx: ConfirmationContext) -> bool:
    """``confirmation``: approve only tools named in ``HIL_AUTO_APPROVE`` (default deny)."""
    allowlist = {n for n in os.environ.get("HIL_AUTO_APPROVE", "").split(",") if n}
    approved = ctx.tool_name in allowlist
    logger.info("confirmation for %s: %s", ctx.tool_name, "approved" if approved else "denied")
    return approved


async def audit_and_redact(ctx: MiddlewareContext[mt.CallToolRequestParams], response: Any) -> Any:
    """``post_tool_call``: log the completed call, then redact credential-shaped strings."""
    logger.info("tool call completed: %s", ctx.message.name)
    content = getattr(response, "content", None)
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                block.text = _SECRET_PATTERN.sub("[REDACTED]", text)
    return response


async def seed_registry(store: Store) -> None:
    """Register the echo upstream + public servers and a read-only group, once."""
    if await store.list_servers():
        return
    await store.create_server(ServerCreate(name="echo", url=ECHO_URL))
    public_ids = []
    for name, url in PUBLIC_SERVERS:
        deny = ["*whoami*"] if name == "huggingface" else []
        record = await store.create_server(ServerCreate(name=name, url=url, deny=deny))
        public_ids.append(record.id)
    await store.create_group(
        GroupCreate(
            name="readonly",
            member_server_ids=public_ids,
            deny=["*write*", "*delete*", "*create*", "*update*"],
        )
    )


async def require_admin(authorization: Annotated[str, Header()] = "") -> None:
    """FastAPI dependency: gate every admin route behind a static bearer token.

    No-ops when ``ADMIN_TOKEN`` is unset so the example is trivially runnable; set the
    variable to require ``Authorization: Bearer $ADMIN_TOKEN`` on every ``/admin`` route.
    """
    expected = os.environ.get("ADMIN_TOKEN")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Admin authentication required.")


store = SqliteStore(os.environ.get("GATEWAY_DB", "live_gateway.db"))
gateway = create_gateway(
    store=store,
    list_mode="all",
    hooks=Hooks(
        pre_mcp_connect=[inject_auth],
        pre_list_tools=[hide_destructive_from_listing],
        pre_tool_call=[shape_call],
        confirmation=[approve],
        post_tool_call=[audit_and_redact],
    ),
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the gateway lifespan, then seed the registry and reload once on first boot."""
    async with gateway.lifespan(app):
        await seed_registry(store)
        await gateway.reload()
        yield


app = FastAPI(title="Live MCP Gateway", lifespan=lifespan)
gateway.install(app, admin_dependencies=[Depends(require_admin)])
