"""Probe candidate public MCP servers for connectivity + tool listing.

Usage: uv run python scripts/probe_live_mcp.py
Prints, per candidate URL: ok/fail, transport, tool count, first few tool names.
Used to pick reliable live upstreams for end-to-end gateway validation.
"""

from __future__ import annotations

import asyncio

from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.server.providers.proxy import ProxyClient

CANDIDATES: list[tuple[str, str]] = [
    ("agno-docs", "https://docs.agno.com/mcp"),
    ("deepwiki", "https://mcp.deepwiki.com/mcp"),
    ("huggingface", "https://huggingface.co/mcp"),
    ("context7", "https://mcp.context7.com/mcp"),
    ("gitmcp-fastmcp", "https://gitmcp.io/jlowin/fastmcp"),
]


async def probe(name: str, url: str) -> None:
    for transport_name, make in (
        ("http", lambda: StreamableHttpTransport(url)),
        ("sse", lambda: SSETransport(url)),
    ):
        try:
            client = ProxyClient(make(), timeout=20.0)
            async with client:
                tools = await client.list_tools()
            names = [t.name for t in tools][:6]
            print(f"OK   {name:16} [{transport_name}] {url}")
            print(f"       tools={len(tools)} sample={names}")
            return
        except Exception as exc:
            msg = str(exc).splitlines()[0][:120]
            print(f"FAIL {name:16} [{transport_name}] {url}\n       {type(exc).__name__}: {msg}")


async def main() -> None:
    for name, url in CANDIDATES:
        await probe(name, url)
        print()


if __name__ == "__main__":
    asyncio.run(main())
