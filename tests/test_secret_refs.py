"""Tests for ${env:}/${file:} secret references and their resolution at connect time."""

from __future__ import annotations

from pathlib import Path

import pytest

from fast_gateway.connect import resolve_connect_settings
from fast_gateway.hooks import ConnectContext, ConnectSettings, Hooks
from fast_gateway.models import ServerRecord
from fast_gateway.secret_refs import (
    SecretResolutionError,
    contains_secret_ref,
    resolve_header_refs,
    resolve_secret_refs,
)

# ---------------------------------------------------------------------------
# resolve_secret_refs / contains_secret_ref
# ---------------------------------------------------------------------------


def test_plain_value_passes_through() -> None:
    assert resolve_secret_refs("just-a-value") == "just-a-value"


def test_env_ref_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "sk-live-123")
    assert resolve_secret_refs("${env:MY_TOKEN}") == "sk-live-123"


def test_env_ref_embedded_in_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "sk-live-123")
    assert resolve_secret_refs("Bearer ${env:MY_TOKEN}") == "Bearer sk-live-123"


def test_multiple_refs_in_one_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("PASS", "s3cret")
    assert resolve_secret_refs("${env:USER}:${env:PASS}") == "alice:s3cret"


def test_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    with pytest.raises(SecretResolutionError, match="DOES_NOT_EXIST"):
        resolve_secret_refs("${env:DOES_NOT_EXIST}")


def test_file_ref_resolves_and_strips(tmp_path: Path) -> None:
    secret_file = tmp_path / "token"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    ref = "${file:" + str(secret_file) + "}"
    assert resolve_secret_refs(ref) == "file-secret"


def test_missing_file_raises(tmp_path: Path) -> None:
    ref = "${file:" + str(tmp_path / "absent") + "}"
    with pytest.raises(SecretResolutionError):
        resolve_secret_refs(ref)


def test_contains_secret_ref() -> None:
    assert contains_secret_ref("Bearer ${env:X}") is True
    assert contains_secret_ref("${file:/run/secret}") is True
    assert contains_secret_ref("Bearer raw-token") is False
    assert contains_secret_ref("${unknown:X}") is False


def test_resolve_header_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "k-1")
    headers = {"X-Api-Key": "${env:API_KEY}", "X-Plain": "v"}
    assert resolve_header_refs(headers) == {"X-Api-Key": "k-1", "X-Plain": "v"}


# ---------------------------------------------------------------------------
# Resolution at connect time
# ---------------------------------------------------------------------------


def record(static_headers: dict[str, str]) -> ServerRecord:
    return ServerRecord(
        id="abc",
        name="weather",
        url="https://example.com/mcp",
        static_headers=static_headers,
    )


async def test_static_header_refs_resolved_at_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPSTREAM_TOKEN", "sk-9")
    srv = record({"Authorization": "Bearer ${env:UPSTREAM_TOKEN}"})
    headers, _, _ = await resolve_connect_settings(srv, Hooks())
    assert headers == {"Authorization": "Bearer sk-9"}


async def test_unresolvable_ref_fails_loudly_at_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    srv = record({"Authorization": "Bearer ${env:NOPE}"})
    with pytest.raises(SecretResolutionError):
        await resolve_connect_settings(srv, Hooks())


async def test_hook_headers_are_passed_through_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)

    async def add_header(ctx: ConnectContext) -> ConnectSettings:
        return ConnectSettings(headers={"X-Hook": "${env:NOPE}"})

    srv = record({})
    headers, _, _ = await resolve_connect_settings(srv, Hooks(pre_mcp_connect=[add_header]))
    assert headers == {"X-Hook": "${env:NOPE}"}
