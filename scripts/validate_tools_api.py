"""Live validation: ToolsApiPlugin REST routes against a real daemon + real upstream.

Boots the echo upstream and the Mode-B gateway (``factory.build_app``) as real
uvicorn servers on localhost sockets, registers the upstream over the admin API,
then drives ``/admin/tools`` over real HTTP: list, group-scoped list, describe,
invoke a proxied tool, config-policy deny in-band, group deny in-band, and a
latency check. Exits non-zero on any required failure.

Run::

    uv run python scripts/validate_tools_api.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.echo_upstream import app as upstream_app

from fast_gateway.config import GatewayConfig
from fast_gateway.factory import build_app

UPSTREAM_PORT = 9102
GATEWAY_PORT = 8003
FAILURES: list[str] = []
TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


async def start(server: uvicorn.Server, probe_url: str) -> asyncio.Task[None]:
    task = asyncio.create_task(server.serve())
    async with httpx.AsyncClient() as probe:
        for _ in range(200):
            try:
                await probe.get(probe_url, timeout=1.0)
                return task
            except httpx.TransportError:
                await asyncio.sleep(0.1)
    raise RuntimeError(f"Server at {probe_url} did not come up.")


async def run_checks(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/admin/servers",
        json={
            "name": "echo",
            "url": f"http://127.0.0.1:{UPSTREAM_PORT}/mcp/",
            "transport": "http",
            "deny": ["leak_*"],
        },
    )
    check("register echo upstream", created.status_code == 201)
    server_id = created.json()["id"]
    group = await client.post(
        "/admin/groups",
        json={"name": "readonly", "member_server_ids": [server_id], "deny": ["*delete*"]},
    )
    check("create readonly group", group.status_code == 201)
    reloaded = await client.post("/admin/reload")
    check("reload", reloaded.status_code == 200, str(reloaded.json()))

    print("\n## GET /admin/tools")
    listing = await client.get("/admin/tools")
    names = {t["name"] for t in listing.json()}
    check(
        "lists proxied + meta tools",
        listing.status_code == 200 and {"echo_echo", "search_tools", "describe_tool"} <= names,
        f"{len(names)} tools",
    )
    check("server-record deny hides echo_leak_secret from list", "echo_leak_secret" not in names)
    check(
        "config-policy deny glob is call-time: tool stays listed",
        "echo_purge_cache" in names,
    )

    print("\n## GET /admin/tools?group=readonly")
    scoped = await client.get("/admin/tools", params={"group": "readonly"})
    scoped_names = {t["name"] for t in scoped.json()}
    check("group view keeps member tools", "echo_echo" in scoped_names)
    check("group deny hides echo_delete_item", "echo_delete_item" not in scoped_names)

    print("\n## GET /admin/tools/{name}")
    detail = await client.get("/admin/tools/echo_echo")
    check(
        "describe returns input schema",
        detail.status_code == 200 and "message" in detail.json()["input_schema"]["properties"],
    )
    check(
        "policy-hidden tool reads as 404 (no existence leak)",
        (await client.get("/admin/tools/echo_leak_secret")).status_code == 404,
    )

    print("\n## POST /admin/tools/{name}/call")
    call = await client.post(
        "/admin/tools/echo_echo/call", json={"arguments": {"message": "hi-from-rest"}}
    )
    body = call.json()
    check(
        "invokes REAL proxied upstream tool",
        call.status_code == 200
        and body["is_error"] is False
        and body["structured_content"]["message"] == "hi-from-rest",
        str(body["structured_content"]),
    )
    denied = await client.post("/admin/tools/echo_purge_cache/call", json={})
    dbody = denied.json()
    check(
        "config-policy deny reported in-band",
        dbody["is_error"] is True and "denied by policy" in dbody["content"][0]["text"],
        dbody["content"][0]["text"],
    )
    gdenied = await client.post(
        "/admin/tools/echo_delete_item/call",
        json={"arguments": {"item": "x"}, "group": "readonly"},
    )
    gbody = gdenied.json()
    check(
        "group deny reported in-band",
        gbody["is_error"] is True and "not permitted" in gbody["content"][0]["text"],
        gbody["content"][0]["text"],
    )

    print("\n## Performance")
    started = time.perf_counter()
    for _ in range(20):
        await client.get("/admin/tools")
    list_avg = (time.perf_counter() - started) / 20 * 1000
    check("REST list avg latency < 250ms", list_avg < 250, f"{list_avg:.1f}ms (n=20)")
    started = time.perf_counter()
    for _ in range(20):
        await client.post("/admin/tools/echo_echo/call", json={"arguments": {"message": "p"}})
    call_avg = (time.perf_counter() - started) / 20 * 1000
    check("REST proxied invoke avg latency < 500ms", call_avg < 500, f"{call_avg:.1f}ms (n=20)")


async def main() -> int:
    db = Path(tempfile.mkdtemp()) / "validate_tools_api.db"
    config = GatewayConfig.model_validate(
        {
            "db": str(db),
            "host": "127.0.0.1",
            "port": GATEWAY_PORT,
            "hil": {"enabled": False},
            "policy": {"deny": ["echo_purge*"], "confirm": [], "audit": True},
        }
    )
    upstream = uvicorn.Server(
        uvicorn.Config(upstream_app, host="127.0.0.1", port=UPSTREAM_PORT, log_level="warning")
    )
    gateway = uvicorn.Server(
        uvicorn.Config(build_app(config), host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
    )
    upstream_task = await start(upstream, f"http://127.0.0.1:{UPSTREAM_PORT}/mcp/")
    gateway_task = await start(gateway, f"http://127.0.0.1:{GATEWAY_PORT}/docs")
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{GATEWAY_PORT}", timeout=30
        ) as client:
            await run_checks(client)
    finally:
        gateway.should_exit = True
        upstream.should_exit = True
        await asyncio.gather(gateway_task, upstream_task, return_exceptions=True)

    print(f"\n{'=' * 60}\nTOOLS API SUMMARY: {TOTAL - len(FAILURES)}/{TOTAL} passed\n{'=' * 60}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
