"""Tests for the AGT (agent-os) policy plugin.

Skipped unless the optional ``agt`` extra is installed (``agent_os`` importable).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_os")


def _deny_doc(field: str, value: str, *, name: str = "deny-rule") -> Any:
    """An agent-os PolicyDocument with one rule that denies when ``field == value``."""
    from agent_os.policies import PolicyCondition, PolicyDocument, PolicyOperator, PolicyRule

    condition = PolicyCondition(field=field, operator=PolicyOperator.EQ, value=value)
    rule = PolicyRule(
        name=name, condition=condition, action="deny", message=f"{field}={value} denied"
    )
    return PolicyDocument(version="1.0", name="test-policy", rules=[rule])


def _gateway_context() -> Any:
    from fastmcp import FastMCP

    from mcp_gateway.plugins import GatewayContext
    from mcp_gateway.store.sqlite import SqliteStore

    async def _noop_reload() -> None: ...

    return GatewayContext(store=SqliteStore(":memory:"), mcp=FastMCP("t"), reload=_noop_reload)


async def _run_hook(hook: Any, tool_name: str, group: str | None) -> Any:
    from mcp_gateway.access import current_group

    token = current_group.set(group)
    try:
        return await hook(SimpleNamespace(message=SimpleNamespace(name=tool_name, arguments={})))
    finally:
        current_group.reset(token)


def test_agt_policy_api_surface() -> None:
    from agent_os.policies import AsyncPolicyEvaluator, PolicyDecision, PolicyEvaluator

    assert inspect.iscoroutinefunction(AsyncPolicyEvaluator.evaluate)
    assert {"allowed", "reason"} <= set(PolicyDecision.model_fields)
    assert hasattr(PolicyEvaluator, "load_policies")
    assert hasattr(PolicyEvaluator, "evaluate")


async def test_build_evaluator_denies_via_in_memory_policy() -> None:
    from mcp_gateway.integrations.agt.policy import build_evaluator
    from mcp_gateway.integrations.agt.settings import AgtSettings

    evaluator = build_evaluator(AgtSettings(policies=[_deny_doc("resource", "delete_all")]))

    allowed = await evaluator.evaluate({"action": "tool_call", "resource": "read_file"})
    denied = await evaluator.evaluate({"action": "tool_call", "resource": "delete_all"})
    assert allowed.allowed is True
    assert denied.allowed is False


def test_build_evaluator_missing_dir_raises() -> None:
    from mcp_gateway.integrations.agt.policy import build_evaluator
    from mcp_gateway.integrations.agt.settings import AgtSettings

    with pytest.raises(FileNotFoundError):
        build_evaluator(AgtSettings(policy_dir="does-not-exist-xyz"))


async def test_build_evaluator_loads_and_validates_yaml_dir(tmp_path: Any) -> None:
    from mcp_gateway.integrations.agt.policy import build_evaluator
    from mcp_gateway.integrations.agt.settings import AgtSettings

    (tmp_path / "policy.yaml").write_text(
        'version: "1.0"\n'
        "name: yaml-policy\n"
        "rules:\n"
        "  - name: no-delete\n"
        "    condition:\n"
        "      field: resource\n"
        "      operator: eq\n"
        "      value: delete_all\n"
        "    action: deny\n"
        "    message: delete_all is forbidden\n"
    )
    evaluator = build_evaluator(AgtSettings(policy_dir=str(tmp_path)))

    denied = await evaluator.evaluate({"action": "tool_call", "resource": "delete_all"})
    assert denied.allowed is False
    assert "forbidden" in denied.reason


async def test_enforce_hook_denies_disallowed_tool() -> None:
    from mcp_gateway.hooks import ToolDecision
    from mcp_gateway.integrations.agt.plugin import AgtPolicyPlugin
    from mcp_gateway.integrations.agt.settings import AgtSettings

    plugin = AgtPolicyPlugin(AgtSettings(policies=[_deny_doc("resource", "delete_all")]))
    await plugin.setup()
    hook = plugin.contributions(_gateway_context()).hooks.pre_tool_call[0]

    assert await _run_hook(hook, "read_file", None) is None
    denied = await _run_hook(hook, "delete_all", None)
    assert denied is not None
    assert denied.decision is ToolDecision.DENY
    assert "delete_all" in (denied.reason or "")
    await plugin.teardown()


async def test_enforce_hook_scopes_by_current_group() -> None:
    from mcp_gateway.hooks import ToolDecision
    from mcp_gateway.integrations.agt.plugin import AgtPolicyPlugin
    from mcp_gateway.integrations.agt.settings import AgtSettings

    plugin = AgtPolicyPlugin(AgtSettings(policies=[_deny_doc("group", "restricted")]))
    await plugin.setup()
    hook = plugin.contributions(_gateway_context()).hooks.pre_tool_call[0]

    denied = await _run_hook(hook, "any_tool", "restricted")
    assert denied is not None
    assert denied.decision is ToolDecision.DENY
    assert await _run_hook(hook, "any_tool", "open") is None
    await plugin.teardown()


async def test_agt_plugin_satisfies_protocol_and_assembles() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mcp_gateway.app import create_gateway
    from mcp_gateway.integrations.agt.plugin import AgtPolicyPlugin
    from mcp_gateway.integrations.agt.settings import AgtSettings
    from mcp_gateway.plugins import Plugin
    from mcp_gateway.store.sqlite import SqliteStore

    plugin: Plugin = AgtPolicyPlugin(AgtSettings(policies=[_deny_doc("resource", "delete_all")]))
    assert isinstance(plugin, Plugin)
    assert plugin.name == "agt"

    gateway = create_gateway(SqliteStore(":memory:"), plugins=[plugin])
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    with TestClient(app):
        pass
