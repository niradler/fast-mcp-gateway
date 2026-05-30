"""Build and validate an agent-os policy evaluator from :class:`AgtSettings`."""

from __future__ import annotations

from pathlib import Path

from agent_os.policies import AsyncPolicyEvaluator, PolicyEvaluator

from mcp_gateway.integrations.agt.settings import AgtSettings


def build_evaluator(settings: AgtSettings) -> AsyncPolicyEvaluator:
    """Load and validate agent-os policies into an async evaluator.

    Loading is the validation step: agent-os raises on a malformed policy document
    or missing ``policy_dir``. ``policy_dir`` takes precedence over in-memory
    ``policies``.
    """
    if settings.policy_dir is not None:
        directory = Path(settings.policy_dir)
        if not directory.exists():
            raise FileNotFoundError(f"AGT policy_dir does not exist: {settings.policy_dir!r}")
        evaluator = PolicyEvaluator()
        evaluator.load_policies(directory)
    else:
        evaluator = PolicyEvaluator(policies=list(settings.policies) or None)
    return AsyncPolicyEvaluator(evaluator)
