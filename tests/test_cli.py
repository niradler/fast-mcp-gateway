"""Tests for the fast-gateway CLI using typer.testing.CliRunner with a MockTransport."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from fast_gateway.cli import app

runner = CliRunner()

_SERVER_RECORD: dict[str, Any] = {
    "id": "srv-001",
    "name": "weather",
    "transport": "http",
    "url": "https://example.com/mcp",
    "enabled": True,
    "allow": [],
    "deny": [],
    "static_headers": {},
    "timeout_seconds": 30.0,
    "tags": [],
    "auth": "none",
    "oauth_scopes": [],
}

_GROUP_RECORD: dict[str, Any] = {
    "id": "grp-001",
    "name": "prod",
    "member_server_ids": [],
    "allow": [],
    "deny": [],
}


def _make_handler(
    routes: dict[tuple[str, str], tuple[int, Any]],
) -> Any:
    """Return an httpx transport handler that dispatches by (method, url-path)."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        if key in routes:
            status, body = routes[key]
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": "not found in mock"})

    return handler


def _make_mock_client(
    routes: dict[tuple[str, str], tuple[int, Any]], base_url: str = "http://127.0.0.1:8000"
) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(_make_handler(routes)),
        base_url=base_url,
    )


@pytest.fixture
def mock_client_factory() -> Generator[MagicMock, None, None]:
    with patch("fast_gateway.cli.make_client") as mock_factory:
        yield mock_factory


def test_add_posts_server_and_reloads(mock_client_factory: MagicMock) -> None:
    routes = {
        ("POST", "/admin/servers"): (201, _SERVER_RECORD),
        ("POST", "/admin/reload"): (200, {"status": "reloaded"}),
    }
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        key = (req.method, req.url.path)
        if key in routes:
            status, body = routes[key]
            return httpx.Response(status, json=body)
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:8000",
    )
    mock_client_factory.return_value = client

    result = runner.invoke(app, ["add", "weather", "https://example.com/mcp"])
    assert result.exit_code == 0, result.output
    assert "weather" in result.output

    post_req = next(r for r in requests_seen if r.method == "POST" and "/servers" in r.url.path)
    body = json.loads(post_req.content)
    assert body["name"] == "weather"
    assert body["url"] == "https://example.com/mcp"
    assert body["transport"] == "http"

    reload_req = next(r for r in requests_seen if r.method == "POST" and "/reload" in r.url.path)
    assert reload_req is not None


def test_add_sends_bearer_token(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "POST" and "/servers" in req.url.path:
            return httpx.Response(201, json=_SERVER_RECORD)
        if req.method == "POST" and "/reload" in req.url.path:
            return httpx.Response(200, json={"status": "reloaded"})
        return httpx.Response(404)

    def factory(url: str, token: str | None) -> httpx.Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url=url,
            headers=headers,
        )

    mock_client_factory.side_effect = factory

    result = runner.invoke(
        app,
        ["add", "weather", "https://example.com/mcp", "--admin-token", "mytoken"],
    )
    assert result.exit_code == 0, result.output
    post_req = next(r for r in requests_seen if r.method == "POST" and "/servers" in r.url.path)
    assert post_req.headers.get("authorization") == "Bearer mytoken"


def test_add_with_header_option(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "POST" and "/servers" in req.url.path:
            return httpx.Response(201, json=_SERVER_RECORD)
        return httpx.Response(200, json={"status": "reloaded"})

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(
        app,
        ["add", "weather", "https://example.com/mcp", "--header", "X-Token=abc123"],
    )
    assert result.exit_code == 0, result.output
    post_req = next(r for r in requests_seen if r.method == "POST" and "/servers" in r.url.path)
    body = json.loads(post_req.content)
    assert body["static_headers"] == {"X-Token": "abc123"}


def test_add_malformed_header_errors(mock_client_factory: MagicMock) -> None:
    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        base_url="http://127.0.0.1:8000",
    )
    result = runner.invoke(
        app, ["add", "weather", "https://example.com/mcp", "--header", "BADHEADER"]
    )
    assert result.exit_code != 0
    assert "malformed header" in result.output.lower() or "malformed" in result.output


def test_add_409_shows_friendly_message(mock_client_factory: MagicMock) -> None:
    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(409, json={"detail": "conflict"})),
        base_url="http://127.0.0.1:8000",
    )
    result = runner.invoke(app, ["add", "weather", "https://example.com/mcp", "--no-reload"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_list_formats_rows(mock_client_factory: MagicMock) -> None:
    servers = [
        _SERVER_RECORD,
        {**_SERVER_RECORD, "id": "srv-002", "name": "docs", "transport": "sse"},
    ]
    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=servers)),
        base_url="http://127.0.0.1:8000",
    )
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "weather" in result.output
    assert "docs" in result.output
    assert "https://example.com/mcp" in result.output


def test_remove_resolves_name_to_id(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "GET" and req.url.path.endswith("/weather"):
            return httpx.Response(404)
        if req.method == "GET" and req.url.path.endswith("/servers"):
            return httpx.Response(200, json=[_SERVER_RECORD])
        if req.method == "DELETE":
            return httpx.Response(204)
        if req.method == "POST" and "/reload" in req.url.path:
            return httpx.Response(200, json={"status": "reloaded"})
        return httpx.Response(404)

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(app, ["remove", "weather"])
    assert result.exit_code == 0, result.output
    delete_req = next(r for r in requests_seen if r.method == "DELETE")
    assert "srv-001" in delete_req.url.path


def test_enable_patches_enabled_true(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "GET" and req.url.path.endswith("/weather"):
            return httpx.Response(200, json=_SERVER_RECORD)
        if req.method == "PATCH":
            return httpx.Response(200, json={**_SERVER_RECORD, "enabled": True})
        if req.method == "POST" and "/reload" in req.url.path:
            return httpx.Response(200, json={"status": "reloaded"})
        return httpx.Response(404)

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(app, ["enable", "weather"])
    assert result.exit_code == 0, result.output
    patch_req = next(r for r in requests_seen if r.method == "PATCH")
    assert json.loads(patch_req.content)["enabled"] is True


def test_disable_patches_enabled_false(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "GET" and req.url.path.endswith("/weather"):
            return httpx.Response(200, json=_SERVER_RECORD)
        if req.method == "PATCH":
            return httpx.Response(200, json={**_SERVER_RECORD, "enabled": False})
        if req.method == "POST" and "/reload" in req.url.path:
            return httpx.Response(200, json={"status": "reloaded"})
        return httpx.Response(404)

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(app, ["disable", "weather"])
    assert result.exit_code == 0, result.output
    patch_req = next(r for r in requests_seen if r.method == "PATCH")
    assert json.loads(patch_req.content)["enabled"] is False


def test_connect_output_contains_mcp_add_and_json(mock_client_factory: MagicMock) -> None:
    result = runner.invoke(app, ["connect", "--gateway-url", "http://myhost:9000"])
    assert result.exit_code == 0, result.output
    assert "claude mcp add --transport http gateway http://myhost:9000/mcp/" in result.output
    assert '"url": "http://myhost:9000/mcp/"' in result.output


def test_connect_custom_name_and_path(mock_client_factory: MagicMock) -> None:
    result = runner.invoke(
        app,
        [
            "connect",
            "--gateway-url",
            "http://gw:8000",
            "--name",
            "myagent",
            "--mcp-path",
            "/v2/mcp",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "myagent" in result.output
    assert "http://gw:8000/v2/mcp/" in result.output


def test_info_prints_endpoints() -> None:
    result = runner.invoke(
        app,
        [
            "info",
            "--gateway-url",
            "http://gw:8000",
            "--mcp-path",
            "/mcp",
            "--admin-prefix",
            "/admin",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "http://gw:8000/mcp/" in result.output
    assert "http://gw:8000/admin" in result.output
    assert "http://gw:8000/docs" in result.output
    assert "http://gw:8000/admin/hil" in result.output


def test_serve_calls_uvicorn_with_resolved_host_port(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with (
        patch("fast_gateway.cli.uvicorn") as mock_uvicorn,
        patch("fast_gateway.cli.build_app") as mock_build,
    ):
        mock_build.return_value = MagicMock()
        result = runner.invoke(
            app,
            ["serve", "--host", "0.0.0.0", "--port", "9999", "--db", str(db_path)],
        )
        assert result.exit_code == 0, result.output
        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs[1]["host"] == "0.0.0.0"
        assert call_kwargs[1]["port"] == 9999


def test_serve_config_overrides_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    captured: dict[str, Any] = {}

    def fake_build(cfg: Any) -> MagicMock:
        captured["cfg"] = cfg
        return MagicMock()

    with (
        patch("fast_gateway.cli.uvicorn"),
        patch("fast_gateway.cli.build_app", side_effect=fake_build),
    ):
        runner.invoke(
            app,
            ["serve", "--host", "0.0.0.0", "--port", "7777", "--db", str(db_path)],
        )
        cfg = captured.get("cfg")
        assert cfg is not None
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 7777
        assert cfg.db == str(db_path)


def test_reload_command(mock_client_factory: MagicMock) -> None:
    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "reloaded"})),
        base_url="http://127.0.0.1:8000",
    )
    result = runner.invoke(app, ["reload"])
    assert result.exit_code == 0, result.output
    assert "reloaded" in result.output


# ---------------------------------------------------------------------------
# OAuth add flags
# ---------------------------------------------------------------------------


def test_add_oauth_posts_auth_oauth(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    oauth_record = {**_SERVER_RECORD, "auth": "oauth", "oauth_scopes": ["user"]}

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "POST" and "/servers" in req.url.path:
            return httpx.Response(201, json=oauth_record)
        return httpx.Response(200, json={"status": "reloaded"})

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(
        app,
        ["add", "weather", "https://example.com/mcp", "--oauth", "--scope", "user"],
    )
    assert result.exit_code == 0, result.output
    post_req = next(r for r in requests_seen if r.method == "POST" and "/servers" in r.url.path)
    body = json.loads(post_req.content)
    assert body["auth"] == "oauth"
    assert body["oauth_scopes"] == ["user"]


def test_add_no_oauth_posts_auth_none(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "POST" and "/servers" in req.url.path:
            return httpx.Response(201, json=_SERVER_RECORD)
        return httpx.Response(200, json={"status": "reloaded"})

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(app, ["add", "weather", "https://example.com/mcp"])
    assert result.exit_code == 0, result.output
    post_req = next(r for r in requests_seen if r.method == "POST" and "/servers" in r.url.path)
    body = json.loads(post_req.content)
    assert body["auth"] == "none"
    assert body["oauth_scopes"] == []


def test_add_multiple_scopes(mock_client_factory: MagicMock) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_seen.append(req)
        if req.method == "POST" and "/servers" in req.url.path:
            return httpx.Response(
                201, json={**_SERVER_RECORD, "auth": "oauth", "oauth_scopes": ["user", "admin"]}
            )
        return httpx.Response(200, json={"status": "reloaded"})

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(
        app,
        [
            "add",
            "weather",
            "https://example.com/mcp",
            "--oauth",
            "--scope",
            "user",
            "--scope",
            "admin",
        ],
    )
    assert result.exit_code == 0, result.output
    post_req = next(r for r in requests_seen if r.method == "POST" and "/servers" in r.url.path)
    body = json.loads(post_req.content)
    assert body["oauth_scopes"] == ["user", "admin"]


# ---------------------------------------------------------------------------
# login command
# ---------------------------------------------------------------------------

_OAUTH_SERVER_RECORD: dict[str, Any] = {
    "id": "srv-001",
    "name": "datadog",
    "transport": "http",
    "url": "https://api.datadoghq.com/mcp",
    "enabled": True,
    "allow": [],
    "deny": [],
    "static_headers": {},
    "timeout_seconds": 30.0,
    "tags": [],
    "auth": "oauth",
    "oauth_scopes": ["user", "read:metrics"],
}


def test_login_resolves_server_and_calls_run_oauth_login(
    mock_client_factory: MagicMock, tmp_path: Path
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path.endswith("/datadog"):
            return httpx.Response(200, json=_OAUTH_SERVER_RECORD)
        return httpx.Response(404)

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    called_with: list[Any] = []

    def fake_run_oauth_login(server: Any) -> None:
        called_with.append(server)

    with patch("fast_gateway.cli.run_oauth_login", side_effect=fake_run_oauth_login):
        result = runner.invoke(app, ["login", "datadog"])

    assert result.exit_code == 0, result.output
    assert len(called_with) == 1
    srv = called_with[0]
    assert srv.url == "https://api.datadoghq.com/mcp"
    assert srv.auth.value == "oauth"
    assert srv.oauth_scopes == ["user", "read:metrics"]
    assert "complete" in result.output.lower() or "cached" in result.output.lower()


def test_login_server_not_found_exits_1(
    mock_client_factory: MagicMock,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path.endswith("/nope"):
            return httpx.Response(404)
        if req.method == "GET" and "/servers" in req.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(app, ["login", "nope"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# logout command
# ---------------------------------------------------------------------------


def test_logout_prints_token_dir_path(
    mock_client_factory: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path.endswith("/datadog"):
            return httpx.Response(200, json=_OAUTH_SERVER_RECORD)
        return httpx.Response(404)

    mock_client_factory.return_value = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8000"
    )

    result = runner.invoke(app, ["logout", "datadog"])
    assert result.exit_code == 0, result.output
    assert "token" in result.output.lower()
