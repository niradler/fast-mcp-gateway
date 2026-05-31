"""HumanApprovalPlugin: blocks tool calls until a human approves via a browser page."""

from __future__ import annotations

import logging
import webbrowser

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from fast_gateway.config import HilConfig
from fast_gateway.hil.pending import PendingRegistry
from fast_gateway.hil.views import render_detail, render_list, render_result
from fast_gateway.hooks import ConfirmationContext, Hooks
from fast_gateway.plugins import GatewayContext, PluginContributions

_logger = logging.getLogger("fast_gateway.hil")


class HumanApprovalPlugin:
    """Gateway plugin that requires human sign-off on flagged tool calls."""

    name = "hil"

    def __init__(self, settings: HilConfig | None = None) -> None:
        self._settings = settings or HilConfig()
        self._registry = PendingRegistry()

    def contributions(self, context: GatewayContext) -> PluginContributions:
        return PluginContributions(
            hooks=Hooks(confirmation=[self._approve]),
            admin_router=self._build_router(),
        )

    async def _approve(self, ctx: ConfirmationContext) -> bool:
        """Create a pending approval, optionally open the browser, then wait for a decision."""
        settings = self._settings
        approval = self._registry.create(ctx.tool_name, ctx.arguments, ctx.reason)
        url = f"{settings.approval_base_url.rstrip('/')}/{approval.id}"
        _logger.info("HIL approval required - open %s to decide", url)
        if settings.auto_open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                _logger.warning(
                    "Could not open browser for HIL approval URL %s", url, exc_info=True
                )
        return await self._registry.wait(approval.id, wait_timeout=settings.timeout_seconds)

    def _build_router(self) -> APIRouter:
        """Build the admin sub-router (mounted by the gateway under /admin/hil)."""
        router = APIRouter()
        registry = self._registry

        @router.get("")
        @router.get("/")
        async def list_approvals() -> HTMLResponse:
            return HTMLResponse(render_list(registry.list_pending()))

        @router.get("/{approval_id}")
        async def detail(approval_id: str) -> HTMLResponse:
            pending = {a.id: a for a in registry.list_pending()}
            if approval_id not in pending:
                return HTMLResponse("Already decided or expired.", status_code=404)
            return HTMLResponse(render_detail(pending[approval_id]))

        @router.post("/{approval_id}/approve")
        async def approve(approval_id: str) -> HTMLResponse:
            pending = {a.id: a for a in registry.list_pending()}
            tool_name = pending[approval_id].tool_name if approval_id in pending else approval_id
            registry.resolve(approval_id, True)
            return HTMLResponse(render_result(True, tool_name))

        @router.post("/{approval_id}/deny")
        async def deny(approval_id: str) -> HTMLResponse:
            pending = {a.id: a for a in registry.list_pending()}
            tool_name = pending[approval_id].tool_name if approval_id in pending else approval_id
            registry.resolve(approval_id, False)
            return HTMLResponse(render_result(False, tool_name))

        return router

    async def teardown(self) -> None:
        self._registry.cancel_all()
