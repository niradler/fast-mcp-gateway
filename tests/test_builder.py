"""Tests for ``GatewayBuilder.reload``: enabled servers get mounted, disabled ones
are skipped, and reloads are idempotent (no stale or duplicate mounts)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from fast_gateway.access import AccessPolicy
from fast_gateway.builder import GatewayBuilder
from fast_gateway.hooks import Hooks
from fast_gateway.models import CatalogTool, ServerCreate
from fast_gateway.store.sqlite import SqliteStore


def server(
    name: str,
    *,
    enabled: bool = True,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> ServerCreate:
    return ServerCreate(
        name=name,
        url=f"https://{name}.example.com/mcp",
        enabled=enabled,
        allow=allow or [],
        deny=deny or [],
    )


async def make_builder() -> tuple[FastMCP, GatewayBuilder, SqliteStore, AccessPolicy]:
    store = SqliteStore(":memory:")
    await store.initialize()
    mcp: FastMCP = FastMCP("test-gateway")
    policy = AccessPolicy()
    builder = GatewayBuilder(mcp=mcp, store=store, hooks=Hooks(), policy=policy)
    return mcp, builder, store, policy


async def test_reload_mounts_only_enabled_servers() -> None:
    mcp, builder, store, _ = await make_builder()
    baseline = len(mcp.providers)
    await store.create_server(server("alpha"))
    await store.create_server(server("beta", enabled=False))

    await builder.reload()

    assert len(mcp.providers) == baseline + 1


async def test_reload_is_idempotent() -> None:
    mcp, builder, store, _ = await make_builder()
    baseline = len(mcp.providers)
    await store.create_server(server("alpha"))
    await store.create_server(server("gamma"))

    await builder.reload()
    after_first = len(mcp.providers)
    await builder.reload()

    assert after_first == baseline + 2
    assert len(mcp.providers) == after_first


async def test_reload_drops_removed_servers() -> None:
    mcp, builder, store, _ = await make_builder()
    baseline = len(mcp.providers)
    created = await store.create_server(server("alpha"))
    await builder.reload()
    assert len(mcp.providers) == baseline + 1

    await store.delete_server(created.id)
    await builder.reload()

    assert len(mcp.providers) == baseline


# ---------------------------------------------------------------------------
# Policy is populated by reload
# ---------------------------------------------------------------------------


async def test_reload_populates_policy_allows() -> None:
    """After reload, policy reflects the store's allow rules."""
    _mcp, builder, store, policy = await make_builder()
    await store.create_server(server("github", allow=["get_*"]))
    await builder.reload()

    assert policy.allows("github_get_repo") is True
    assert policy.allows("github_delete_repo") is False


async def test_reload_populates_policy_deny() -> None:
    """After reload, policy reflects the store's deny rules."""
    _mcp, builder, store, policy = await make_builder()
    await store.create_server(server("svc", deny=["delete_*"]))
    await builder.reload()

    assert policy.allows("svc_delete_all") is False
    assert policy.allows("svc_list") is True


async def test_reload_updates_policy_on_second_reload() -> None:
    """A second reload with changed server rules updates the policy."""
    _mcp, builder, store, policy = await make_builder()
    rec = await store.create_server(server("svc"))
    await builder.reload()
    assert policy.allows("svc_delete_all") is True

    from fast_gateway.models import ServerPatch

    await store.update_server(rec.id, ServerPatch(deny=["delete_*"]))
    await builder.reload()

    assert policy.allows("svc_delete_all") is False


async def test_reload_returns_empty_degraded_when_all_healthy() -> None:
    """reload() returns an empty list when all enabled servers introspect cleanly."""
    _mcp, builder, store, _ = await make_builder()
    await store.create_server(server("alpha"))

    with patch("fast_gateway.builder.collect_catalog", new=AsyncMock(return_value=([], set()))):
        degraded = await builder.reload()

    assert degraded == []


async def test_reload_returns_degraded_server_name_on_introspection_failure() -> None:
    """reload() returns the failing server's name when introspection raises."""
    _mcp, builder, store, _ = await make_builder()
    rec = await store.create_server(server("broken"))

    async def _failing_catalog(servers: object, hooks: object) -> tuple[list[object], set[str]]:
        return [], {rec.id}

    with patch("fast_gateway.builder.collect_catalog", new=AsyncMock(side_effect=_failing_catalog)):
        degraded = await builder.reload()

    assert degraded == ["broken"]


async def test_reload_healthy_server_not_in_degraded() -> None:
    """A healthy server never appears in the degraded list."""
    _mcp, builder, store, _ = await make_builder()
    await store.create_server(server("good"))

    with patch("fast_gateway.builder.collect_catalog", new=AsyncMock(return_value=([], set()))):
        degraded = await builder.reload()

    assert "good" not in degraded


# ---------------------------------------------------------------------------
# rebuild_mounts: mounts + policy without any upstream introspection
# ---------------------------------------------------------------------------


async def test_rebuild_mounts_does_not_introspect_upstreams() -> None:
    mcp, builder, store, policy = await make_builder()
    baseline = len(mcp.providers)
    await store.create_server(server("alpha", deny=["delete_*"]))

    mock_catalog = AsyncMock(return_value=([], set()))
    with patch("fast_gateway.builder.collect_catalog", new=mock_catalog):
        await builder.rebuild_mounts()

    mock_catalog.assert_not_awaited()
    assert len(mcp.providers) == baseline + 1
    assert policy.allows("alpha_delete_x") is False


# ---------------------------------------------------------------------------
# refresh_server: on-demand single-server introspection
# ---------------------------------------------------------------------------


def _catalog_tool(server_id: str, namespace: str, bare: str) -> CatalogTool:
    return CatalogTool(
        server_id=server_id,
        namespace=namespace,
        name=f"{namespace}_{bare}",
        bare_name=bare,
        description=f"{bare} tool",
    )


async def test_refresh_server_unknown_id_raises() -> None:
    _mcp, builder, _store, _ = await make_builder()
    with pytest.raises(KeyError):
        await builder.refresh_server("nope")


async def test_refresh_server_updates_only_that_server() -> None:
    mcp, builder, store, _ = await make_builder()
    baseline = len(mcp.providers)
    alpha = await store.create_server(server("alpha"))
    beta = await store.create_server(server("beta"))
    await store.replace_catalog(
        [
            _catalog_tool(alpha.id, "alpha", "old"),
            _catalog_tool(beta.id, "beta", "stable"),
        ]
    )

    async def single(servers: object, hooks: object) -> tuple[list[CatalogTool], set[str]]:
        return [_catalog_tool(alpha.id, "alpha", "fresh")], set()

    with patch("fast_gateway.builder.collect_catalog", new=AsyncMock(side_effect=single)):
        ok = await builder.refresh_server(alpha.id)

    assert ok is True
    names = {t.name for t in await store.list_catalog()}
    assert names == {"alpha_fresh", "beta_stable"}
    assert len(mcp.providers) == baseline + 2


async def test_refresh_server_failure_keeps_last_known_rows() -> None:
    _mcp, builder, store, _ = await make_builder()
    alpha = await store.create_server(server("alpha"))
    await store.replace_catalog([_catalog_tool(alpha.id, "alpha", "old")])

    with patch(
        "fast_gateway.builder.collect_catalog",
        new=AsyncMock(return_value=([], {alpha.id})),
    ):
        ok = await builder.refresh_server(alpha.id)

    assert ok is False
    names = {t.name for t in await store.list_catalog()}
    assert names == {"alpha_old"}


async def test_refresh_server_disabled_drops_rows_without_introspection() -> None:
    _mcp, builder, store, _ = await make_builder()
    alpha = await store.create_server(server("alpha", enabled=False))
    other = await store.create_server(server("beta"))
    await store.replace_catalog(
        [
            _catalog_tool(alpha.id, "alpha", "stale"),
            _catalog_tool(other.id, "beta", "stable"),
        ]
    )

    mock_catalog = AsyncMock(return_value=([], set()))
    with patch("fast_gateway.builder.collect_catalog", new=mock_catalog):
        ok = await builder.refresh_server(alpha.id)

    assert ok is True
    mock_catalog.assert_not_awaited()
    names = {t.name for t in await store.list_catalog()}
    assert names == {"beta_stable"}
