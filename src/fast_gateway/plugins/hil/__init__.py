"""fast_gateway.plugins.hil — human-in-the-loop approval plugin.

Decisions land via the browser pages or the JSON API under ``/admin/hil/pending``;
notifications are pluggable through :data:`ApprovalNotifier` (browser-open default).
"""

from __future__ import annotations

from fast_gateway.plugins.hil.pending import ApprovalDecision, PendingApproval, PendingRegistry
from fast_gateway.plugins.hil.plugin import ApprovalNotifier, HumanApprovalPlugin

__all__ = [
    "ApprovalDecision",
    "ApprovalNotifier",
    "HumanApprovalPlugin",
    "PendingApproval",
    "PendingRegistry",
]
