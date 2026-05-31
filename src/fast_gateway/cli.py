"""``fast-gateway`` CLI — manage servers/groups via the admin API and serve the gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn
from fastmcp import Client
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from pydantic import ValidationError

from fast_gateway.config import apply_oauth_token_dir, load_config
from fast_gateway.factory import build_app
from fast_gateway.models import ServerAuth, ServerCreate, ServerRecord, Transport
from fast_gateway.plugins.oauth import build_oauth, default_oauth_token_dir

logger = logging.getLogger("fast_gateway.cli")

app = typer.Typer(name="fast-gateway", no_args_is_help=True)
group_app = typer.Typer(name="group", no_args_is_help=True)
app.add_typer(group_app, name="group")

_DEFAULT_URL = "http://127.0.0.1:8000"
_DEFAULT_PREFIX = "/admin"


def make_client(gateway_url: str, admin_token: str | None) -> httpx.Client:
    """Return an :class:`httpx.Client` pointed at *gateway_url* with optional bearer auth."""
    headers: dict[str, str] = {}
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"
    return httpx.Client(base_url=gateway_url, headers=headers)


def _resolve_url(gateway_url: str | None) -> str:
    return gateway_url or os.environ.get("FAST_GATEWAY_URL", _DEFAULT_URL)


def _resolve_token(admin_token: str | None) -> str | None:
    return admin_token or os.environ.get("FAST_GATEWAY_ADMIN_TOKEN")


def _resolve_prefix(admin_prefix: str | None) -> str:
    return admin_prefix or _DEFAULT_PREFIX


def _resolve_server_id(client: httpx.Client, prefix: str, name_or_id: str) -> str:
    """Return the server id matching *name_or_id*; tries direct GET first, then name lookup."""
    try:
        r = client.get(f"{prefix}/servers/{name_or_id}")
        if r.status_code == 200:
            return name_or_id
    except httpx.HTTPError:
        pass
    try:
        r = client.get(f"{prefix}/servers")
        r.raise_for_status()
        for srv in r.json():
            if srv.get("name") == name_or_id:
                return str(srv["id"])
    except httpx.HTTPError as exc:
        typer.echo(f"Error: could not list servers - {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Error: server {name_or_id!r} not found.")
    raise typer.Exit(1)


def run_oauth_login(server: ServerRecord) -> None:
    """Run the OAuth browser flow for *server* and cache the resulting tokens.

    Builds an OAuth-authenticated transport directly using ``build_oauth`` from the
    OAuth plugin, then wraps an async ``fastmcp.Client`` ping in ``asyncio.run`` so
    the CLI can call it synchronously. Exposed at module level as a seam for tests.
    """

    async def _login() -> None:
        oauth = build_oauth(server)
        headers = dict(server.static_headers)
        t: StreamableHttpTransport | SSETransport
        if server.transport is Transport.SSE:
            t = SSETransport(server.url, headers=headers, auth=oauth)
        else:
            t = StreamableHttpTransport(server.url, headers=headers, auth=oauth)
        async with Client(t) as c:
            await c.ping()

    asyncio.run(_login())


def _do_reload(client: httpx.Client, prefix: str) -> None:
    try:
        r = client.post(f"{prefix}/reload")
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.echo(f"Error: reload failed - {exc}", err=True)
        raise typer.Exit(1) from exc
    degraded: list[str] = r.json().get("degraded") or []
    if degraded:
        names = ", ".join(degraded)
        typer.echo(
            f"WARNING: {len(degraded)} server(s) failed introspection and may be serving "
            f"stale tools: {names}. If a server uses OAuth, run: fast-gateway login <name>.",
            err=True,
        )


def _resolve_group_id(client: httpx.Client, prefix: str, name_or_id: str) -> str:
    try:
        r = client.get(f"{prefix}/groups/{name_or_id}")
        if r.status_code == 200:
            return name_or_id
    except httpx.HTTPError:
        pass
    try:
        r = client.get(f"{prefix}/groups")
        r.raise_for_status()
        for grp in r.json():
            if grp.get("name") == name_or_id:
                return str(grp["id"])
    except httpx.HTTPError as exc:
        typer.echo(f"Error: could not list groups - {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Error: group {name_or_id!r} not found.")
    raise typer.Exit(1)


@app.command()
def serve(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    db: Annotated[str | None, typer.Option("--db")] = None,
) -> None:
    """Load config and run the gateway with uvicorn."""
    try:
        cfg = load_config(config)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if host is not None:
        cfg = cfg.model_copy(update={"host": host})
    if port is not None:
        cfg = cfg.model_copy(update={"port": port})
    if db is not None:
        cfg = cfg.model_copy(update={"db": db})

    base = f"http://{cfg.host}:{cfg.port}"
    typer.echo(f"MCP endpoint : {base}{cfg.mcp_path}/")
    typer.echo(f"Admin API    : {base}{cfg.admin_prefix}")
    typer.echo(f"OpenAPI docs : {base}/docs")

    gateway_app = build_app(cfg)
    uvicorn.run(gateway_app, host=cfg.host, port=cfg.port)


@app.command()
def add(
    name: Annotated[str, typer.Argument()],
    url: Annotated[str, typer.Argument()],
    transport: Annotated[str, typer.Option("--transport")] = "http",
    allow: Annotated[list[str], typer.Option("--allow")] = [],  # noqa: B006
    deny: Annotated[list[str], typer.Option("--deny")] = [],  # noqa: B006
    header: Annotated[list[str], typer.Option("--header")] = [],  # noqa: B006
    oauth: Annotated[bool, typer.Option("--oauth/--no-oauth")] = False,
    scope: Annotated[list[str], typer.Option("--scope")] = [],  # noqa: B006
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Register a new upstream server with the gateway."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    static_headers: dict[str, str] = {}
    for h in header:
        if "=" not in h:
            typer.echo(f"Error: malformed header {h!r}; expected KEY=VALUE.", err=True)
            raise typer.Exit(1)
        key, _, value = h.partition("=")
        static_headers[key.strip()] = value.strip()

    try:
        transport_enum = Transport(transport)
    except ValueError as exc:
        typer.echo(f"Error: invalid transport {transport!r}; choose http or sse.", err=True)
        raise typer.Exit(1) from exc

    auth_scheme = ServerAuth.OAUTH if oauth else ServerAuth.NONE
    payload = ServerCreate(
        name=name,
        url=url,
        transport=transport_enum,
        allow=list(allow),
        deny=list(deny),
        static_headers=static_headers,
        auth=auth_scheme,
        oauth_scopes=list(scope),
    )

    client = make_client(resolved_url, token)
    try:
        r = client.post(f"{prefix}/servers", json=payload.model_dump(mode="json"))
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc

    if r.status_code == 409:
        typer.echo(f"Error: server {name!r} already exists.")
        raise typer.Exit(1)
    if not r.is_success:
        typer.echo(f"Error: {r.status_code} {r.text}", err=True)
        raise typer.Exit(1)

    server = r.json()
    typer.echo(f"Created server {server['name']!r} (id={server['id']})")

    if not no_reload:
        _do_reload(client, prefix)


@app.command(name="list")
@app.command(name="ls")
def list_servers(
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """List all registered upstream servers."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    try:
        r = client.get(f"{prefix}/servers")
        r.raise_for_status()
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1) from exc

    servers = r.json()
    if not servers:
        typer.echo("No servers registered.")
        return

    typer.echo(f"{'NAME':<20} {'TRANSPORT':<10} {'ENABLED':<8} {'ALLOW':<15} {'DENY':<15} URL")
    typer.echo("-" * 100)
    for srv in servers:
        allow_str = ",".join(srv.get("allow") or []) or "*"
        deny_str = ",".join(srv.get("deny") or []) or "-"
        enabled = "yes" if srv.get("enabled") else "no"
        typer.echo(
            f"{srv['name']:<20} {srv['transport']:<10} {enabled:<8} "
            f"{allow_str:<15} {deny_str:<15} {srv['url']}"
        )


@app.command()
def remove(
    name_or_id: Annotated[str, typer.Argument()],
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Remove an upstream server by name or id."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    server_id = _resolve_server_id(client, prefix, name_or_id)

    try:
        r = client.delete(f"{prefix}/servers/{server_id}")
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc

    if r.status_code == 404:
        typer.echo(f"Error: server {name_or_id!r} not found.")
        raise typer.Exit(1)
    if not r.is_success:
        typer.echo(f"Error: {r.status_code} {r.text}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Removed server {name_or_id!r}")
    if not no_reload:
        _do_reload(client, prefix)


app.command(name="rm")(remove)


@app.command()
def enable(
    name: Annotated[str, typer.Argument()],
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Enable an upstream server by name or id."""
    _patch_enabled(name, True, no_reload, gateway_url, admin_token, admin_prefix)


@app.command()
def disable(
    name: Annotated[str, typer.Argument()],
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Disable an upstream server by name or id."""
    _patch_enabled(name, False, no_reload, gateway_url, admin_token, admin_prefix)


def _patch_enabled(
    name: str,
    enabled: bool,
    no_reload: bool,
    gateway_url: str | None,
    admin_token: str | None,
    admin_prefix: str | None,
) -> None:
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    server_id = _resolve_server_id(client, prefix, name)

    try:
        r = client.patch(f"{prefix}/servers/{server_id}", json={"enabled": enabled})
        r.raise_for_status()
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1) from exc

    state = "enabled" if enabled else "disabled"
    typer.echo(f"Server {name!r} {state}.")
    if not no_reload:
        _do_reload(client, prefix)


@app.command()
def reload(
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Trigger a live reload of the gateway's proxy mounts."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    try:
        r = client.post(f"{prefix}/reload")
        r.raise_for_status()
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1) from exc

    body = r.json()
    typer.echo(f"Reloaded: {body['status']}")
    degraded: list[str] = body.get("degraded") or []
    if degraded:
        names = ", ".join(degraded)
        typer.echo(
            f"WARNING: {len(degraded)} server(s) failed introspection and may be serving "
            f"stale tools: {names}. If a server uses OAuth, run: fast-gateway login <name>.",
            err=True,
        )


@app.command()
def connect(
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    mcp_path: Annotated[str, typer.Option("--mcp-path")] = "/mcp",
    name: Annotated[str, typer.Option("--name")] = "gateway",
) -> None:
    """Print the claude mcp add command and .mcp.json snippet to connect an agent."""
    resolved_url = _resolve_url(gateway_url)
    endpoint = f"{resolved_url.rstrip('/')}{mcp_path}/"
    mcp_json = {"mcpServers": {name: {"type": "http", "url": endpoint}}}
    typer.echo(f"claude mcp add --transport http {name} {endpoint}")
    typer.echo("")
    typer.echo(json.dumps(mcp_json, indent=2))
    typer.echo("")
    typer.echo(
        "Adding upstream servers later needs no agent reconfig - "
        "the gateway endpoint is stable and its tool list updates automatically."
    )


@app.command()
def info(
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    mcp_path: Annotated[str, typer.Option("--mcp-path")] = "/mcp",
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Print all resolved gateway endpoints."""
    resolved_url = _resolve_url(gateway_url)
    prefix = _resolve_prefix(admin_prefix)
    base = resolved_url.rstrip("/")
    typer.echo(f"MCP endpoint  : {base}{mcp_path}/")
    typer.echo(f"Admin API     : {base}{prefix}")
    typer.echo(f"OpenAPI docs  : {base}/docs")
    typer.echo(f"HIL approvals : {base}{prefix}/hil")


@app.command()
def login(
    name_or_id: Annotated[str, typer.Argument()],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Run the OAuth login flow for an upstream server and cache the tokens locally."""
    if config is not None:
        try:
            cfg = load_config(config)
        except FileNotFoundError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        apply_oauth_token_dir(cfg)

    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    server_id = _resolve_server_id(client, prefix, name_or_id)

    try:
        r = client.get(f"{prefix}/servers/{server_id}")
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.echo(f"Error: could not fetch server - {exc}", err=True)
        raise typer.Exit(1) from exc

    srv_data = r.json()
    try:
        server = ServerRecord.model_validate(srv_data)
    except ValidationError as exc:
        typer.echo(f"Error: unexpected server response - {exc}", err=True)
        raise typer.Exit(1) from exc

    if server.auth is not ServerAuth.OAUTH:
        typer.echo(
            f"Error: server '{server.name}' is not configured for OAuth"
            f" (auth={server.auth.value}).",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Logging in to '{server.name}' - a browser window will open...")
    try:
        run_oauth_login(server)
    except Exception as exc:
        typer.echo(f"Error: login failed - {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo("Login complete; tokens cached.")


@app.command()
def logout(
    name_or_id: Annotated[str, typer.Argument()],
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Clear cached OAuth tokens for an upstream server.

    Fetches the server record, builds a non-interactive OAuth provider, then clears
    the token cache entry for that server URL. The next gateway connect will require
    a fresh ``login`` to re-authorise.
    """
    if config is not None:
        try:
            cfg = load_config(config)
        except FileNotFoundError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        apply_oauth_token_dir(cfg)

    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    server_id = _resolve_server_id(client, prefix, name_or_id)

    try:
        r = client.get(f"{prefix}/servers/{server_id}")
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.echo(f"Error: could not fetch server - {exc}", err=True)
        raise typer.Exit(1) from exc

    srv_data = r.json()
    try:
        server = ServerRecord.model_validate(srv_data)
    except ValidationError as exc:
        typer.echo(f"Error: unexpected server response - {exc}", err=True)
        raise typer.Exit(1) from exc

    if server.auth is not ServerAuth.OAUTH:
        typer.echo(
            f"Error: server '{server.name}' is not configured for OAuth"
            f" (auth={server.auth.value}).",
            err=True,
        )
        raise typer.Exit(1)

    token_dir = default_oauth_token_dir()
    typer.echo(f"Token cache directory: {token_dir}")

    oauth = build_oauth(server, interactive=False)

    async def _logout() -> None:
        existed = await oauth.token_storage_adapter.get_tokens() is not None
        await oauth.token_storage_adapter.clear()
        if existed:
            typer.echo(f"Logged out '{server.name}'; cached tokens cleared.")
        else:
            typer.echo(f"No cached tokens for '{server.name}'; nothing to clear.")

    try:
        asyncio.run(_logout())
    except Exception as exc:
        typer.echo(f"Error: failed to clear tokens - {exc}", err=True)
        raise typer.Exit(1) from exc


@group_app.command(name="create")
def group_create(
    name: Annotated[str, typer.Argument()],
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Create a new server group."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    try:
        r = client.post(f"{prefix}/groups", json={"name": name})
        r.raise_for_status()
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1) from exc

    grp = r.json()
    typer.echo(f"Created group {grp['name']!r} (id={grp['id']})")
    if not no_reload:
        _do_reload(client, prefix)


@group_app.command(name="list")
@group_app.command(name="ls")
def group_list(
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """List all server groups."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    try:
        r = client.get(f"{prefix}/groups")
        r.raise_for_status()
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1) from exc

    groups = r.json()
    if not groups:
        typer.echo("No groups defined.")
        return

    typer.echo(f"{'NAME':<20} {'ID':<36} MEMBERS")
    typer.echo("-" * 80)
    for grp in groups:
        members = str(len(grp.get("member_server_ids") or []))
        typer.echo(f"{grp['name']:<20} {grp['id']:<36} {members}")


@group_app.command(name="rm")
def group_remove(
    name_or_id: Annotated[str, typer.Argument()],
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Remove a server group by name or id."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    group_id = _resolve_group_id(client, prefix, name_or_id)

    try:
        r = client.delete(f"{prefix}/groups/{group_id}")
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc

    if r.status_code == 404:
        typer.echo(f"Error: group {name_or_id!r} not found.")
        raise typer.Exit(1)
    if not r.is_success:
        typer.echo(f"Error: {r.status_code} {r.text}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Removed group {name_or_id!r}")
    if not no_reload:
        _do_reload(client, prefix)


@group_app.command(name="members")
def group_members(
    name: Annotated[str, typer.Argument()],
    server: Annotated[list[str], typer.Option("--server")] = [],  # noqa: B006
    no_reload: Annotated[bool, typer.Option("--no-reload")] = False,
    gateway_url: Annotated[str | None, typer.Option("--gateway-url")] = None,
    admin_token: Annotated[str | None, typer.Option("--admin-token")] = None,
    admin_prefix: Annotated[str | None, typer.Option("--admin-prefix")] = None,
) -> None:
    """Set the member servers of a group (replaces existing membership)."""
    resolved_url = _resolve_url(gateway_url)
    token = _resolve_token(admin_token)
    prefix = _resolve_prefix(admin_prefix)

    client = make_client(resolved_url, token)
    group_id = _resolve_group_id(client, prefix, name)

    server_ids = [_resolve_server_id(client, prefix, s) for s in server]

    try:
        r = client.put(f"{prefix}/groups/{group_id}/servers", json=server_ids)
        r.raise_for_status()
    except httpx.ConnectError as exc:
        typer.echo(f"Error: cannot connect to gateway at {resolved_url} - {exc}", err=True)
        raise typer.Exit(1) from exc
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} {exc.response.text}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Group {name!r} members updated ({len(server_ids)} server(s)).")
    if not no_reload:
        _do_reload(client, prefix)
