"""In-flight approval registry: creates, tracks, and resolves pending HIL requests."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from pydantic import BaseModel

_logger = logging.getLogger("fast_gateway.hil")


class PendingApproval(BaseModel):
    """Snapshot of a tool call awaiting human approval."""

    id: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str | None


class PendingRegistry:
    """Tracks in-flight approval futures keyed by UUID hex id."""

    def __init__(self) -> None:
        self._approvals: dict[str, PendingApproval] = {}
        self._futures: dict[str, asyncio.Future[bool]] = {}

    def create(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str | None,
    ) -> PendingApproval:
        """Register a new approval request and return its record."""
        approval_id = uuid.uuid4().hex
        approval = PendingApproval(
            id=approval_id,
            tool_name=tool_name,
            arguments=arguments,
            reason=reason,
        )
        loop = asyncio.get_running_loop()
        self._futures[approval_id] = loop.create_future()
        self._approvals[approval_id] = approval
        return approval

    async def wait(self, approval_id: str, wait_timeout: float) -> bool:
        """Await the decision; returns False (deny) on timeout or unknown id."""
        fut = self._futures.get(approval_id)
        if fut is None:
            return False
        try:
            return await asyncio.wait_for(asyncio.shield(fut), wait_timeout)
        except TimeoutError:
            _logger.warning("HIL approval %s timed out after %s seconds", approval_id, wait_timeout)
            return False
        finally:
            self._approvals.pop(approval_id, None)
            self._futures.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """Set the decision on a pending future; returns False if unknown or already done."""
        fut = self._futures.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def list_pending(self) -> list[PendingApproval]:
        """Return all approvals that are still awaiting a decision."""
        return list(self._approvals.values())

    def cancel_all(self) -> None:
        """Deny every outstanding approval — called on gateway teardown."""
        for approval_id in list(self._futures.keys()):
            self.resolve(approval_id, False)
