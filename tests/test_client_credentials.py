"""Tests for the OAuth2 client-credentials grant: auth provider, hook, model rules."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from fast_gateway.hooks import ConnectContext
from fast_gateway.models import ServerAuth, ServerCreate, ServerRecord
from fast_gateway.plugins.oauth import (
    ClientCredentialsAuth,
    OAuthPlugin,
    build_client_credentials,
    client_credentials_hook,
)


class FakeIdp:
    """MockTransport handler simulating a token endpoint plus a guarded API."""

    def __init__(self) -> None:
        self.token_requests: list[dict[str, str]] = []
        self.issued = 0
        self.expires_in: Any = 3600

    @property
    def current_token(self) -> str:
        return f"tok-{self.issued}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            self.token_requests.append(
                {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            )
            self.issued += 1
            body: dict[str, Any] = {"access_token": self.current_token}
            if self.expires_in is not None:
                body["expires_in"] = self.expires_in
            return httpx.Response(200, json=body)
        if request.headers.get("authorization") == f"Bearer {self.current_token}":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_auth(idp: FakeIdp, scopes: list[str] | None = None) -> ClientCredentialsAuth:
    return ClientCredentialsAuth(
        token_url="https://idp.example.com/token",
        client_id="cid",
        client_secret="csecret",
        scopes=scopes,
        transport=idp.transport(),
    )


def cc_record(**overrides: Any) -> ServerRecord:
    fields: dict[str, Any] = {
        "id": "srv-1",
        "name": "weather",
        "url": "https://example.com/mcp",
        "auth": ServerAuth.OAUTH_CLIENT_CREDENTIALS,
        "oauth_token_url": "https://idp.example.com/token",
        "oauth_client_id": "cid",
        "oauth_client_secret": "${env:CC_SECRET}",
    }
    fields.update(overrides)
    return ServerRecord(**fields)


# ---------------------------------------------------------------------------
# ClientCredentialsAuth
# ---------------------------------------------------------------------------


async def test_fetches_token_and_authorizes_request() -> None:
    idp = FakeIdp()
    auth = make_auth(idp)
    async with httpx.AsyncClient(transport=idp.transport(), auth=auth) as client:
        response = await client.get("https://api.example.com/data")
    assert response.status_code == 200
    assert idp.token_requests[0]["grant_type"] == "client_credentials"
    assert idp.token_requests[0]["client_id"] == "cid"
    assert idp.token_requests[0]["client_secret"] == "csecret"


async def test_token_is_cached_across_requests() -> None:
    idp = FakeIdp()
    auth = make_auth(idp)
    async with httpx.AsyncClient(transport=idp.transport(), auth=auth) as client:
        await client.get("https://api.example.com/data")
        await client.get("https://api.example.com/data")
    assert idp.issued == 1


async def test_401_forces_one_refresh() -> None:
    idp = FakeIdp()
    auth = make_auth(idp)
    async with httpx.AsyncClient(transport=idp.transport(), auth=auth) as client:
        await client.get("https://api.example.com/data")
        idp.issued += 1
        response = await client.get("https://api.example.com/data")
    assert response.status_code == 200
    assert len(idp.token_requests) == 2


async def test_scopes_sent_space_joined() -> None:
    idp = FakeIdp()
    auth = make_auth(idp, scopes=["read", "write"])
    async with httpx.AsyncClient(transport=idp.transport(), auth=auth) as client:
        await client.get("https://api.example.com/data")
    assert idp.token_requests[0]["scope"] == "read write"


async def test_token_endpoint_error_raises() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    auth = ClientCredentialsAuth("https://idp/token", "cid", "cs", transport=transport)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await auth._get_token()


async def test_missing_access_token_raises() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1}))
    auth = ClientCredentialsAuth("https://idp/token", "cid", "cs", transport=transport)
    with pytest.raises(RuntimeError, match="access_token"):
        await auth._get_token()


async def test_missing_expires_in_uses_default() -> None:
    idp = FakeIdp()
    idp.expires_in = None
    auth = make_auth(idp)
    async with httpx.AsyncClient(transport=idp.transport(), auth=auth) as client:
        await client.get("https://api.example.com/data")
        await client.get("https://api.example.com/data")
    assert idp.issued == 1


def test_sync_flow_is_rejected() -> None:
    auth = ClientCredentialsAuth("https://idp/token", "cid", "cs")
    request = httpx.Request("GET", "https://api.example.com")
    with pytest.raises(RuntimeError, match="async-only"):
        auth.sync_auth_flow(request)


# ---------------------------------------------------------------------------
# build_client_credentials + secret-ref resolution
# ---------------------------------------------------------------------------


def test_build_resolves_secret_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SECRET", "resolved-secret")
    auth = build_client_credentials(cc_record())
    assert auth._client_secret == "resolved-secret"
    assert auth._client_id == "cid"


def test_build_missing_fields_raises() -> None:
    srv = cc_record(auth=ServerAuth.NONE, oauth_token_url=None)
    with pytest.raises(ValueError, match="missing"):
        build_client_credentials(srv)


# ---------------------------------------------------------------------------
# client_credentials_hook (standalone, no plugin needed)
# ---------------------------------------------------------------------------


async def test_hook_skips_non_cc_servers() -> None:
    hook = client_credentials_hook()
    srv = ServerRecord(id="s", name="plain", url="https://example.com/mcp")
    assert await hook(ConnectContext(server=srv)) is None


async def test_hook_attaches_and_caches_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SECRET", "s1")
    hook = client_credentials_hook()
    ctx = ConnectContext(server=cc_record())

    first = await hook(ctx)
    second = await hook(ctx)
    assert first is not None
    assert second is not None
    assert isinstance(first.auth, ClientCredentialsAuth)
    assert first.auth is second.auth


async def test_hook_rebuilds_provider_when_config_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CC_SECRET", "s1")
    monkeypatch.setenv("CC_SECRET_2", "s2")
    hook = client_credentials_hook()

    first = await hook(ConnectContext(server=cc_record()))
    second = await hook(ConnectContext(server=cc_record(oauth_client_secret="${env:CC_SECRET_2}")))
    assert first is not None
    assert second is not None
    assert first.auth is not second.auth
    assert isinstance(second.auth, ClientCredentialsAuth)
    assert second.auth._client_secret == "s2"


async def test_oauth_plugin_attaches_client_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CC_SECRET", "s1")
    plugin = OAuthPlugin()
    settings = await plugin._attach_oauth(ConnectContext(server=cc_record()))
    assert settings is not None
    assert isinstance(settings.auth, ClientCredentialsAuth)


# ---------------------------------------------------------------------------
# Registry validation rules
# ---------------------------------------------------------------------------


def test_create_requires_cc_fields() -> None:
    with pytest.raises(ValidationError, match="oauth_token_url"):
        ServerCreate(
            name="weather",
            url="https://example.com/mcp",
            auth=ServerAuth.OAUTH_CLIENT_CREDENTIALS,
        )


def test_create_rejects_raw_secret() -> None:
    with pytest.raises(ValidationError, match="reference"):
        ServerCreate(
            name="weather",
            url="https://example.com/mcp",
            auth=ServerAuth.OAUTH_CLIENT_CREDENTIALS,
            oauth_token_url="https://idp/token",
            oauth_client_id="cid",
            oauth_client_secret="raw-plaintext-secret",
        )


def test_create_accepts_secret_ref() -> None:
    srv = ServerCreate(
        name="weather",
        url="https://example.com/mcp",
        auth=ServerAuth.OAUTH_CLIENT_CREDENTIALS,
        oauth_token_url="https://idp/token",
        oauth_client_id="cid",
        oauth_client_secret="${env:CC_SECRET}",
    )
    assert srv.oauth_client_secret == "${env:CC_SECRET}"


def test_non_cc_auth_needs_no_cc_fields() -> None:
    srv = ServerCreate(name="weather", url="https://example.com/mcp")
    assert srv.oauth_token_url is None
