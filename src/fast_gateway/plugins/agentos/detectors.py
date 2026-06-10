"""agent-os detector/scanner hooks contributed by :class:`AgtAgentOsPlugin`.

``pre_tool_call``: prompt-injection detection and semantic-policy classification.
``post_tool_call``: response threat scan and credential redaction.
``pre_mcp_connect``: egress policy (refuses upstreams outside an allowlist).
Detectors are built once per plugin instance and run inline (synchronous, cheap).
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Sequence
from typing import Any

from agent_os.credential_redactor import CredentialRedactor
from agent_os.egress_policy import EgressPolicy
from agent_os.mcp_response_scanner import MCPResponseScanner
from agent_os.mcp_security import MCPSecurityScanner
from agent_os.mcp_sliding_rate_limiter import MCPSlidingRateLimiter
from agent_os.prompt_injection import DetectionConfig, PromptInjectionDetector
from agent_os.semantic_policy import PolicyDenied, SemanticPolicyEngine
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import Tool

from fast_gateway.access import current_group
from fast_gateway.hooks import (
    ConnectContext,
    ConnectHook,
    ListToolsHook,
    PostToolCallHook,
    PreToolCallHook,
    ToolCallResult,
    ToolDecision,
)
from fast_gateway.plugins.agentos.settings import AgtAgentOsSettings

_logger = logging.getLogger("fast_gateway.plugins.agentos")


def _args_text(arguments: dict[str, Any] | None) -> str:
    return " ".join(str(v) for v in (arguments or {}).values())


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content:
        joined = "".join(getattr(block, "text", "") or "" for block in content)
        if joined:
            return joined
    return response if isinstance(response, str) else str(response)


def make_prompt_injection_hook(settings: AgtAgentOsSettings) -> PreToolCallHook:
    """Deny a tool call when agent-os flags prompt injection in its arguments."""
    detector = PromptInjectionDetector(settings.injection_config or DetectionConfig())

    async def prompt_injection(ctx: Any) -> ToolCallResult | None:
        text = _args_text(getattr(ctx.message, "arguments", None))
        if not text:
            return None
        result = detector.detect(text, source=ctx.message.name)
        if result.is_injection:
            return ToolCallResult(
                decision=ToolDecision.DENY,
                reason=result.explanation or "Prompt injection detected in tool arguments.",
            )
        return None

    return prompt_injection


def make_semantic_policy_hook(settings: AgtAgentOsSettings) -> PreToolCallHook:
    """Deny a tool call whose classified intent is in the deny list.

    ``SemanticPolicyEngine.check`` raises ``PolicyDenied`` when the classified intent is
    in ``deny``; that is translated into a gateway DENY. The engine's built-in signals
    are only samples — pass a tuned ``semantic_config`` (agent-os ``SemanticPolicyConfig``,
    or one from ``load_semantic_policy_config``) for real coverage.
    """
    engine = SemanticPolicyEngine(
        deny=settings.semantic_deny or None,
        confidence_threshold=settings.semantic_confidence_threshold,
        config=settings.semantic_config,
    )

    async def semantic_policy(ctx: Any) -> ToolCallResult | None:
        try:
            engine.check(ctx.message.name, getattr(ctx.message, "arguments", None) or {})
        except PolicyDenied as denied:
            return ToolCallResult(decision=ToolDecision.DENY, reason=str(denied))
        return None

    return semantic_policy


def make_response_scan_hook(settings: AgtAgentOsSettings) -> PostToolCallHook:
    """Block a tool response that agent-os flags as unsafe (credential/PII/threat)."""
    scanner = MCPResponseScanner()

    async def response_scan(ctx: Any, response: Any) -> Any:
        result = scanner.scan_response(_response_text(response), ctx.message.name)
        if not result.is_safe:
            reasons = "; ".join(threat.description for threat in result.threats) or "unsafe content"
            raise ToolError(f"Tool '{ctx.message.name}' response blocked: {reasons}")
        return response

    return response_scan


def make_credential_redaction_hook(settings: AgtAgentOsSettings) -> PostToolCallHook:
    """Redact credentials/PII out of a tool response (text blocks + structured content)."""

    async def credential_redaction(ctx: Any, response: Any) -> Any:
        if isinstance(response, str):
            return CredentialRedactor.redact(response)
        content = getattr(response, "content", None)
        if content:
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    block.text = CredentialRedactor.redact(text)
        if getattr(response, "structured_content", None) is not None:
            response.structured_content = CredentialRedactor.redact_data_structure(
                response.structured_content
            )
        return response

    return credential_redaction


def make_mcp_security_scan_hook(settings: AgtAgentOsSettings) -> ListToolsHook:
    """Filter or log tool definitions flagged as poisoned by the agent-os security scanner.

    When ``fail_closed`` is True, flagged tools are dropped from the returned catalog
    and a warning is logged.  When False, the warning is logged but the tool is kept.
    Scan errors for a single tool are swallowed; the tool is treated as clean so a
    transient failure does not blank the entire catalog.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scanner = MCPSecurityScanner()

    async def mcp_security_scan(ctx: Any, tools: Sequence[Tool]) -> Sequence[Tool]:
        clean: list[Tool] = []
        for tool in tools:
            try:
                schema = tool.parameters if tool.parameters is not None else None
                threats = scanner.scan_tool(
                    tool.name,
                    tool.description or "",
                    schema,
                    server_name="gateway",
                )
            except Exception:
                _logger.warning("MCP security scan error for tool %r; treating as clean", tool.name)
                clean.append(tool)
                continue
            if threats:
                names = ", ".join(t.threat_type.value for t in threats)
                if settings.fail_closed:
                    _logger.warning(
                        "MCP security scan: dropping tool %r -- threats: %s", tool.name, names
                    )
                    continue
                _logger.warning(
                    "MCP security scan: tool %r flagged (kept) -- threats: %s", tool.name, names
                )
            clean.append(tool)
        return clean

    return mcp_security_scan


def make_rate_limiting_hook(settings: AgtAgentOsSettings) -> PreToolCallHook:
    """Deny tool calls that exceed the per-group sliding-window budget.

    The rate-limit key is the active group from ``current_group``; when no group is
    set the plugin's ``default_principal`` is used so all ungrouped callers share
    one budget.
    """
    limiter = MCPSlidingRateLimiter(
        max_calls_per_window=settings.rate_limit_max_calls,
        window_size=settings.rate_limit_window_seconds,
    )

    async def rate_limit(ctx: Any) -> ToolCallResult | None:
        principal = current_group.get() or settings.default_principal
        if not limiter.try_acquire(principal):
            return ToolCallResult(
                decision=ToolDecision.DENY,
                reason=f"Rate limit exceeded for {principal!r}",
            )
        return None

    return rate_limit


def make_egress_hook(settings: AgtAgentOsSettings) -> ConnectHook:
    """Refuse to connect to an upstream whose URL is outside the egress allowlist.

    Runs at ``pre_mcp_connect`` (when the proxy opens an upstream session); a denied
    destination raises, so the gateway never connects to it. ``egress_rules`` are agent-os
    ``EgressRule`` objects (domain ``fnmatch`` + ports); unmatched URLs fall to
    ``egress_default_action`` (``deny`` by default).
    """
    policy = EgressPolicy(default_action=settings.egress_default_action)
    for rule in settings.egress_rules:
        policy.add_rule(rule.domain, rule.ports, rule.protocol, rule.action)

    async def egress_check(context: ConnectContext) -> None:
        decision = policy.check_url(context.server.url)
        if not decision.allowed:
            raise PermissionError(
                f"Egress denied for upstream {context.server.url!r}: {decision.reason}"
            )
        return

    return egress_check
