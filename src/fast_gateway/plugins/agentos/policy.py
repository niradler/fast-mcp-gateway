"""Build and validate an agent-os policy evaluator from :class:`AgtAgentOsSettings`."""

from __future__ import annotations

import logging
from pathlib import Path

from agent_os.policies import AsyncPolicyEvaluator, PolicyEvaluator

from fast_gateway.plugins.agentos.settings import AgtAgentOsSettings

_logger = logging.getLogger("fast_gateway.plugins.agentos")


def build_evaluator(settings: AgtAgentOsSettings) -> AsyncPolicyEvaluator:
    """Load and validate agent-os policies into an async evaluator.

    ``policy_dir`` wins over in-memory ``policies``. With neither set, raises unless
    ``allow_no_policies`` is True (allow-all mode, logged as a warning).
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
