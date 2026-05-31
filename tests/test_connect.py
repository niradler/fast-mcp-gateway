"""Tests for the client-factory layer: header merging, timeout override, auth seam,
and transport selection driven by ``pre_mcp_connect`` hooks."""

from __future__ import annotations

from typing import Any

from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport

from fast_gateway.connect import _build_transport, build_client_factory, resolve_connect_settings
from fast_gateway.hooks import ConnectContext, ConnectSettings, Hooks
from fast_gateway.models import ServerAuth, ServerRecord, Transport


def record(
    transport: Transport = Transport.HTTP, auth: ServerAuth = ServerAuth.NONE
) -> ServerRecord:
    return ServerRecord(
        id="abc",
        name="weather",
        transport=transport,
        url="https://example.com/mcp",
        static_headers={"x-static": "1", "x-shared": "static"},
        timeout_seconds=12.0,
        auth=auth,
    )


async def test_defaults_when_no_hooks() -> None:
    headers, timeout, auth = await resolve_connect_settings(record(), Hooks())
    assert headers == {"x-static": "1", "x-shared": "static"}
    assert timeout == 12.0
    assert auth is None


async def test_hook_headers_win_over_static() -> None:
    async def add_auth(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(headers={"x-shared": "dynamic", "authorization": "Bearer t"})

    headers, _, _ = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[add_auth]))
    assert headers == {
        "x-static": "1",
        "x-shared": "dynamic",
        "authorization": "Bearer t",
    }


async def test_hook_can_override_timeout() -> None:
    async def shorten(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(timeout_seconds=3.0)

    _, timeout, _ = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[shorten]))
    assert timeout == 3.0


async def test_returning_none_keeps_defaults() -> None:
    async def noop(ctx: ConnectContext) -> None:
        return None

    headers, timeout, auth = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[noop]))
    assert headers == {"x-static": "1", "x-shared": "static"}
    assert timeout == 12.0
    assert auth is None


async def test_hook_can_supply_auth_sentinel() -> None:
    sentinel: Any = object()

    async def attach_auth(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(auth=sentinel)

    _, _, auth = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[attach_auth]))
    assert auth is sentinel


async def test_last_hook_auth_wins() -> None:
    first: Any = object()
    second: Any = object()

    async def hook_a(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(auth=first)

    async def hook_b(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(auth=second)

    _, _, auth = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[hook_a, hook_b]))
    assert auth is second


async def test_hook_auth_forwarded_to_http_transport() -> None:
    sentinel: Any = object()

    async def attach_auth(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(auth=sentinel)

    factory = build_client_factory(record(), Hooks(pre_mcp_connect=[attach_auth]))
    client = await factory()
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.auth is sentinel


async def test_hook_auth_forwarded_to_sse_transport() -> None:
    sentinel: Any = object()

    async def attach_auth(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(auth=sentinel)

    factory = build_client_factory(record(Transport.SSE), Hooks(pre_mcp_connect=[attach_auth]))
    client = await factory()
    assert isinstance(client.transport, SSETransport)
    assert client.transport.auth is sentinel


async def test_factory_builds_http_transport_with_merged_headers() -> None:
    factory = build_client_factory(record(), Hooks())
    client = await factory()
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.headers["x-static"] == "1"


async def test_factory_builds_sse_transport() -> None:
    factory = build_client_factory(record(Transport.SSE), Hooks())
    client = await factory()
    assert isinstance(client.transport, SSETransport)


async def test_build_transport_no_auth_yields_none_auth_http() -> None:
    srv = record()
    transport = _build_transport(srv, {"x": "1"}, None)
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.auth is None


async def test_build_transport_no_auth_yields_none_auth_sse() -> None:
    srv = record(Transport.SSE)
    transport = _build_transport(srv, {}, None)
    assert isinstance(transport, SSETransport)
    assert transport.auth is None


async def test_build_transport_passes_auth_to_http() -> None:
    sentinel: Any = object()
    srv = record()
    transport = _build_transport(srv, {}, sentinel)
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.auth is sentinel


async def test_build_transport_passes_auth_to_sse() -> None:
    sentinel: Any = object()
    srv = record(Transport.SSE)
    transport = _build_transport(srv, {}, sentinel)
    assert isinstance(transport, SSETransport)
    assert transport.auth is sentinel


async def test_no_auth_transport_has_no_auth() -> None:
    srv = record(auth=ServerAuth.NONE)
    factory = build_client_factory(srv, Hooks())
    client = await factory()
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.auth is None
