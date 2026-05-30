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
    from agent_os.semantic_policy import IntentCategory


@dataclass
class AgtAgentOsSettings:
    """Tunables for :class:`~mcp_gateway.integrations.agt.plugin.AgtAgentOsPlugin`.

    Capabilities beyond the always-on policy engine are opt-in via ``enable_*`` toggles;
    each carries its companion agent-os config object so nothing is re-modelled here.
    """

    # Policy engine (always on). Provide policies via ``policy_dir`` (YAML docs, loaded
    # and validated at startup) or in-memory ``policies``; ``policy_dir`` takes precedence.
    policy_dir: str | None = None
    policies: list[PolicyDocument] = field(default_factory=list)
    default_principal: str = "*"
    fail_closed: bool = False

    # Prompt-injection detection (pre_tool_call). Pass an agent-os ``DetectionConfig``.
    enable_prompt_injection: bool = False
    injection_config: DetectionConfig | None = None

    # Semantic-policy intent classification (pre_tool_call).
    enable_semantic_policy: bool = False
    semantic_deny: list[IntentCategory] = field(default_factory=list)
    semantic_confidence_threshold: float = 0.5

    # Response governance (post_tool_call).
    enable_response_scan: bool = False
    enable_credential_redaction: bool = False

    # Egress allowlist (pre_mcp_connect). Provide agent-os ``EgressRule`` objects.
    enable_egress_policy: bool = False
    egress_rules: list[EgressRule] = field(default_factory=list)
    egress_default_action: str = "deny"
