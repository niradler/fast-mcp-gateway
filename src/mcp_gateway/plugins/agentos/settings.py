"""Configuration for the agent-os plugin (:class:`AgtAgentOsPlugin`).

Where agent-os already defines a config type it is reused verbatim, so values pass
straight through to the agent-os APIs with no transformation: policy documents
(``PolicyDocument``), prompt-injection config (``DetectionConfig``), semantic-policy
intent categories (``IntentCategory``), and egress rules (``EgressRule``). The remaining
fields are gateway-level orchestration knobs that agent-os does not model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_os.egress_policy import EgressRule
    from agent_os.policies import PolicyDocument
    from agent_os.prompt_injection import DetectionConfig
    from agent_os.semantic_policy import IntentCategory, SemanticPolicyConfig


@dataclass
class AgtAgentOsSettings:
    """Tunables for :class:`~mcp_gateway.plugins.agentos.plugin.AgtAgentOsPlugin`.

    Capabilities beyond the always-on policy engine are opt-in via ``enable_*`` toggles,
    each paired with its agent-os config object so nothing is re-modelled here.

    Policy engine (always on) reads ``policy_dir`` (YAML, validated at startup) or
    in-memory ``policies`` (``policy_dir`` wins). With neither set the plugin refuses to
    build unless ``allow_no_policies`` is True, which opts in to running with no policies
    (allow-all mode). ``fail_closed`` (default True, the safer default for a governance
    component) denies a tool call when policy evaluation raises. ``injection_config`` is an agent-os
    ``DetectionConfig`` (pre_tool_call). Semantic policy (pre_tool_call) classifies intent;
    its built-in signals are only samples, so supply a tuned ``semantic_config``
    (``SemanticPolicyConfig``) and the ``semantic_deny`` categories to enforce. Response
    scan and credential redaction run on post_tool_call. ``egress_rules`` are agent-os
    ``EgressRule`` objects checked at pre_mcp_connect.
    """

    policy_dir: str | None = None
    policies: list[PolicyDocument] = field(default_factory=list)
    allow_no_policies: bool = False
    default_principal: str = "*"
    fail_closed: bool = True

    enable_prompt_injection: bool = False
    injection_config: DetectionConfig | None = None

    enable_semantic_policy: bool = False
    semantic_deny: list[IntentCategory] = field(default_factory=list)
    semantic_confidence_threshold: float = 0.5
    semantic_config: SemanticPolicyConfig | None = None

    enable_response_scan: bool = False
    enable_credential_redaction: bool = False

    enable_egress_policy: bool = False
    egress_rules: list[EgressRule] = field(default_factory=list)
    egress_default_action: str = "deny"
