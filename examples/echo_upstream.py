"""A tiny local upstream MCP server used to ground-truth the gateway's hook seams.

Public internet upstreams prove real proxying, namespacing, catalog, and search — but
they cannot confirm what the gateway *injected* or *transformed*. This echo server can:
it reflects the arguments and headers it actually received, so an end-to-end run can
assert that ``pre_mcp_connect`` header injection, ``pre_tool_call`` argument mutation,
``post_tool_call`` redaction, and the confirmation gate all did what they claim.

Run standalone::

    uv run uvicorn examples.echo_upstream:app --port 9100

It then speaks streamable-HTTP MCP at http://127.0.0.1:9100/mcp/.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

mcp: FastMCP = FastMCP("echo")


@mcp.tool(description="Echo the arguments back to the caller, verbatim.")
def echo(message: str = "", note: str = "") -> dict[str, Any]:
    """Return whatever arguments arrived, so callers can see arg mutation."""
    return {"message": message, "note": note}


@mcp.tool(description="Return the Authorization header this upstream received.")
def whoami() -> dict[str, str]:
    """Reflect the inbound Authorization header — proves pre_mcp_connect injection.

    ``authorization`` is on FastMCP's default strip-list, so we opt it back in with
    ``include`` — exactly what a proxy forwarding upstream credentials must do.
    """
    headers = get_http_headers(include={"authorization"})
    return {"authorization": headers.get("authorization", "<none>")}


@mcp.tool(description="Return a payload containing a credential-shaped string.")
def leak_secret() -> dict[str, str]:
    """Emit a fake GitHub token so a redaction hook has something to scrub."""
    return {"text": "here is a token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 keep it safe"}


@mcp.tool(description="A deliberately destructive-sounding tool for the HIL gate.")
def delete_item(item: str = "") -> dict[str, str]:
    """Pretend to delete something; its name triggers the confirmation seam."""
    return {"deleted": item}


@mcp.tool(description="Another destructive-sounding tool, for the deny path of HIL.")
def purge_cache() -> dict[str, str]:
    """A second destructive tool, used to exercise confirmation *rejection*."""
    return {"purged": "ok"}


app = mcp.http_app(path="/mcp/")
