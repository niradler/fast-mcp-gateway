"""Tests for model schemas, focusing on the ServerAuth/oauth_scopes additions."""

from __future__ import annotations

from fast_gateway.models import (
    ServerAuth,
    ServerBase,
    ServerCreate,
    ServerPatch,
    Transport,
)


def test_server_auth_default_is_none() -> None:
    record = ServerCreate(name="weather", url="https://example.com/mcp")
    assert record.auth is ServerAuth.NONE


def test_oauth_scopes_default_is_empty() -> None:
    record = ServerCreate(name="weather", url="https://example.com/mcp")
    assert record.oauth_scopes == []


def test_server_auth_enum_values() -> None:
    assert ServerAuth.NONE.value == "none"
    assert ServerAuth.OAUTH.value == "oauth"


def test_server_create_with_oauth_roundtrips() -> None:
    record = ServerCreate(
        name="datadog",
        url="https://api.datadoghq.com/mcp",
        transport=Transport.HTTP,
        auth=ServerAuth.OAUTH,
        oauth_scopes=["user", "read:metrics"],
    )
    assert record.auth is ServerAuth.OAUTH
    assert record.oauth_scopes == ["user", "read:metrics"]

    dumped = record.model_dump(mode="json")
    assert dumped["auth"] == "oauth"
    assert dumped["oauth_scopes"] == ["user", "read:metrics"]

    loaded = ServerCreate.model_validate(dumped)
    assert loaded.auth is ServerAuth.OAUTH
    assert loaded.oauth_scopes == ["user", "read:metrics"]


def test_server_patch_auth_defaults_none() -> None:
    patch = ServerPatch()
    assert patch.auth is None
    assert patch.oauth_scopes is None


def test_server_patch_auth_can_be_set() -> None:
    patch = ServerPatch(auth=ServerAuth.OAUTH, oauth_scopes=["admin"])
    assert patch.auth is ServerAuth.OAUTH
    assert patch.oauth_scopes == ["admin"]


def test_server_base_auth_field_description_exists() -> None:
    field_info = ServerBase.model_fields["auth"]
    assert field_info.description is not None
    assert len(field_info.description) > 0
