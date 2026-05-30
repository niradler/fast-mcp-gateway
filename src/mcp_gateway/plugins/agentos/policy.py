"""Build and validate an agent-os policy evaluator from :class:`AgtAgentOsSettings`."""

from __future__ import annotations

import logging
from pathlib import Path

from agent_os.policies import AsyncPolicyEvaluator, PolicyEvaluator

from mcp_gateway.plugins.agentos.settings import AgtAgentOsSettings

_logger = logging.getLogger("mcp_gateway.plugins.agentos")


def build_evaluator(settings: AgtAgentOsSettings) -> AsyncPolicyEvaluator:
    """Load and validate agent-os policies into an async evaluator.

    Loading is the validation step: agent-os raises on a malformed policy document
    or missing ``policy_dir``. ``policy_dir`` takes precedence over in-memory
    ``policies``. With neither configured this raises ``ValueError`` unless
    ``settings.allow_no_policies`` is True; in that opt-in case the engine allows every
    call (allow-all), which is logged so a misconfigured no-op plugin is visible.
    """
    if settings.policy_dir is not None:
        directory = Path(settings.policy_dir)
        if not directory.exists():
            raise FileNotFoundError(f"AGT policy_dir does not exist: {settings.policy_dir!r}")
        evaluator = PolicyEvaluator()
        evaluator.load_policies(directory)
    else:
        documents = list(settings.policies)
        if not documents:
            if not settings.allow_no_policies:
                raise ValueError(
                    "AGT plugin has no policy_dir and no policies; set "
                    "allow_no_policies=True to run in allow-all mode."
                )
            _logger.warning(
                "AGT policy plugin has no policy_dir and no policies; all tool calls "
                "will be allowed."
            )
        evaluator = PolicyEvaluator(policies=documents or None)
    return AsyncPolicyEvaluator(evaluator)
