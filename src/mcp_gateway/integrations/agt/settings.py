"""Configuration for the AGT (agent-os) policy plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_os.policies import PolicyDocument


@dataclass
class AgtSettings:
    """Tunables for :class:`~mcp_gateway.integrations.agt.plugin.AgtAgentOsPlugin`.

    Supply policies one of two ways (``policy_dir`` takes precedence):

    - ``policy_dir``: a directory of agent-os YAML policy documents, loaded and
      validated at startup (a malformed document raises).
    - ``policies``: agent-os ``PolicyDocument`` objects built in code.

    At each tool call the plugin passes the selected group
    (``mcp_gateway.access.current_group``) to the policy engine as ``principal`` and
    ``group``, so policies can enforce per group. When no group view is in use the
    group is empty and ``default_principal`` is used.
    """

    policy_dir: str | None = None
    policies: list[PolicyDocument] = field(default_factory=list)
    default_principal: str = "*"
    fail_closed: bool = False

    # Quick-win agent-os capabilities, each off by default.
    enable_prompt_injection: bool = False  # pre: deny calls whose args look like injection
    injection_sensitivity: str = "balanced"  # strict | balanced | permissive
    enable_semantic_policy: bool = False  # pre: deny calls with dangerous classified intent
    semantic_deny: list[str] = field(default_factory=list)  # IntentCategory names to deny
    semantic_confidence_threshold: float = 0.5
    enable_response_scan: bool = False  # post: block responses flagged unsafe
    enable_credential_redaction: bool = False  # post: redact secrets/PII from responses
