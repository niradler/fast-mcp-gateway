"""Tests for ``GatewayBuilder.reload``: enabled servers get mounted, disabled ones
are skipped, and reloads are idempotent (no stale or duplicate mounts)."""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_gateway.access import AccessPolicy
from mcp_gateway.builder import GatewayBuilder
from mcp_gateway.hooks import Hooks
from mcp_gateway.models import ServerCreate
from mcp_gateway.store.sqlite import SqliteStore


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
    # Initially no rules — all allowed
    assert policy.allows("svc_delete_all") is True

    from mcp_gateway.models import ServerPatch

    await store.update_server(rec.id, ServerPatch(deny=["delete_*"]))
    await builder.reload()

    assert policy.allows("svc_delete_all") is False
