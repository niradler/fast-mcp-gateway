"""Tests for the HIL browser-approval plugin."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fast_gateway.config import HilConfig
from fast_gateway.hooks import ConfirmationContext
from fast_gateway.plugins.hil.pending import PendingApproval, PendingRegistry
from fast_gateway.plugins.hil.plugin import HumanApprovalPlugin
from fast_gateway.plugins.hil.views import render_detail, render_list, render_result

# ---------------------------------------------------------------------------
# PendingRegistry
# ---------------------------------------------------------------------------


async def test_registry_create_and_approve() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {"env": "prod"}, "needs sign-off")

    assert approval.tool_name == "deploy"
    assert approval.arguments == {"env": "prod"}
    assert approval.reason == "needs sign-off"

    registry.resolve(approval.id, True)
    result = await registry.wait(approval.id, wait_timeout=1.0)
    assert result is True


async def test_registry_create_and_deny() -> None:
    registry = PendingRegistry()
    approval = registry.create("drop_table", {}, None)
    registry.resolve(approval.id, False)
    result = await registry.wait(approval.id, wait_timeout=1.0)
    assert result is False


async def test_registry_timeout_returns_false() -> None:
    registry = PendingRegistry()
    approval = registry.create("slow_tool", {}, None)
    result = await registry.wait(approval.id, wait_timeout=0.05)
    assert result is False


async def test_registry_resolve_unknown_id_returns_false() -> None:
    registry = PendingRegistry()
    resolved = registry.resolve("nonexistent-id", True)
    assert resolved is False


async def test_registry_double_resolve_second_returns_false() -> None:
    registry = PendingRegistry()
    approval = registry.create("tool", {}, None)
    first = registry.resolve(approval.id, True)
    second = registry.resolve(approval.id, False)
    assert first is True
    assert second is False
    result = await registry.wait(approval.id, wait_timeout=0.5)
    assert result is True


async def test_registry_cancel_all_denies_waiters() -> None:
    registry = PendingRegistry()
    approval = registry.create("tool", {}, None)

    async def waiter() -> bool:
        return await registry.wait(approval.id, wait_timeout=5.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    registry.cancel_all()
    result = await task
    assert result is False


async def test_registry_list_pending_before_and_after_resolve() -> None:
    registry = PendingRegistry()
    a1 = registry.create("t1", {}, None)
    a2 = registry.create("t2", {}, None)
    assert len(registry.list_pending()) == 2

    registry.resolve(a1.id, True)
    await registry.wait(a1.id, wait_timeout=0.5)
    assert len(registry.list_pending()) == 1
    assert registry.list_pending()[0].id == a2.id


# ---------------------------------------------------------------------------
# Views (pure string functions)
# ---------------------------------------------------------------------------


def test_render_list_empty() -> None:
    html = render_list([])
    assert "No pending approvals" in html


def test_render_list_shows_tool_name() -> None:
    approval = PendingApproval(id="abc123", tool_name="deploy", arguments={}, reason=None)
    html = render_list([approval])
    assert "deploy" in html
    assert "abc123" in html


def test_render_detail_escapes_html() -> None:
    approval = PendingApproval(
        id="xss",
        tool_name="<script>alert(1)</script>",
        arguments={"k": "<b>v</b>"},
        reason="<em>reason</em>",
    )
    result = render_detail(approval)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "&lt;em&gt;reason&lt;/em&gt;" in result


def test_render_detail_has_approve_and_deny_forms() -> None:
    approval = PendingApproval(id="myid", tool_name="tool", arguments={}, reason=None)
    result = render_detail(approval)
    assert 'action="myid/approve"' in result
    assert 'action="myid/deny"' in result
    assert 'method="post"' in result


def test_render_result_approved() -> None:
    result = render_result(True, "deploy")
    assert "Approved" in result
    assert "deploy" in result


def test_render_result_denied() -> None:
    result = render_result(False, "drop_table")
    assert "Denied" in result
    assert "drop_table" in result


# ---------------------------------------------------------------------------
# Confirmation hook end-to-end (no real browser)
# ---------------------------------------------------------------------------


async def test_hook_approve_flow() -> None:
    plugin = HumanApprovalPlugin(HilConfig(auto_open_browser=False, timeout_seconds=2.0))
    mock_ctx = MagicMock()
    mock_ctx.store = MagicMock()
    mock_ctx.mcp = MagicMock()
    mock_ctx.reload = MagicMock()
    contributions = plugin.contributions(mock_ctx)
    hook = contributions.hooks.confirmation[0]

    ctx = ConfirmationContext(tool_name="deploy", arguments={"env": "prod"}, reason="review")

    async def approver() -> bool:
        return await hook(ctx)

    task = asyncio.create_task(approver())
    await asyncio.sleep(0.01)

    pending = plugin._registry.list_pending()
    assert len(pending) == 1
    plugin._registry.resolve(pending[0].id, True)

    result = await task
    assert result is True


async def test_hook_deny_flow() -> None:
    plugin = HumanApprovalPlugin(HilConfig(auto_open_browser=False, timeout_seconds=2.0))
    mock_ctx = MagicMock()
    contributions = plugin.contributions(mock_ctx)
    hook = contributions.hooks.confirmation[0]

    ctx = ConfirmationContext(tool_name="drop_table", arguments={}, reason=None)

    async def run_hook() -> bool:
        return await hook(ctx)

    task: asyncio.Task[bool] = asyncio.create_task(run_hook())
    await asyncio.sleep(0.01)

    pending = plugin._registry.list_pending()
    assert len(pending) == 1
    plugin._registry.resolve(pending[0].id, False)

    result = await task
    assert result is False


async def test_hook_timeout_returns_false() -> None:
    plugin = HumanApprovalPlugin(HilConfig(auto_open_browser=False, timeout_seconds=0.05))
    mock_ctx = MagicMock()
    contributions = plugin.contributions(mock_ctx)
    hook = contributions.hooks.confirmation[0]

    ctx = ConfirmationContext(tool_name="slow_tool", arguments={}, reason=None)
    result = await hook(ctx)
    assert result is False


async def test_teardown_denies_pending() -> None:
    plugin = HumanApprovalPlugin(HilConfig(auto_open_browser=False, timeout_seconds=10.0))
    mock_ctx = MagicMock()
    contributions = plugin.contributions(mock_ctx)
    hook = contributions.hooks.confirmation[0]

    ctx = ConfirmationContext(tool_name="tool", arguments={}, reason=None)

    async def run_hook() -> bool:
        return await hook(ctx)

    task: asyncio.Task[bool] = asyncio.create_task(run_hook())
    await asyncio.sleep(0.01)
    await plugin.teardown()
    result = await task
    assert result is False


# ---------------------------------------------------------------------------
# Admin router via TestClient (sync HTTP tests; futures created inside async fixture)
# ---------------------------------------------------------------------------


def _make_test_app(registry: PendingRegistry) -> FastAPI:
    plugin = HumanApprovalPlugin.__new__(HumanApprovalPlugin)
    plugin._settings = HilConfig(auto_open_browser=False)
    plugin._registry = registry
    app = FastAPI()
    app.include_router(plugin._build_router(), prefix="/hil")
    return app


async def test_router_list_empty() -> None:
    registry = PendingRegistry()
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.get("/hil/")
        assert resp.status_code == 200
        assert "No pending approvals" in resp.text


async def test_router_list_shows_pending() -> None:
    registry = PendingRegistry()
    approval = registry.create("my_tool", {"x": 1}, "check this")
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.get("/hil/")
        assert resp.status_code == 200
        assert "my_tool" in resp.text
        assert approval.id in resp.text


async def test_router_detail_200() -> None:
    registry = PendingRegistry()
    approval = registry.create("my_tool", {"x": 1}, "check this")
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.get(f"/hil/{approval.id}")
        assert resp.status_code == 200
        assert "my_tool" in resp.text
        assert "check this" in resp.text


async def test_router_detail_unknown_returns_404() -> None:
    registry = PendingRegistry()
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.get("/hil/deadbeef")
        assert resp.status_code == 404


async def test_router_approve_resolves_future() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {}, None)
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.post(f"/hil/{approval.id}/approve")
        assert resp.status_code == 200
        assert "Approved" in resp.text
    result = await registry.wait(approval.id, wait_timeout=0.1)
    assert result is True


async def test_router_deny_resolves_future() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {}, None)
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.post(f"/hil/{approval.id}/deny")
        assert resp.status_code == 200
        assert "Denied" in resp.text
    result = await registry.wait(approval.id, wait_timeout=0.1)
    assert result is False


async def test_router_detail_escapes_arguments() -> None:
    registry = PendingRegistry()
    registry.create("tool", {"key": "<b>xss</b>"}, None)
    pending = registry.list_pending()[0]
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.get(f"/hil/{pending.id}")
        assert "<b>" not in resp.text
        assert "&lt;b&gt;" in resp.text


# ---------------------------------------------------------------------------
# Pluggable notifier
# ---------------------------------------------------------------------------


async def test_custom_notifier_receives_approval_and_url() -> None:
    seen: list[tuple[str, str]] = []

    async def notify(approval: PendingApproval, url: str) -> None:
        seen.append((approval.tool_name, url))

    plugin = HumanApprovalPlugin(
        HilConfig(auto_open_browser=True, timeout_seconds=2.0), notifier=notify
    )
    contributions = plugin.contributions(MagicMock())
    hook = contributions.hooks.confirmation[0]
    ctx = ConfirmationContext(tool_name="deploy", arguments={}, reason=None)

    async def run_hook() -> bool:
        return await hook(ctx)

    task: asyncio.Task[bool] = asyncio.create_task(run_hook())
    await asyncio.sleep(0.01)
    assert len(seen) == 1
    tool_name, url = seen[0]
    assert tool_name == "deploy"
    pending = plugin._registry.list_pending()
    assert url.endswith(pending[0].id)

    plugin._registry.resolve(pending[0].id, True)
    assert await task is True


async def test_failing_notifier_still_waits_for_decision() -> None:
    async def explode(approval: PendingApproval, url: str) -> None:
        raise RuntimeError("slack is down")

    plugin = HumanApprovalPlugin(HilConfig(timeout_seconds=2.0), notifier=explode)
    contributions = plugin.contributions(MagicMock())
    hook = contributions.hooks.confirmation[0]
    ctx = ConfirmationContext(tool_name="deploy", arguments={}, reason=None)

    async def run_hook() -> bool:
        return await hook(ctx)

    task: asyncio.Task[bool] = asyncio.create_task(run_hook())
    await asyncio.sleep(0.01)
    pending = plugin._registry.list_pending()
    assert len(pending) == 1
    plugin._registry.resolve(pending[0].id, True)
    assert await task is True


# ---------------------------------------------------------------------------
# JSON decision API
# ---------------------------------------------------------------------------


async def test_json_list_pending() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {"env": "prod"}, "review")
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.get("/hil/pending")
        assert resp.status_code == 200
        body = resp.json()
        assert body == [
            {
                "id": approval.id,
                "tool_name": "deploy",
                "arguments": {"env": "prod"},
                "reason": "review",
            }
        ]


async def test_json_detail_200_and_404() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {}, None)
    app = _make_test_app(registry)
    with TestClient(app) as client:
        ok = client.get(f"/hil/pending/{approval.id}")
        assert ok.status_code == 200
        assert ok.json()["tool_name"] == "deploy"
        missing = client.get("/hil/pending/deadbeef")
        assert missing.status_code == 404


async def test_json_approve_resolves_future() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {}, None)
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.post(f"/hil/pending/{approval.id}/approve")
        assert resp.status_code == 200
        assert resp.json() == {"id": approval.id, "tool_name": "deploy", "approved": True}
    assert await registry.wait(approval.id, wait_timeout=0.1) is True


async def test_json_deny_resolves_future() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {}, None)
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.post(f"/hil/pending/{approval.id}/deny")
        assert resp.status_code == 200
        assert resp.json()["approved"] is False
    assert await registry.wait(approval.id, wait_timeout=0.1) is False


async def test_json_decide_unknown_returns_404() -> None:
    registry = PendingRegistry()
    app = _make_test_app(registry)
    with TestClient(app) as client:
        resp = client.post("/hil/pending/deadbeef/approve")
        assert resp.status_code == 404


async def test_json_double_decision_returns_404() -> None:
    registry = PendingRegistry()
    approval = registry.create("deploy", {}, None)
    app = _make_test_app(registry)
    with TestClient(app) as client:
        first = client.post(f"/hil/pending/{approval.id}/approve")
        assert first.status_code == 200
        second = client.post(f"/hil/pending/{approval.id}/deny")
        assert second.status_code == 404
