"""fast_gateway.hil — browser-based human-in-the-loop approval plugin."""

from __future__ import annotations

from fast_gateway.hil.pending import PendingApproval, PendingRegistry
from fast_gateway.hil.plugin import HumanApprovalPlugin

__all__ = ["HumanApprovalPlugin", "PendingApproval", "PendingRegistry"]
