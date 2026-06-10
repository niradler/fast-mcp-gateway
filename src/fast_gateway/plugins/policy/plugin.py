"""PolicyPlugin: the reference governance hooks bundled as a single plugin.

Combines glob-based hard deny, glob-based human-confirmation gating, and audit
logging of completed calls — the three cross-cutting policies most gateways
need. Wraps the hook factories in :mod:`fast_gateway.reference`, so embedders
who want finer control can keep composing those directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from fast_gateway.hooks import Hooks, PostToolCallHook, PreToolCallHook
from fast_gateway.plugins import GatewayContext, PluginContributions
from fast_gateway.reference import audit_hook, confirm_hook, deny_hook


class PolicyPlugin:
    """Deny / confirm / audit governance over every tool call through the gateway.

    ``deny`` globs hard-block matching tools; ``confirm`` globs route matching
    calls through the confirmation seam (pair with a confirmation handler such
    as ``HumanApprovalPlugin``); ``audit`` logs every completed call at INFO.
    Patterns match the namespaced tool name (``"<server>_<tool>"``).
    """

    name = "policy"

    def __init__(
        self,
        *,
        deny: Sequence[str] = (),
        confirm: Sequence[str] = (),
        audit: bool = True,
    ) -> None:
        self._deny = list(deny)
        self._confirm = list(confirm)
        self._audit = audit

    def contributions(self, context: GatewayContext) -> PluginContributions:
        pre: list[PreToolCallHook] = []
        if self._deny:
            pre.append(deny_hook(self._deny))
        if self._confirm:
            pre.append(confirm_hook(self._confirm))
        post: list[PostToolCallHook] = [audit_hook()] if self._audit else []
        return PluginContributions(hooks=Hooks(pre_tool_call=pre, post_tool_call=post))
