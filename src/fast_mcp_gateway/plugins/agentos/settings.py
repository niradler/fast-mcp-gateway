"""Configuration for the agent-os plugin (:class:`AgtAgentOsPlugin`).

Agent-os config types (``PolicyDocument``, ``DetectionConfig``, ``EgressRule``, etc.)
are reused verbatim and passed straight through with no transformation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_os.egress_policy import EgressRule
from agent_os.policies import PolicyDocument
from agent_os.prompt_injection import DetectionConfig
from agent_os.semantic_policy import IntentCategory, SemanticPolicyConfig


@dataclass
class AgtAgentOsSettings:
    """Tunables for :class:`~fast_mcp_gateway.plugins.agentos.plugin.AgtAgentOsPlugin`.

    Policy engine (always on) reads ``policy_dir`` or in-memory ``policies``
    (``policy_dir`` wins); ``allow_no_policies`` opts in to allow-all mode.
    ``fail_closed`` (default True) denies calls when policy evaluation raises.
    Remaining capabilities are opt-in via ``enable_*`` toggles.
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
