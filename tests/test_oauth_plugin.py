"""Tests for the OAuthPlugin: hook behavior, token-dir resolution, and build_oauth."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastmcp.client.auth import OAuth

from fast_gateway.hooks import ConnectContext, ConnectSettings
from fast_gateway.models import ServerAuth, ServerRecord, Transport
from fast_gateway.plugins.oauth import OAuthPlugin, build_oauth, default_oauth_token_dir
from fast_gateway.plugins.oauth.plugin import _NonInteractiveOAuth


def _server(
    auth: ServerAuth = ServerAuth.NONE, transport: Transport = Transport.HTTP
) -> ServerRecord:
    return ServerRecord(
        id="s1",
        name="myserver",
        transport=transport,
        url="https://example.com/mcp",
        auth=auth,
    )


async def test_attach_oauth_returns_settings_for_oauth_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    plugin = OAuthPlugin()
    ctx = ConnectContext(server=_server(auth=ServerAuth.OAUTH))
    result = await plugin._attach_oauth(ctx)
    assert isinstance(result, ConnectSettings)
    assert isinstance(result.auth, OAuth)


async def test_attach_oauth_returns_none_for_non_oauth_server() -> None:
    plugin = OAuthPlugin()
    ctx = ConnectContext(server=_server(auth=ServerAuth.NONE))
    result = await plugin._attach_oauth(ctx)
    assert result is None


async def test_attach_oauth_sse_server_also_gets_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    plugin = OAuthPlugin()
    ctx = ConnectContext(server=_server(auth=ServerAuth.OAUTH, transport=Transport.SSE))
    result = await plugin._attach_oauth(ctx)
    assert isinstance(result, ConnectSettings)
    assert isinstance(result.auth, OAuth)


async def test_attach_oauth_returns_non_interactive_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    plugin = OAuthPlugin()
    ctx = ConnectContext(server=_server(auth=ServerAuth.OAUTH))
    result = await plugin._attach_oauth(ctx)
    assert isinstance(result, ConnectSettings)
    assert isinstance(result.auth, _NonInteractiveOAuth)


def test_default_oauth_token_dir_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom_tokens"
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(custom))
    resolved = default_oauth_token_dir()
    assert resolved == custom
    assert custom.exists()


def test_default_oauth_token_dir_creates_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nested" / "oauth"
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(target))
    resolved = default_oauth_token_dir()
    assert resolved == target
    assert target.is_dir()


def test_default_oauth_token_dir_sets_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "mode_test"
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(target))
    default_oauth_token_dir()
    assert target.exists()
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_build_oauth_interactive_returns_plain_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = _server(auth=ServerAuth.OAUTH)
    oauth = build_oauth(srv, interactive=True)
    assert isinstance(oauth, OAuth)
    assert not isinstance(oauth, _NonInteractiveOAuth)


def test_build_oauth_non_interactive_returns_non_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = _server(auth=ServerAuth.OAUTH)
    oauth = build_oauth(srv, interactive=False)
    assert isinstance(oauth, _NonInteractiveOAuth)


async def test_non_interactive_redirect_handler_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = _server(auth=ServerAuth.OAUTH)
    provider = build_oauth(srv, interactive=False)
    assert isinstance(provider, _NonInteractiveOAuth)
    with pytest.raises(RuntimeError, match="fast-gateway login myserver"):
        await provider.redirect_handler("http://example.com/authorize")


def test_build_oauth_default_is_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = _server(auth=ServerAuth.OAUTH)
    oauth = build_oauth(srv)
    assert isinstance(oauth, OAuth)
    assert not isinstance(oauth, _NonInteractiveOAuth)


def test_build_oauth_returns_oauth_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = _server(auth=ServerAuth.OAUTH)
    oauth = build_oauth(srv)
    assert isinstance(oauth, OAuth)
    assert (tmp_path / "tokens").exists()


def test_build_oauth_respects_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    srv = _server(auth=ServerAuth.OAUTH)
    srv = srv.model_copy(update={"oauth_scopes": ["read", "write"]})
    oauth = build_oauth(srv)
    assert isinstance(oauth, OAuth)


def test_build_oauth_no_real_home_dir_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_dir = tmp_path / "my_tokens"
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(token_dir))
    srv = _server(auth=ServerAuth.OAUTH)
    build_oauth(srv)
    assert token_dir.exists()
    assert token_dir.is_dir()
