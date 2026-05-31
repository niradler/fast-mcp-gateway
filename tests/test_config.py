"""Tests for config loading: defaults, full parse, policy_file override, and error cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from fast_gateway.config import GatewayConfig, HilConfig, LocalPolicy, load_config, load_policy


def write_json(tmp_path: Path, name: str, data: dict[str, Any]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_config_none_returns_defaults() -> None:
    cfg = load_config(None)
    assert isinstance(cfg, GatewayConfig)
    assert cfg.name == "MCP Gateway"
    assert cfg.db == "gateway.db"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
    assert cfg.mcp_path == "/mcp"
    assert cfg.admin_prefix == "/admin"
    assert cfg.admin_token is None
    assert cfg.policy_file is None
    assert cfg.oauth_token_dir is None
    assert isinstance(cfg.policy, LocalPolicy)
    assert isinstance(cfg.hil, HilConfig)


def test_load_config_full_json(tmp_path: Path) -> None:
    f = write_json(
        tmp_path,
        "gateway.json",
        {
            "name": "My Gateway",
            "db": "custom.db",
            "host": "0.0.0.0",
            "port": 9000,
            "mcp_path": "/mcp/v1",
            "admin_prefix": "/ops",
            "admin_token": "secret",
            "policy": {"deny": ["math_*"], "confirm": ["deploy_*"], "audit": False},
            "hil": {
                "enabled": False,
                "timeout_seconds": 60.0,
                "approval_base_url": "http://myhost:9000/hil",
                "auto_open_browser": False,
            },
        },
    )
    cfg = load_config(f)
    assert cfg.name == "My Gateway"
    assert cfg.db == "custom.db"
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.mcp_path == "/mcp/v1"
    assert cfg.admin_prefix == "/ops"
    assert cfg.admin_token == "secret"
    assert cfg.policy.deny == ["math_*"]
    assert cfg.policy.confirm == ["deploy_*"]
    assert cfg.policy.audit is False
    assert cfg.hil.enabled is False
    assert cfg.hil.timeout_seconds == 60.0
    assert cfg.hil.approval_base_url == "http://myhost:9000/hil"
    assert cfg.hil.auto_open_browser is False


def test_load_config_partial_keys(tmp_path: Path) -> None:
    f = write_json(tmp_path, "gateway.json", {"name": "Flat Gateway", "port": 7777})
    cfg = load_config(f)
    assert cfg.name == "Flat Gateway"
    assert cfg.port == 7777
    assert cfg.db == "gateway.db"


def test_load_config_policy_file_wins_over_inline(tmp_path: Path) -> None:
    policy_file = write_json(
        tmp_path,
        "policy.json",
        {"deny": ["blocked_*"], "confirm": ["risky_*"], "audit": False},
    )
    gateway_file = write_json(
        tmp_path,
        "gateway.json",
        {
            "name": "Test",
            "policy_file": policy_file.as_posix(),
            "policy": {"deny": ["inline_deny"], "confirm": [], "audit": True},
        },
    )
    cfg = load_config(gateway_file)
    assert cfg.policy.deny == ["blocked_*"]
    assert cfg.policy.confirm == ["risky_*"]
    assert cfg.policy.audit is False


def test_load_config_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/path/gateway.json"))


def test_load_config_bad_value_raises(tmp_path: Path) -> None:
    f = write_json(tmp_path, "bad.json", {"port": "not_an_int"})
    with pytest.raises(ValidationError):
        load_config(f)


def test_load_policy_top_level(tmp_path: Path) -> None:
    f = write_json(
        tmp_path,
        "policy.json",
        {"deny": ["risky_*", "admin_*"], "confirm": ["deploy_*"], "audit": True},
    )
    policy = load_policy(f)
    assert policy.deny == ["risky_*", "admin_*"]
    assert policy.confirm == ["deploy_*"]
    assert policy.audit is True


def test_load_policy_nested_object(tmp_path: Path) -> None:
    f = write_json(
        tmp_path,
        "policy.json",
        {"policy": {"deny": ["secret_*"], "confirm": [], "audit": False}},
    )
    policy = load_policy(f)
    assert policy.deny == ["secret_*"]
    assert policy.audit is False


def test_load_policy_empty_object_returns_defaults(tmp_path: Path) -> None:
    f = write_json(tmp_path, "empty.json", {})
    policy = load_policy(f)
    assert policy.deny == []
    assert policy.confirm == []
    assert policy.audit is True


def test_load_policy_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_policy(Path("/no/such/policy.json"))


def test_hil_config_defaults() -> None:
    hil = HilConfig()
    assert hil.enabled is True
    assert hil.auto_open_browser is True
    assert hil.timeout_seconds == 300.0
    assert hil.approval_base_url == "http://127.0.0.1:8000/admin/hil"


def test_hil_config_invalid_timeout() -> None:
    with pytest.raises(ValidationError):
        HilConfig(timeout_seconds=0)


def test_local_policy_defaults() -> None:
    p = LocalPolicy()
    assert p.deny == []
    assert p.confirm == []
    assert p.audit is True


def test_load_config_string_path(tmp_path: Path) -> None:
    f = write_json(tmp_path, "gateway.json", {"port": 1234})
    cfg = load_config(str(f))
    assert cfg.port == 1234
