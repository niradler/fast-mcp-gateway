"""AgtAgentOsPlugin: the gateway's Microsoft agent-governance-toolkit (agent-os) plugin.

This is the gateway's first integration plugin and the home for agent-os capabilities.
Today it contributes a single ``pre_tool_call`` hook that evaluates agent-os policy for
every tool call and denies the call when the policy rejects it. The active group
(``mcp_gateway.access.current_group``, set per request by the group-scoped MCP mount) is
passed to the engine as ``principal`` and ``group`` so policies enforce per group — no
registry lookups are needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_gateway.access import current_group
from mcp_gateway.hooks import Hooks, PreToolCallHook, ToolCallResult, ToolDecision
from mcp_gateway.integrations.agt.policy import build_evaluator
from mcp_gateway.integrations.agt.settings import AgtSettings
from mcp_gateway.plugins import PluginContributions

if TYPE_CHECKING:
    from agent_os.policies import AsyncPolicyEvaluator

    from mcp_gateway.plugins import GatewayContext

_logger = logging.getLogger("mcp_gateway.integrations.agt")


class AgtAgentOsPlugin:
    """Evaluate agent-os policy on every tool call; deny calls the policy rejects."""

    name = "agt"

    def __init__(self, settings: AgtSettings) -> None:
        self._settings = settings
        self._evaluator: AsyncPolicyEvaluator | None = None

    async def setup(self) -> None:
        """Load and validate policies (raises on a malformed policy document)."""
        if self._evaluator is None:
            self._evaluator = build_evaluator(self._settings)

    async def teardown(self) -> None:
        self._evaluator = None

    def contributions(self, context: GatewayContext) -> PluginContributions:
        if self._evaluator is None:
            self._evaluator = build_evaluator(self._settings)
        return PluginContributions(hooks=Hooks(pre_tool_call=[self._make_enforce_hook()]))

    def _make_enforce_hook(self) -> PreToolCallHook:
        settings = self._settings

        async def enforce_policy(ctx: Any) -> ToolCallResult | None:
            evaluator = self._evaluator
            if evaluator is None:
                evaluator = self._evaluator = build_evaluator(settings)
            group = current_group.get()
            tool_name = ctx.message.name
            eval_context = {
                "action": "tool_call",
                "principal": group or settings.default_principal,
                "resource": tool_name,
                "tool": tool_name,
                "group": group or "",
                "arguments": getattr(ctx.message, "arguments", None) or {},
            }
            try:
                decision = await evaluator.evaluate(eval_context)
            except Exception:
                _logger.warning(
                    "AGT policy evaluation failed for tool %r", tool_name, exc_info=True
                )
                if settings.fail_closed:
                    return ToolCallResult(
                        decision=ToolDecision.DENY, reason="AGT policy evaluation failed"
                    )
                raise
            if not decision.allowed:
                return ToolCallResult(decision=ToolDecision.DENY, reason=decision.reason)
            return None

        return enforce_policy
