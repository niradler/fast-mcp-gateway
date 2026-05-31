"""Tests for the client-factory layer: header merging, timeout override, and
transport selection driven by ``pre_mcp_connect`` hooks."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.client.auth import OAuth
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport

from fast_gateway.connect import build_client_factory, build_oauth, resolve_connect_settings
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


# ---------------------------------------------------------------------------
# OAuth transport tests
# ---------------------------------------------------------------------------


async def test_oauth_transport_has_oauth_auth_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = record(auth=ServerAuth.OAUTH)
    factory = build_client_factory(srv, Hooks())
    client = await factory()
    assert isinstance(client.transport, StreamableHttpTransport)
    assert isinstance(client.transport.auth, OAuth)


async def test_none_auth_transport_has_no_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = record(auth=ServerAuth.NONE)
    factory = build_client_factory(srv, Hooks())
    client = await factory()
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.auth is None


async def test_oauth_sse_transport_has_oauth_auth_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = record(transport=Transport.SSE, auth=ServerAuth.OAUTH)
    factory = build_client_factory(srv, Hooks())
    client = await factory()
    assert isinstance(client.transport, SSETransport)
    assert isinstance(client.transport.auth, OAuth)


async def test_build_oauth_creates_persistent_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_dir = tmp_path / "tokens"
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(token_dir))
    srv = record(auth=ServerAuth.OAUTH, transport=Transport.HTTP)
    srv = srv.model_copy(update={"oauth_scopes": ["user", "read:data"]})
    oauth = build_oauth(srv)
    assert isinstance(oauth, OAuth)
    assert token_dir.exists()


async def test_build_oauth_no_real_home_dir_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_dir = tmp_path / "my_tokens"
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(token_dir))
    srv = record(auth=ServerAuth.OAUTH)
    build_oauth(srv)
    assert token_dir.exists()
    assert token_dir.is_dir()
