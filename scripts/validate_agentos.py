"""Live validation of the agent-os policy plugin over real HTTP + a real upstream.

Boots the echo upstream and ``examples.agentos_gateway`` (which wires AgtAgentOsPlugin
with a policy denying ``echo_purge_cache``) as real uvicorn processes, then asserts a
permitted tool succeeds and the policy-denied tool is blocked — through the gateway's
real MCP middleware stack. Skips cleanly if the optional ``agt`` extra is absent.

Run::  uv run python scripts/validate_agentos.py
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

from fastmcp import Client
from fastmcp.client.transports.http import StreamableHttpTransport

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
ECHO_PORT, POISONED_PORT, GW_PORT = 9100, 9103, 8002
ECHO_URL = f"http://127.0.0.1:{ECHO_PORT}/mcp/"
POISONED_URL = f"http://127.0.0.1:{POISONED_PORT}/mcp/"
GW_MCP = f"http://127.0.0.1:{GW_PORT}/mcp/"
RATE_LIMIT_MAX = 12


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _spawn(app: str, port: int, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    full = {**os.environ, **env}
    full.pop("VIRTUAL_ENV", None)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--port", str(port), "--log-level", "warning"],
        cwd=REPO,
        env=full,
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )


async def _wait(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with Client(StreamableHttpTransport(url)) as c:
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

    print("\n## Agent-OS policy plugin (live over HTTP)")
    async with Client(StreamableHttpTransport(GW_MCP)) as c:
        names = {t.name for t in await c.list_tools()}
        check("upstream proxied under policy plugin", "echo_echo" in names, f"{sorted(names)}")
        res = await c.call_tool("echo_echo", {"message": "hi"})
        text = res.content[0].text if res.content else ""
        check("policy allows permitted tool (echo_echo)", "hi" in text, text[:60])
        denied, msg = False, "unexpectedly succeeded"
        try:
            await c.call_tool("echo_purge_cache", {})
        except Exception as exc:
            denied = True
            msg = str(exc).splitlines()[0][:80]
        check("policy denies disallowed tool (echo_purge_cache)", denied, msg)

        print("\n## MCP security scan (tool poisoning)")
        check("benign tool from scanned upstream survives", "ext_lookup" in names)
        check("poisoned tool dropped from catalog", "ext_backdoor" not in names)

        print("\n## Sliding-window rate limiting")
        successes, limited, limit_msg = 0, False, "never rate limited"
        for _ in range(RATE_LIMIT_MAX + 3):
            try:
                await c.call_tool("echo_echo", {"message": "rl"})
                successes += 1
            except Exception as exc:
                limited = True
                limit_msg = str(exc).splitlines()[0][:80]
                break
        check("calls under budget succeed", successes > 0, f"{successes} calls ok")
        check(
            "over-budget call denied with rate-limit reason",
            limited and "ate limit" in limit_msg,
            limit_msg,
        )

    failed = [n for n, ok, _ in rows if not ok]
    print("\n" + "=" * 60)
    print(f"AGENT-OS SUMMARY: {len(rows) - len(failed)}/{len(rows)} passed")
    if failed:
        print("FAILED:", failed)
    print("=" * 60)
    return 1 if failed else 0


def main() -> int:
    try:
        import agent_os  # noqa: F401
    except ImportError:
        print("SKIP: agent_os not installed (run `uv sync --extra agt`)")
        return 0
    if not (_free(ECHO_PORT) and _free(POISONED_PORT) and _free(GW_PORT)):
        print(f"!! port {ECHO_PORT}, {POISONED_PORT} or {GW_PORT} in use")
        return 2
    db = REPO / "agentos_validate.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    scripts = Path(tempfile.gettempdir())
    echo = _spawn("examples.echo_upstream:app", ECHO_PORT, {}, scripts / "echo_upstream.log")
    poisoned = _spawn(
        "examples.poisoned_upstream:app", POISONED_PORT, {}, scripts / "poisoned_upstream.log"
    )
    gw = _spawn(
        "examples.agentos_gateway:app",
        GW_PORT,
        {
            "GATEWAY_DB": str(db),
            "ECHO_URL": ECHO_URL,
            "POISONED_URL": POISONED_URL,
            "AGT_SCAN": "1",
            "AGT_RATE_LIMIT_MAX": str(RATE_LIMIT_MAX),
        },
        scripts / "agentos_gateway.log",
    )
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print("Waiting for echo + poisoned + agentos gateway...")
        if not loop.run_until_complete(_wait(ECHO_URL, 30)):
            print((scripts / "echo_upstream.log").read_text(errors="replace")[-1500:])
            return 2
        if not loop.run_until_complete(_wait(POISONED_URL, 30)):
            print((scripts / "poisoned_upstream.log").read_text(errors="replace")[-1500:])
            return 2
        if not loop.run_until_complete(_wait(GW_MCP, 60)):
            print((scripts / "agentos_gateway.log").read_text(errors="replace")[-2500:])
            return 2
        return loop.run_until_complete(drive())
    finally:
        for p in (gw, poisoned, echo):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
