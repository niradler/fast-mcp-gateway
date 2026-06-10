"""Gateway and policy configuration loaded from JSON files.

A gateway config is a JSON object whose keys map to :class:`GatewayConfig`, with
nested ``policy`` and ``hil`` objects. A policy file is a JSON object mapping to
:class:`LocalPolicy` (either at the top level or under a ``"policy"`` key); when a
config sets ``policy_file`` it overrides any inline ``policy`` object.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

DEFAULT_CONFIG_DIR = Path.home() / ".fast-gateway"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "gateway.json"


class HilConfig(BaseModel):
    """Human-in-the-loop timing and UI settings."""

    enabled: bool = Field(default=True, description="Whether HIL confirmation is active.")
    auto_open_browser: bool = Field(
        default=True, description="Open the approval URL in the default browser automatically."
    )
    timeout_seconds: float = Field(
        default=300.0, gt=0, description="Seconds to wait for a human decision before denying."
    )
    approval_base_url: str = Field(
        default="http://127.0.0.1:8000/admin/hil",
        description="Base URL rendered into the approval link shown to operators.",
    )


class LocalPolicy(BaseModel):
    """Inline tool-call policy expressed as glob patterns."""

    deny: list[str] = Field(
        default_factory=list,
        description="Glob patterns of namespaced tool names that are hard-denied.",
    )
    confirm: list[str] = Field(
        default_factory=list,
        description="Glob patterns of tool names that require human confirmation.",
    )
    audit: bool = Field(default=True, description="Emit an audit log entry for every tool call.")


class GatewayConfig(BaseModel):
    """Top-level gateway configuration."""

    name: str = Field(default="MCP Gateway", description="Display name shown in the MCP manifest.")
    db: str = Field(default="gateway.db", description="Path to the SQLite registry database.")
    host: str = Field(default="127.0.0.1", description="Bind host for the uvicorn server.")
    port: int = Field(default=8000, description="Bind port for the uvicorn server.")
    mcp_path: str = Field(default="/mcp", description="Mount path for the MCP ASGI sub-app.")
    admin_prefix: str = Field(default="/admin", description="URL prefix for the admin router.")
    admin_token: str | None = Field(
        default=None, description="Bearer token that protects the admin API; None disables auth."
    )
    policy_file: str | None = Field(
        default=None,
        description="Path to a separate JSON policy file; overrides any inline policy object.",
    )
    oauth_token_dir: str | None = Field(
        default=None,
        description="Persistent OAuth token cache dir; defaults to ~/.fast-gateway/oauth.",
    )
    startup_catalog: Literal["refresh", "background", "skip"] = Field(
        default="background",
        description=(
            "Upstream introspection at startup. 'background' (default) starts serving "
            "immediately — mounts are live, tools/list uses the last-known catalog — and "
            "refreshes in a background task, so boot time no longer scales with slow or "
            "dead upstreams. 'refresh' blocks startup until every upstream answered "
            "(the pre-0.0.4 behavior); 'skip' defers to an explicit /reload."
        ),
    )
    policy: LocalPolicy = Field(default_factory=LocalPolicy)
    hil: HilConfig = Field(default_factory=HilConfig)


def _read_json_object(path: Path) -> dict[str, object]:
    """Read *path* as a JSON object, raising ``FileNotFoundError`` if it is missing."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as fh:
        return cast(dict[str, object], json.load(fh))


def load_policy(path: str | Path) -> LocalPolicy:
    """Load a :class:`LocalPolicy` from a JSON file.

    Accepts the policy fields at the top level or nested under a ``"policy"`` key;
    when both are present, the nested object wins.
    """
    data = _read_json_object(Path(path))
    nested = data.get("policy")
    raw = cast(dict[str, object], nested) if isinstance(nested, dict) else data
    return LocalPolicy.model_validate(raw)


def apply_oauth_token_dir(cfg: GatewayConfig) -> None:
    """Set ``FAST_GATEWAY_OAUTH_DIR`` when *cfg* names a custom token directory.

    Both ``factory.build_app`` and the CLI ``login``/``logout`` commands call this so
    the daemon and the CLI always agree on which directory holds the token cache,
    regardless of where the token-dir was configured.
    """
    if cfg.oauth_token_dir is not None:
        os.environ["FAST_GATEWAY_OAUTH_DIR"] = cfg.oauth_token_dir


def load_config(path: str | Path | None) -> GatewayConfig:
    """Load :class:`GatewayConfig` from a JSON file, or all-defaults when *path* is None.

    Relative ``db`` and ``policy_file`` paths are resolved against the config file's
    directory so a config in ``~/.fast-gateway/`` writes its sqlite db there too.
    When ``policy_file`` is set it replaces any inline ``policy`` object.
    """
    if path is None:
        return GatewayConfig()
    cfg_path = Path(path).expanduser().resolve()
    data = _read_json_object(cfg_path)
    cfg = GatewayConfig.model_validate(data)
    base_dir = cfg_path.parent
    updates: dict[str, object] = {}
    db_path = Path(cfg.db).expanduser()
    if not db_path.is_absolute():
        updates["db"] = str((base_dir / db_path).resolve())
    if cfg.policy_file is not None:
        policy_path = Path(cfg.policy_file).expanduser()
        if not policy_path.is_absolute():
            policy_path = (base_dir / policy_path).resolve()
        updates["policy"] = load_policy(policy_path)
    if updates:
        cfg = cfg.model_copy(update=updates)
    return cfg


_DEFAULT_CONFIG_JSON: dict[str, object] = {
    "name": "MCP Gateway",
    "db": "gateway.db",
    "host": "127.0.0.1",
    "port": 8000,
    "mcp_path": "/mcp",
    "admin_prefix": "/admin",
    "admin_token": None,
    "policy_file": None,
    "oauth_token_dir": None,
    "startup_catalog": "background",
    "policy": {
        "deny": [],
        "confirm": ["*_delete_*", "*_write_*", "*_purge_*", "*_remove_*"],
        "audit": True,
    },
    "hil": {
        "enabled": True,
        "auto_open_browser": True,
        "timeout_seconds": 300,
        "approval_base_url": "http://127.0.0.1:8000/admin/hil",
    },
}


def write_default_config(path: Path) -> None:
    """Write the bundled default gateway config to *path* (parents created as needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(_DEFAULT_CONFIG_JSON, fh, indent=2)
        fh.write("\n")


def resolve_config_path(explicit: str | Path | None) -> tuple[Path, bool]:
    """Resolve which config file to use, returning ``(path, created)``.

    Resolution order: ``explicit`` argument > ``$FAST_GATEWAY_CONFIG`` env var >
    ``./gateway.json`` if it exists > ``~/.fast-gateway/gateway.json``. The last
    fallback is auto-created with the bundled defaults if it doesn't exist;
    ``created`` is ``True`` in that case so callers can print a one-line notice.
    """
    if explicit is not None:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        return p, False
    env = os.environ.get("FAST_GATEWAY_CONFIG")
    if env:
        p = Path(env).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Config file from FAST_GATEWAY_CONFIG not found: {p}")
        return p, False
    cwd_cfg = Path.cwd() / "gateway.json"
    if cwd_cfg.exists():
        return cwd_cfg.resolve(), False
    default = DEFAULT_CONFIG_PATH
    if default.exists():
        return default, False
    write_default_config(default)
    return default, True
