"""Minimal HTML views for the HIL approval UI — no template engine, stdlib only."""

from __future__ import annotations

import html
import json

from fast_gateway.plugins.hil.pending import PendingApproval

_STYLE = (
    "<style>"
    "body{font-family:sans-serif;max-width:700px;margin:2rem auto;padding:0 1rem}"
    "pre{background:#f4f4f4;padding:1rem;border-radius:4px;overflow:auto}"
    "form{display:inline}"
    "button{padding:.5rem 1.2rem;margin:.25rem;cursor:pointer;border:none;border-radius:4px}"
    ".approve{background:#2d8a4e;color:#fff}"
    ".deny{background:#c0392b;color:#fff}"
    "a{color:#2563eb}"
    "</style>"
)


def render_list(pending: list[PendingApproval]) -> str:
    """Return an HTML page listing pending approvals with links to each detail page."""
    if not pending:
        body = "<p>No pending approvals.</p>"
    else:
        items = "".join(
            f'<li><a href="{html.escape(p.id)}">{html.escape(p.tool_name)}</a></li>'
            for p in pending
        )
        body = f"<ul>{items}</ul>"
    return f"<html><head>{_STYLE}</head><body><h1>Pending Approvals</h1>{body}</body></html>"


def render_detail(approval: PendingApproval) -> str:
    """Return an HTML page with tool details and Approve / Deny form buttons."""
    safe_name = html.escape(approval.tool_name)
    safe_reason = html.escape(approval.reason or "(none)")
    safe_args = html.escape(json.dumps(approval.arguments, indent=2))
    approve_form = (
        f'<form method="post" action="{html.escape(approval.id)}/approve">'
        '<button class="approve" type="submit">Approve</button></form>'
    )
    deny_form = (
        f'<form method="post" action="{html.escape(approval.id)}/deny">'
        '<button class="deny" type="submit">Deny</button></form>'
    )
    return (
        f"<html><head>{_STYLE}</head><body>"
        f"<h1>Approve tool call</h1>"
        f"<p><strong>Tool:</strong> {safe_name}</p>"
        f"<p><strong>Reason:</strong> {safe_reason}</p>"
        f"<h2>Arguments</h2><pre>{safe_args}</pre>"
        f"{approve_form}{deny_form}"
        f"</body></html>"
    )


def render_result(approved: bool, tool_name: str) -> str:
    """Return a confirmation page after an approval decision was submitted."""
    decision = "Approved" if approved else "Denied"
    safe_name = html.escape(tool_name)
    return (
        f"<html><head>{_STYLE}</head><body>"
        f"<h1>{decision}</h1>"
        f"<p>Tool call <strong>{safe_name}</strong> was {decision.lower()}.</p>"
        f'<p><a href=".">Back to approvals</a></p>'
        f"</body></html>"
    )
