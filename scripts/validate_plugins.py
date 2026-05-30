"""Live validation of the plugin system against a running ``plugin_gateway`` app.

Boots ``examples.plugin_gateway`` as a real uvicorn process and asserts that every
``PluginContributions`` field took effect end-to-end: a plugin-registered MCP tool is
callable, plugin FastMCP middleware wraps the call, the plugin's ``pre_tool_call`` hook
fires, its admin router and ASGI mount respond, and ``setup`` ran via the lifespan.

Run::  uv run python scripts/validate_plugins.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports.http import StreamableHttpTransport

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
PORT = 8001
MCP = f"http://127.0.0.1:{PORT}/mcp/"
BASE = f"http://127.0.0.1:{PORT}"


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


async def _wait(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with Client(StreamableHttpTransport(MCP)) as c:
                await c.list_tools()
            return True
        except Exception:
            await asyncio.sleep(1.0)
    return False


async def drive() -> int:
    rows: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    print("\n## Plugin system (live)")
    async with Client(StreamableHttpTransport(MCP)) as c:
        names = {t.name for t in await c.list_tools()}
        check("register_tools: demo_marco listed", "demo_marco" in names, f"{sorted(names)}")
        res = await c.call_tool("demo_marco", {})
        text = res.content[0].text if res.content else ""
        check("register_tools: demo_marco callable", "polo" in text, text)
        check("middleware: around-call wrap applied ([mw])", "[mw]" in text, text)

    async with httpx.AsyncClient(timeout=15) as h:
        rs = await h.get(f"{BASE}/admin/demo/status")
        body = rs.json() if rs.status_code == 200 else {}
        check("admin_router: /admin/demo/status responds", rs.status_code == 200, str(body))
        check("setup(): ran via lifespan (started=True)", body.get("started") is True)
        check(
            "hooks: plugin pre_tool_call fired (pre_calls>=1)",
            (body.get("pre_calls") or 0) >= 1,
            f"pre_calls={body.get('pre_calls')}",
        )
        hm = None
        for path in ("/mcp/demo-health", "/mcp/demo-health/"):
            r = await h.get(f"{BASE}{path}")
            if r.status_code == 200:
                hm = r.json()
                break
        check(
            "mounts: ASGI sub-app reachable under /mcp",
            hm is not None and hm.get("ok") is True,
            str(hm),
        )

    failed = [n for n, ok, _ in rows if not ok]
    print("\n" + "=" * 60)
    print(f"PLUGIN SUMMARY: {len(rows) - len(failed)}/{len(rows)} passed")
    if failed:
        print("FAILED:", failed)
    print("=" * 60)
    return 1 if failed else 0


def main() -> int:
    if not _free(PORT):
        print(f"!! port {PORT} in use")
        return 2
    log = Path(tempfile.gettempdir()) / "plugin_gateway.log"
    env = {**os.environ}
    env.pop("VIRTUAL_ENV", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "examples.plugin_gateway:app",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        env=env,
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print("Waiting for plugin gateway...")
        if not loop.run_until_complete(_wait(40)):
            print(log.read_text(errors="replace")[-2000:])
            return 2
        return loop.run_until_complete(drive())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
