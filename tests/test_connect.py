"""Tests for the client-factory layer: header merging, timeout override, and
transport selection driven by ``pre_mcp_connect`` hooks."""

from __future__ import annotations

from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport

from mcp_gateway.connect import build_client_factory, resolve_connect_settings
from mcp_gateway.hooks import ConnectContext, ConnectSettings, Hooks
from mcp_gateway.models import ServerRecord, Transport


def record(transport: Transport = Transport.HTTP) -> ServerRecord:
    return ServerRecord(
        id="abc",
        name="weather",
        transport=transport,
        url="https://example.com/mcp",
        static_headers={"x-static": "1", "x-shared": "static"},
        timeout_seconds=12.0,
    )


async def test_defaults_when_no_hooks() -> None:
    headers, timeout = await resolve_connect_settings(record(), Hooks())
    assert headers == {"x-static": "1", "x-shared": "static"}
    assert timeout == 12.0


async def test_hook_headers_win_over_static() -> None:
    async def add_auth(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(headers={"x-shared": "dynamic", "authorization": "Bearer t"})

    headers, _ = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[add_auth]))
    assert headers == {
        "x-static": "1",
        "x-shared": "dynamic",
        "authorization": "Bearer t",
    }


async def test_hook_can_override_timeout() -> None:
    async def shorten(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(timeout_seconds=3.0)

    _, timeout = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[shorten]))
    assert timeout == 3.0


async def test_returning_none_keeps_defaults() -> None:
    async def noop(ctx: ConnectContext) -> None:
        return None

    headers, timeout = await resolve_connect_settings(record(), Hooks(pre_mcp_connect=[noop]))
    assert headers == {"x-static": "1", "x-shared": "static"}
    assert timeout == 12.0


async def test_factory_builds_http_transport_with_merged_headers() -> None:
    factory = build_client_factory(record(), Hooks())
    client = await factory()
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.headers["x-static"] == "1"


async def test_factory_builds_sse_transport() -> None:
    factory = build_client_factory(record(Transport.SSE), Hooks())
    client = await factory()
    assert isinstance(client.transport, SSETransport)
