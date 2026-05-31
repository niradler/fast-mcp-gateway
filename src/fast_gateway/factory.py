"""Assemble a ready-to-serve FastAPI app from :class:`GatewayConfig`."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, params

from fast_gateway.app import create_gateway
from fast_gateway.config import GatewayConfig
from fast_gateway.hil import HumanApprovalPlugin
from fast_gateway.hooks import Hooks
from fast_gateway.reference import audit_hook, confirm_hook, deny_hook
from fast_gateway.store import SqliteStore


def _require_admin_token(expected: str) -> Callable[..., Awaitable[None]]:
    """Return a FastAPI dependency that demands ``Authorization: Bearer <expected>``."""

    async def _check(authorization: Annotated[str, Header()] = "") -> None:
        if authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Admin authentication required.")

    return _check


def build_app(config: GatewayConfig) -> FastAPI:
    """Build a ready-to-serve FastAPI app from *config*.

    When ``config.oauth_token_dir`` is set, ``FAST_GATEWAY_OAUTH_DIR`` is written
    into the process environment before the gateway is constructed so that
    ``connect.default_oauth_token_dir`` resolves to the configured path for the
    lifetime of this process.
    """
    if config.oauth_token_dir is not None:
        os.environ["FAST_GATEWAY_OAUTH_DIR"] = config.oauth_token_dir

    store = SqliteStore(config.db)

    pre_tool_call = [deny_hook(config.policy.deny), confirm_hook(config.policy.confirm)]
    post_tool_call = [audit_hook()] if config.policy.audit else []
    hooks = Hooks(pre_tool_call=pre_tool_call, post_tool_call=post_tool_call)

    plugins = [HumanApprovalPlugin(config.hil)] if config.hil.enabled else []

    gateway = create_gateway(store, hooks, plugins=plugins, name=config.name)

    app = FastAPI(title=config.name, lifespan=gateway.lifespan)

    admin_dependencies: list[params.Depends] | None = None
    if config.admin_token is not None:
        dep = _require_admin_token(config.admin_token)
        admin_dependencies = [Depends(dep)]

    gateway.install(
        app,
        mcp_path=config.mcp_path,
        admin_prefix=config.admin_prefix,
        admin_dependencies=admin_dependencies,
    )
    return app
