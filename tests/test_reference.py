"""Tests for reference hook factories: audit, error-audit, deny, and confirm hooks."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from fast_gateway.hooks import ToolDecision
from fast_gateway.models import ServerRecord
from fast_gateway.reference import (
    audit_connect_error_hook,
    audit_error_hook,
    audit_hook,
    confirm_hook,
    deny_hook,
)


def make_context(name: str, arguments: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(message=SimpleNamespace(name=name, arguments=arguments or {}))


async def test_audit_hook_returns_response_unchanged() -> None:
    hook = audit_hook()
    ctx = make_context("math_add")
    response = {"result": 42}
    result = await hook(ctx, response)
    assert result is response


async def test_audit_hook_with_string_response() -> None:
    hook = audit_hook()
    ctx = make_context("some_tool")
    result = await hook(ctx, "hello")
    assert result == "hello"


async def test_audit_error_hook_logs_failure(caplog: pytest.LogCaptureFixture) -> None:
    hook = audit_error_hook()
    with caplog.at_level(logging.WARNING, logger="fast_gateway.reference"):
        await hook(make_context("svc_deploy"), RuntimeError("boom"))
    assert "svc_deploy" in caplog.text
    assert "boom" in caplog.text


async def test_audit_connect_error_hook_logs_server(caplog: pytest.LogCaptureFixture) -> None:
    hook = audit_connect_error_hook()
    server = ServerRecord(id="s1", name="weather", url="https://weather.example.com/mcp")
    with caplog.at_level(logging.WARNING, logger="fast_gateway.reference"):
        await hook(server, RuntimeError("refused"))
    assert "weather" in caplog.text
    assert "refused" in caplog.text


async def test_deny_hook_matches_pattern() -> None:
    hook = deny_hook(["admin_*", "secret_*"])
    ctx = make_context("admin_delete")
    result = await hook(ctx)
    assert result is not None
    assert result.decision == ToolDecision.DENY


async def test_deny_hook_no_match_returns_none() -> None:
    hook = deny_hook(["admin_*"])
    ctx = make_context("math_add")
    result = await hook(ctx)
    assert result is None


async def test_deny_hook_empty_patterns_always_none() -> None:
    hook = deny_hook([])
    ctx = make_context("anything_at_all")
    result = await hook(ctx)
    assert result is None


async def test_deny_hook_exact_match() -> None:
    hook = deny_hook(["blocked"])
    ctx = make_context("blocked")
    result = await hook(ctx)
    assert result is not None
    assert result.decision == ToolDecision.DENY


async def test_deny_hook_reason_contains_tool_name() -> None:
    hook = deny_hook(["admin_*"])
    ctx = make_context("admin_wipe")
    result = await hook(ctx)
    assert result is not None
    assert result.reason is not None
    assert "admin_wipe" in result.reason


async def test_deny_hook_glob_wildcard() -> None:
    hook = deny_hook(["*_delete"])
    ctx = make_context("issues_delete")
    result = await hook(ctx)
    assert result is not None
    assert result.decision == ToolDecision.DENY


async def test_confirm_hook_matches_pattern() -> None:
    hook = confirm_hook(["deploy_*"])
    ctx = make_context("deploy_prod")
    result = await hook(ctx)
    assert result is not None
    assert result.decision == ToolDecision.REQUIRE_CONFIRMATION


async def test_confirm_hook_no_match_returns_none() -> None:
    hook = confirm_hook(["deploy_*"])
    ctx = make_context("math_add")
    result = await hook(ctx)
    assert result is None


async def test_confirm_hook_empty_patterns_always_none() -> None:
    hook = confirm_hook([])
    ctx = make_context("deploy_prod")
    result = await hook(ctx)
    assert result is None


async def test_confirm_hook_reason_references_tool_name() -> None:
    hook = confirm_hook(["risky_*"])
    ctx = make_context("risky_op")
    result = await hook(ctx)
    assert result is not None
    assert result.reason is not None
    assert "risky_op" in result.reason


async def test_confirm_hook_multiple_patterns() -> None:
    hook = confirm_hook(["deploy_*", "migrate_*"])
    for name in ("deploy_prod", "migrate_db"):
        ctx = make_context(name)
        result = await hook(ctx)
        assert result is not None
        assert result.decision == ToolDecision.REQUIRE_CONFIRMATION


async def test_deny_hook_multiple_patterns_second_matches() -> None:
    hook = deny_hook(["alpha_*", "beta_*"])
    ctx = make_context("beta_run")
    result = await hook(ctx)
    assert result is not None
    assert result.decision == ToolDecision.DENY
