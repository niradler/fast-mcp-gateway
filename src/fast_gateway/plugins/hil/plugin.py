"""HumanApprovalPlugin: blocks tool calls until a human (or bot) approves.

The decision surface is dual: the original browser HTML pages, plus a JSON API
under ``/pending`` (list / detail / approve / deny) so Slack bots, custom UIs, and
CI can decide programmatically. How operators get told about a pending approval is
pluggable via the ``notifier`` callable; opening the local browser is the default.
"""

from __future__ import annotations

import logging
import webbrowser
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from fast_gateway.config import HilConfig
from fast_gateway.hooks import ConfirmationContext, Hooks
from fast_gateway.plugins import GatewayContext, PluginContributions
from fast_gateway.plugins.hil.pending import ApprovalDecision, PendingApproval, PendingRegistry
from fast_gateway.plugins.hil.views import render_detail, render_list, render_result

_logger = logging.getLogger("fast_gateway.plugins.hil")

ApprovalNotifier = Callable[[PendingApproval, str], Awaitable[None]]
"""Async callable told about each new approval: ``(approval, decision_url)``.

Inject one into :class:`HumanApprovalPlugin` to route approvals to Slack, a
webhook, email, or anything else; a failing notifier is logged and the approval
keeps waiting for a decision via the admin API or browser page.
"""


class HumanApprovalPlugin:
    """Gateway plugin that requires human sign-off on flagged tool calls."""

    name = "hil"

    def __init__(
        self,
        settings: HilConfig | None = None,
        *,
        notifier: ApprovalNotifier | None = None,
    ) -> None:
        self._settings = settings or HilConfig()
        self._registry = PendingRegistry()
        self._notifier = notifier or self._open_browser

    def contributions(self, context: GatewayContext) -> PluginContributions:
        return PluginContributions(
            hooks=Hooks(confirmation=[self._approve]),
            admin_router=self._build_router(),
        )

    async def _approve(self, ctx: ConfirmationContext) -> bool:
        """Create a pending approval, notify, then wait for a decision."""
        settings = self._settings
        approval = self._registry.create(ctx.tool_name, ctx.arguments, ctx.reason)
        url = f"{settings.approval_base_url.rstrip('/')}/{approval.id}"
        _logger.info("HIL approval required - open %s to decide", url)
        try:
            await self._notifier(approval, url)
        except Exception:
            _logger.warning(
                "HIL notifier failed for approval %s (%s); still awaiting a decision",
                approval.id,
                url,
                exc_info=True,
            )
        return await self._registry.wait(approval.id, wait_timeout=settings.timeout_seconds)

    async def _open_browser(self, approval: PendingApproval, url: str) -> None:
        """Default notifier: open the decision page locally when configured to."""
        if self._settings.auto_open_browser:
            webbrowser.open(url)

    def _build_router(self) -> APIRouter:
        """Build the admin sub-router (mounted by the gateway under /admin/hil).

        JSON routes are declared before the HTML ``/{approval_id}`` catch-all so
        ``/pending`` resolves to the API and not to an approval id.
        """
        router = APIRouter()
        registry = self._registry

        def _decide(approval_id: str, approved: bool) -> ApprovalDecision:
            approval = registry.get(approval_id)
            if approval is None or not registry.resolve(approval_id, approved):
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"No pending approval with id {approval_id!r} (already decided or expired).",
                )
            return ApprovalDecision(id=approval.id, tool_name=approval.tool_name, approved=approved)

        @router.get("/pending", response_model=list[PendingApproval])
        async def list_pending() -> list[PendingApproval]:
            return registry.list_pending()

        @router.get("/pending/{approval_id}", response_model=PendingApproval)
        async def pending_detail(approval_id: str) -> PendingApproval:
            approval = registry.get(approval_id)
            if approval is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"No pending approval with id {approval_id!r} (already decided or expired).",
                )
            return approval

        @router.post("/pending/{approval_id}/approve", response_model=ApprovalDecision)
        async def approve_json(approval_id: str) -> ApprovalDecision:
            return _decide(approval_id, True)

        @router.post("/pending/{approval_id}/deny", response_model=ApprovalDecision)
        async def deny_json(approval_id: str) -> ApprovalDecision:
            return _decide(approval_id, False)

        @router.get("")
        @router.get("/")
        async def list_approvals() -> HTMLResponse:
            return HTMLResponse(render_list(registry.list_pending()))

        @router.get("/{approval_id}")
        async def detail(approval_id: str) -> HTMLResponse:
            approval = registry.get(approval_id)
            if approval is None:
                return HTMLResponse("Already decided or expired.", status_code=404)
            return HTMLResponse(render_detail(approval))

        @router.post("/{approval_id}/approve")
        async def approve(approval_id: str) -> HTMLResponse:
            approval = registry.get(approval_id)
            tool_name = approval.tool_name if approval is not None else approval_id
            registry.resolve(approval_id, True)
            return HTMLResponse(render_result(True, tool_name))

        @router.post("/{approval_id}/deny")
        async def deny(approval_id: str) -> HTMLResponse:
            approval = registry.get(approval_id)
            tool_name = approval.tool_name if approval is not None else approval_id
            registry.resolve(approval_id, False)
            return HTMLResponse(render_result(False, tool_name))

        return router

    async def teardown(self) -> None:
        self._registry.cancel_all()
