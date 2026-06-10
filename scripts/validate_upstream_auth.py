"""Live validation: secret-ref headers, client-credentials OAuth, and the HIL JSON API.

Boots a *secured* upstream (rejects any MCP request without a valid ``X-Api-Key`` or
an access token issued by its own ``/token`` client-credentials endpoint) and the
Mode-B gateway as real uvicorn servers. Then proves end-to-end that:

- ``${env:...}`` refs in ``static_headers`` resolve at connect time (and only the
  ref — never the secret — comes back from the admin read API),
- ``auth=oauth_client_credentials`` fetches a token headlessly, attaches it, and
  caches it across calls,
- an unresolvable secret ref fails loudly instead of sending the placeholder,
- a confirmation-gated tool call can be approved through the JSON decision API.

Run::

    uv run python scripts/validate_upstream_auth.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import FastMCP

from fast_gateway.config import GatewayConfig
from fast_gateway.factory import build_app

UPSTREAM_PORT = 9103
GATEWAY_PORT = 8004
STATIC_KEY = "static-key-9000"
CC_CLIENT_ID = "validator-client"
CC_CLIENT_SECRET = "validator-cc-secret"
FAILURES: list[str] = []
TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def build_upstream(issued: set[str]) -> Any:
    """A FastMCP echo upstream wrapped in an ASGI guard.

    ``POST /token`` implements the client_credentials grant; every other HTTP
    request must carry the static ``X-Api-Key`` or a Bearer token from ``issued``.
    """
    mcp: FastMCP = FastMCP("secure-echo")

    @mcp.tool(description="Echo the message back.")
    def echo(message: str = "") -> dict[str, str]:
        return {"message": message}

    @mcp.tool(description="Destructive-sounding tool to trigger the HIL gate.")
    def delete_item(item: str = "") -> dict[str, str]:
        return {"deleted": item}

    inner = mcp.http_app(path="/mcp/")

    async def send_json(send: Any, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        if scope["path"] == "/token" and scope["method"] == "POST":
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            form = parse_qs(body.decode())
            ok = (
                form.get("grant_type") == ["client_credentials"]
                and form.get("client_id") == [CC_CLIENT_ID]
                and form.get("client_secret") == [CC_CLIENT_SECRET]
            )
            if not ok:
                await send_json(send, 401, {"error": "invalid_client"})
                return
            token = f"cc-tok-{len(issued) + 1}"
            issued.add(token)
            await send_json(send, 200, {"access_token": token, "expires_in": 3600})
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        bearer = headers.get("authorization", "").removeprefix("Bearer ")
        if headers.get("x-api-key") == STATIC_KEY or bearer in issued:
            await inner(scope, receive, send)
            return
        await send_json(send, 401, {"error": "unauthorized"})

    return app


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


async def run_checks(client: httpx.AsyncClient, issued: set[str]) -> None:
    upstream_url = f"http://127.0.0.1:{UPSTREAM_PORT}/mcp/"

    print("\n## Secret-ref header auth (static bearer / API key)")
    created = await client.post(
        "/admin/servers",
        json={
            "name": "statichdr",
            "url": upstream_url,
            "static_headers": {"X-Api-Key": "${env:VALIDATE_STATIC_KEY}"},
        },
    )
    check("register server with ${env:} header ref", created.status_code == 201)
    static_id = created.json()["id"]
    record = (await client.get(f"/admin/servers/{static_id}")).json()
    check(
        "admin API returns the REF, never the secret",
        record["static_headers"]["X-Api-Key"] == "${env:VALIDATE_STATIC_KEY}"
        and STATIC_KEY not in json.dumps(record),
    )
    test = (await client.post(f"/admin/servers/{static_id}/test")).json()
    check("upstream accepts the resolved key (handshake ok)", test.get("ok") is True, str(test))

    refreshed = await client.post(f"/admin/servers/{static_id}/refresh")
    check(
        "per-server refresh (no full reload) succeeds",
        refreshed.status_code == 200 and refreshed.json().get("degraded") is False,
        refreshed.text[:80],
    )
    listed = {t["name"] for t in (await client.get("/admin/tools")).json()}
    check("refreshed server's tools are listed", "statichdr_echo" in listed)

    broken = await client.post(
        "/admin/servers",
        json={
            "name": "brokenref",
            "url": upstream_url,
            "static_headers": {"X-Api-Key": "${env:VALIDATE_MISSING_VAR_XYZ}"},
        },
    )
    broken_id = broken.json()["id"]
    btest = (await client.post(f"/admin/servers/{broken_id}/test")).json()
    check(
        "unresolvable ref fails loudly (no literal placeholder sent)",
        btest.get("ok") is False and "cannot be resolved" in str(btest.get("error")),
        str(btest.get("error"))[:80],
    )
    await client.delete(f"/admin/servers/{broken_id}")

    print("\n## OAuth2 client-credentials (headless)")
    cc = await client.post(
        "/admin/servers",
        json={
            "name": "machine",
            "url": upstream_url,
            "auth": "oauth_client_credentials",
            "oauth_token_url": f"http://127.0.0.1:{UPSTREAM_PORT}/token",
            "oauth_client_id": CC_CLIENT_ID,
            "oauth_client_secret": "${env:VALIDATE_CC_SECRET}",
        },
    )
    check("register client_credentials server", cc.status_code == 201, cc.text[:120])

    raw = await client.post(
        "/admin/servers",
        json={
            "name": "rawsecret",
            "url": upstream_url,
            "auth": "oauth_client_credentials",
            "oauth_token_url": f"http://127.0.0.1:{UPSTREAM_PORT}/token",
            "oauth_client_id": CC_CLIENT_ID,
            "oauth_client_secret": "a-raw-plaintext-secret",
        },
    )
    check(
        "raw plaintext client secret is rejected",
        raw.status_code in (409, 422),
        str(raw.status_code),
    )

    reloaded = await client.post("/admin/reload")
    check("reload mounts both servers", reloaded.status_code == 200, str(reloaded.json()))

    call = await client.post(
        "/admin/tools/machine_echo/call", json={"arguments": {"message": "via-cc"}}
    )
    body = call.json()
    check(
        "tool call through cc-authenticated upstream",
        call.status_code == 200
        and body.get("is_error") is False
        and body["structured_content"]["message"] == "via-cc",
        str(body)[:120],
    )
    check(
        "token was fetched from the live /token endpoint",
        len(issued) >= 1,
        f"issued={len(issued)}",
    )
    issued_before = len(issued)
    await client.post("/admin/tools/machine_echo/call", json={"arguments": {"message": "again"}})
    check(
        "token cached across calls (no re-fetch)",
        len(issued) == issued_before,
        f"issued={len(issued)}",
    )

    scall = await client.post(
        "/admin/tools/statichdr_echo/call", json={"arguments": {"message": "via-static"}}
    )
    sbody = scall.json()
    check(
        "tool call through secret-ref header upstream",
        scall.status_code == 200 and sbody["structured_content"]["message"] == "via-static",
        str(sbody)[:120],
    )

    print("\n## HIL JSON decision API (live approve)")
    pending_task = asyncio.create_task(
        client.post(
            "/admin/tools/machine_delete_item/call",
            json={"arguments": {"item": "thing"}},
            timeout=60,
        )
    )
    approval_id = ""
    for _ in range(100):
        listing = (await client.get("/admin/hil/pending")).json()
        if listing:
            approval_id = listing[0]["id"]
            break
        await asyncio.sleep(0.1)
    check("pending approval visible via GET /admin/hil/pending", bool(approval_id))
    detail = await client.get(f"/admin/hil/pending/{approval_id}")
    check(
        "JSON detail shows tool + arguments",
        detail.status_code == 200
        and detail.json()["tool_name"] == "machine_delete_item"
        and detail.json()["arguments"] == {"item": "thing"},
        detail.text[:120],
    )
    decision = await client.post(f"/admin/hil/pending/{approval_id}/approve")
    check(
        "POST .../approve returns JSON decision",
        decision.status_code == 200 and decision.json()["approved"] is True,
        decision.text[:120],
    )
    result = (await pending_task).json()
    check(
        "approved call completed against the real upstream",
        result.get("is_error") is False and result["structured_content"]["deleted"] == "thing",
        str(result)[:120],
    )
    second = await client.post(f"/admin/hil/pending/{approval_id}/approve")
    check("double-decide returns 404", second.status_code == 404)


async def main() -> int:
    os.environ["VALIDATE_STATIC_KEY"] = STATIC_KEY
    os.environ["VALIDATE_CC_SECRET"] = CC_CLIENT_SECRET
    os.environ.pop("VALIDATE_MISSING_VAR_XYZ", None)

    issued: set[str] = set()
    db = Path(tempfile.mkdtemp()) / "validate_upstream_auth.db"
    config = GatewayConfig.model_validate(
        {
            "db": str(db),
            "host": "127.0.0.1",
            "port": GATEWAY_PORT,
            "hil": {"enabled": True, "auto_open_browser": False, "timeout_seconds": 30},
            "policy": {"deny": [], "confirm": ["*_delete_*"], "audit": True},
        }
    )
    upstream = uvicorn.Server(
        uvicorn.Config(
            build_upstream(issued), host="127.0.0.1", port=UPSTREAM_PORT, log_level="warning"
        )
    )
    gateway = uvicorn.Server(
        uvicorn.Config(build_app(config), host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
    )
    upstream_task = await start(upstream, f"http://127.0.0.1:{UPSTREAM_PORT}/token")
    gateway_task = await start(gateway, f"http://127.0.0.1:{GATEWAY_PORT}/docs")
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{GATEWAY_PORT}", timeout=30
        ) as client:
            await run_checks(client, issued)
    finally:
        gateway.should_exit = True
        upstream.should_exit = True
        await asyncio.gather(gateway_task, upstream_task, return_exceptions=True)

    print(
        f"\n{'=' * 60}\nUPSTREAM AUTH SUMMARY: {TOTAL - len(FAILURES)}/{TOTAL} passed\n{'=' * 60}"
    )
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
