"""agent-os detector/scanner hooks contributed by :class:`AgtAgentOsPlugin`.

Quick-win agent-os capabilities mapped onto the gateway's seams:

- ``pre_tool_call``: prompt-injection detection on arguments, semantic-policy intent
  classification — each denies the call when it fires.
- ``post_tool_call``: response threat scanning (blocks unsafe responses) and credential
  redaction (rewrites secrets out of the response).
- ``pre_mcp_connect``: egress policy (refuses upstreams outside an allowlist).

Each detector is built once and reused across calls. They are synchronous and cheap
(regex / classifier), so they run inline in the async hooks.
"""

from __future__ import annotations

from typing import Any

from agent_os.credential_redactor import CredentialRedactor
from agent_os.egress_policy import EgressPolicy
from agent_os.mcp_response_scanner import MCPResponseScanner
from agent_os.prompt_injection import DetectionConfig, PromptInjectionDetector
from agent_os.semantic_policy import SemanticPolicyEngine
from fastmcp.exceptions import ToolError

from mcp_gateway.hooks import (
    ConnectContext,
    ConnectHook,
    PostToolCallHook,
    PreToolCallHook,
    ToolCallResult,
    ToolDecision,
)
from mcp_gateway.integrations.agt.settings import AgtAgentOsSettings


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
    """Deny a tool call whose classified intent is dangerous / in the deny list."""
    engine = SemanticPolicyEngine(
        deny=settings.semantic_deny or None,
        confidence_threshold=settings.semantic_confidence_threshold,
    )

    async def semantic_policy(ctx: Any) -> ToolCallResult | None:
        classification = engine.check(
            ctx.message.name, getattr(ctx.message, "arguments", None) or {}
        )
        if classification.is_dangerous:
            return ToolCallResult(
                decision=ToolDecision.DENY,
                reason=classification.explanation
                or f"Denied by semantic policy (intent: {classification.category}).",
            )
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
        return None

    return egress_check
