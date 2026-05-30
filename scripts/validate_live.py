"""Live end-to-end validation harness for fast-gateway.

Boots the local echo upstream and the ``examples.live_gateway`` app as real uvicorn
processes, then drives them over real HTTP — admin REST via httpx, MCP protocol via a
real ``fastmcp.Client`` — asserting every feature: CRUD, namespaced proxying, reload,
a live proxied tool call, all five hook seams, allow/deny, groups, the search meta-tools,
admin auth, performance, and security properties.

Run::

    uv run python scripts/validate_live.py

Exits non-zero if any REQUIRED check fails. Public-internet checks are best-effort
(reported, never fatal) so upstream flakiness does not gate the run.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client
from fastmcp.client.transports.http import StreamableHttpTransport

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
ECHO_PORT = 9100
GW_PORT = 8000
ADMIN_TOKEN = "admin-secret-token"
ECHO_URL = f"http://127.0.0.1:{ECHO_PORT}/mcp/"
GW_MCP = f"http://127.0.0.1:{GW_PORT}/mcp/"
GW_GROUP = f"http://127.0.0.1:{GW_PORT}/mcp/g/readonly/"
GW_ADMIN = f"http://127.0.0.1:{GW_PORT}/admin"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@dataclass
class Results:
    rows: list[tuple[str, str, bool, bool, str]] = field(default_factory=list)

    def check(
        self, section: str, name: str, ok: bool, detail: str = "", required: bool = True
    ) -> bool:
        self.rows.append((section, name, ok, required, detail))
        tag = "PASS" if ok else ("FAIL" if required else "WARN")
        print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    def summary(self) -> int:
        req_fail = [r for r in self.rows if not r[2] and r[3]]
        best_fail = [r for r in self.rows if not r[2] and not r[3]]
        passed = [r for r in self.rows if r[2]]
        print("\n" + "=" * 70)
        print(
            f"SUMMARY: {len(passed)} passed, {len(req_fail)} required-failed, "
            f"{len(best_fail)} best-effort-warned, {len(self.rows)} total"
        )
        if req_fail:
            print("\nREQUIRED FAILURES:")
            for s, n, _, _, d in req_fail:
                print(f"  - [{s}] {n}: {d}")
        if best_fail:
            print("\nBEST-EFFORT WARNINGS (public internet, non-fatal):")
            for s, n, _, _, d in best_fail:
                print(f"  - [{s}] {n}: {d}")
        print("=" * 70)
        return 1 if req_fail else 0


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _spawn(module_app: str, port: int, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        module_app,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    full_env = {**os.environ, **env}
    full_env.pop("VIRTUAL_ENV", None)
    return subprocess.Popen(
        cmd, cwd=REPO, env=full_env, stdout=log.open("wb"), stderr=subprocess.STDOUT
    )


async def _wait_mcp(url: str, timeout: float, label: str) -> bool:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            async with Client(StreamableHttpTransport(url)) as c:
                await c.list_tools()
            return True
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc).splitlines()[0][:80]}"
            await asyncio.sleep(1.0)
    print(f"  !! {label} not ready after {timeout}s: {last}")
    return False


async def _wait_admin(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as h:
        while time.monotonic() < deadline:
            try:
                r = await h.get(f"{GW_ADMIN}/servers", headers=AUTH, timeout=5)
                if r.status_code == 200 and r.json():
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    return False


async def call_text(client: Client, name: str, args: dict[str, Any]) -> str:
    res = await client.call_tool(name, args)
    parts = []
    for block in res.content:
        parts.append(getattr(block, "text", ""))
    return " ".join(parts)


async def run_admin_checks(r: Results) -> dict[str, str]:
    print("\n## Admin API (CRUD) + auth")
    ids: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30) as h:
        # auth: missing token rejected
        r401 = await h.get(f"{GW_ADMIN}/servers")
        r.check(
            "admin",
            "unauthenticated admin call -> 401",
            r401.status_code == 401,
            f"got {r401.status_code}",
        )
        # list
        rl = await h.get(f"{GW_ADMIN}/servers", headers=AUTH)
        servers = rl.json() if rl.status_code == 200 else []
        ids = {s["name"]: s["id"] for s in servers}
        r.check(
            "admin",
            "list servers (seeded)",
            rl.status_code == 200 and "echo" in ids,
            f"{len(servers)} servers: {sorted(ids)}",
        )
        # get one
        if "echo" in ids:
            rg = await h.get(f"{GW_ADMIN}/servers/{ids['echo']}", headers=AUTH)
            r.check(
                "admin", "get server by id", rg.status_code == 200 and rg.json()["name"] == "echo"
            )
        payload = {"name": "throwaway", "url": "http://127.0.0.1:9999/mcp/", "transport": "http"}
        rc = await h.post(f"{GW_ADMIN}/servers", headers=AUTH, json=payload)
        r.check("admin", "create server -> 201", rc.status_code == 201, f"got {rc.status_code}")
        rdup = await h.post(f"{GW_ADMIN}/servers", headers=AUTH, json=payload)
        r.check(
            "admin", "duplicate name -> 409", rdup.status_code == 409, f"got {rdup.status_code}"
        )
        if rc.status_code == 201:
            tid = rc.json()["id"]
            rpatch = await h.patch(
                f"{GW_ADMIN}/servers/{tid}", headers=AUTH, json={"timeout_seconds": 12.5}
            )
            r.check(
                "admin",
                "patch server",
                rpatch.status_code == 200 and rpatch.json()["timeout_seconds"] == 12.5,
            )
            rdel = await h.delete(f"{GW_ADMIN}/servers/{tid}", headers=AUTH)
            r.check(
                "admin", "delete server -> 204", rdel.status_code == 204, f"got {rdel.status_code}"
            )
        r404 = await h.get(f"{GW_ADMIN}/servers/does-not-exist", headers=AUTH)
        r.check("admin", "unknown id -> 404", r404.status_code == 404, f"got {r404.status_code}")
        rbad = await h.post(
            f"{GW_ADMIN}/servers", headers=AUTH, json={"name": "bad name!", "url": "x"}
        )
        r.check(
            "admin",
            "invalid name -> 422 validation",
            rbad.status_code == 422,
            f"got {rbad.status_code}",
        )
        # live introspection: /test and /tools on echo
        if "echo" in ids:
            rt = await h.post(f"{GW_ADMIN}/servers/{ids['echo']}/test", headers=AUTH)
            body = rt.json() if rt.status_code == 200 else {}
            r.check(
                "admin",
                "/servers/{id}/test handshake (echo)",
                body.get("ok") is True,
                f"tool_count={body.get('tool_count')}",
            )
            rtools = await h.get(f"{GW_ADMIN}/servers/{ids['echo']}/tools", headers=AUTH)
            tools = rtools.json() if rtools.status_code == 200 else []
            r.check(
                "admin",
                "/servers/{id}/tools live list (echo)",
                rtools.status_code == 200 and any(t["name"] == "echo" for t in tools),
                f"{[t['name'] for t in tools]}",
            )
        # secret-leak check: bearer token must NOT appear in read API
        rl2 = await h.get(f"{GW_ADMIN}/servers", headers=AUTH)
        r.check(
            "security",
            "injected bearer absent from registry read API",
            "echo-bearer-12345" not in rl2.text,
            "static_headers stays clean; auth is hook-only",
        )
        # reload endpoint
        rr = await h.post(f"{GW_ADMIN}/reload", headers=AUTH)
        r.check(
            "admin", "POST /reload", rr.status_code == 200 and rr.json().get("status") == "reloaded"
        )
    return ids


async def run_mcp_checks(r: Results) -> None:
    print("\n## MCP full endpoint: namespacing, hook seams, live call")
    async with Client(StreamableHttpTransport(GW_MCP)) as c:
        tools = await c.list_tools()
        names = {t.name for t in tools}
        r.check(
            "proxy",
            "namespaced tools listed (echo_echo present)",
            "echo_echo" in names,
            f"{len(names)} tools",
        )
        r.check(
            "proxy",
            "meta-tools present (search_tools/describe_tool)",
            {"search_tools", "describe_tool"} <= names,
        )
        # pre_list_tools: delete_* hidden from discovery
        r.check(
            "hook:pre_list_tools",
            "echo_delete_item hidden from listing",
            "echo_delete_item" not in names,
            "hidden by pre_list_tools; still governed on call",
        )
        # server deny: huggingface whoami hidden
        r.check(
            "access",
            "server deny hides huggingface_hf_whoami",
            "huggingface_hf_whoami" not in names,
            required=False,
        )
        # public namespacing best-effort
        public_ns = {n.split("_")[0] for n in names if "_" in n}
        r.check(
            "proxy",
            "multiple upstreams namespaced",
            len(public_ns) >= 2,
            f"namespaces={sorted(public_ns)}",
            required=False,
        )

        # LIVE proxied tool call + pre_tool_call arg mutation
        echo_out = await call_text(c, "echo_echo", {"message": "hello"})
        r.check("proxy", "LIVE proxied tool call (echo_echo)", "hello" in echo_out, echo_out[:80])
        r.check(
            "hook:pre_tool_call",
            "argument mutation injected (note=via-gateway)",
            "via-gateway" in echo_out,
            echo_out[:80],
        )
        # pre_mcp_connect: injected bearer reflected by upstream
        who = await call_text(c, "echo_whoami", {})
        r.check(
            "hook:pre_mcp_connect",
            "injected Authorization reached upstream",
            "echo-bearer-12345" in who,
            who[:80],
        )
        leaked = await call_text(c, "echo_leak_secret", {})
        r.check(
            "hook:post_tool_call",
            "credential redacted from response",
            "[REDACTED]" in leaked and "ghp_" not in leaked,
            leaked[:90],
        )
        # confirmation approve path (delete_item in HIL_AUTO_APPROVE)
        try:
            del_out = await call_text(c, "echo_delete_item", {"item": "x"})
            ok = "deleted" in del_out
        except Exception as exc:
            ok, del_out = False, str(exc)
        r.check(
            "hook:confirmation",
            "approved destructive call proceeds (delete_item)",
            ok,
            del_out[:80],
        )
        # confirmation deny path (purge_cache NOT in allowlist) — any raised error means blocked
        denied, deny_msg = False, "call unexpectedly succeeded"
        try:
            out = await call_text(c, "echo_purge_cache", {})
            deny_msg = f"NOT denied, returned: {out[:60]}"
        except Exception as exc:
            denied = True
            deny_msg = str(exc).splitlines()[0][:80]
        r.check(
            "hook:confirmation",
            "unapproved destructive call denied (purge_cache)",
            denied,
            deny_msg,
        )


async def run_search_checks(r: Results) -> None:
    print("\n## search_tools / describe_tool meta-tools")
    async with Client(StreamableHttpTransport(GW_MCP)) as c:
        import json

        async def search(q: str, limit: int = 10) -> list[dict[str, Any]]:
            res = await c.call_tool("search_tools", {"query": q, "limit": limit})
            return json.loads(res.content[0].text)

        empty = await search("", 50)
        r.check("search", "empty query browses catalog", len(empty) > 0, f"{len(empty)} tools")
        hits = await search("echo", 10)
        r.check(
            "search",
            "keyword search returns hits",
            any("echo" in h["name"] for h in hits),
            f"{[h['name'] for h in hits][:5]}",
        )
        # denied tool absent from search (no existence leak)
        hidden = await search("whoami", 50)
        r.check(
            "security",
            "denied huggingface_hf_whoami absent from search",
            not any(h["name"] == "huggingface_hf_whoami" for h in hidden),
            required=False,
        )
        # FTS injection safety
        try:
            await search('"; DROP TABLE catalog_tools; --', 10)
            after = await search("echo", 10)
            inj_ok = len(after) > 0
        except Exception as exc:
            inj_ok = False
            print(f"     injection raised: {exc}")
        r.check("security", "FTS injection query is safe (catalog intact)", inj_ok)
        # describe_tool full schema
        res = await c.call_tool("describe_tool", {"name": "echo_echo"})
        desc = json.loads(res.content[0].text)
        r.check(
            "search",
            "describe_tool returns full schema",
            desc.get("name") == "echo_echo" and "input_schema" in desc,
        )
        # describe_tool on denied -> ToolError (no leak)
        leaked_exists = False
        try:
            await c.call_tool("describe_tool", {"name": "huggingface_hf_whoami"})
            leaked_exists = True
        except Exception:
            leaked_exists = False
        r.check(
            "security",
            "describe_tool on denied tool errors (no existence leak)",
            not leaked_exists,
            required=False,
        )


async def run_group_checks(r: Results) -> None:
    print("\n## Group-scoped endpoint /mcp/g/readonly/")
    try:
        async with Client(StreamableHttpTransport(GW_GROUP)) as c:
            tools = await c.list_tools()
            names = {t.name for t in tools}
        r.check("groups", "group endpoint reachable", True, f"{len(names)} tools")
        r.check(
            "groups",
            "echo not a member -> echo_* absent at group endpoint",
            not any(
                n.startswith("echo_") for n in names if n not in {"search_tools", "describe_tool"}
            ),
        )
        r.check(
            "groups",
            "group deny applied (*delete*/*write* absent)",
            not any("delete" in n or "write" in n for n in names),
            required=False,
        )
        r.check(
            "groups",
            "group view non-empty (public members present)",
            len(names) > 2,
            required=False,
        )
    except Exception as exc:
        r.check("groups", "group endpoint reachable", False, str(exc)[:100])


async def run_perf_checks(r: Results) -> None:
    print("\n## Performance")
    async with Client(StreamableHttpTransport(GW_MCP)) as c:
        # list_tools latency (catalog snapshot, no upstream fan-out)
        n = 20
        t0 = time.monotonic()
        for _ in range(n):
            await c.list_tools()
        avg_list = (time.monotonic() - t0) / n * 1000
        r.check(
            "perf",
            f"list_tools avg latency {avg_list:.1f}ms (n={n})",
            avg_list < 200,
            "catalog snapshot, no fan-out",
            required=False,
        )
        t0 = time.monotonic()
        for _ in range(n):
            await c.call_tool("search_tools", {"query": "doc", "limit": 10})
        avg_search = (time.monotonic() - t0) / n * 1000
        r.check(
            "perf",
            f"search_tools avg latency {avg_search:.1f}ms (n={n})",
            avg_search < 300,
            required=False,
        )
        # concurrency: many proxied echo calls at once
        m = 15
        t0 = time.monotonic()
        outs = await asyncio.gather(
            *(call_text(c, "echo_echo", {"message": f"c{i}"}) for i in range(m)),
            return_exceptions=True,
        )
        dur = time.monotonic() - t0
        ok = sum(1 for o in outs if isinstance(o, str) and "via-gateway" in o)
        r.check(
            "perf",
            f"{m} concurrent proxied calls: {ok}/{m} ok in {dur:.2f}s",
            ok == m,
            f"throughput~{m / dur:.1f} calls/s",
        )
    # reload timing
    async with httpx.AsyncClient(timeout=120) as h:
        t0 = time.monotonic()
        rr = await h.post(f"{GW_ADMIN}/reload", headers=AUTH)
        dur = time.monotonic() - t0
        r.check(
            "perf",
            f"reload (re-introspect all upstreams) {dur:.2f}s",
            rr.status_code == 200,
            required=False,
        )


async def drive() -> int:
    r = Results()
    await run_admin_checks(r)
    await run_mcp_checks(r)
    await run_search_checks(r)
    await run_group_checks(r)
    await run_perf_checks(r)
    return r.summary()


def main() -> int:
    db = REPO / "validate_live.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    logs = Path(tempfile.gettempdir())
    echo_log = logs / "echo_upstream.log"
    gw_log = logs / "live_gateway.log"

    if not (_free(ECHO_PORT) and _free(GW_PORT)):
        print(f"!! port {ECHO_PORT} or {GW_PORT} already in use; aborting")
        return 2

    echo_proc = _spawn("examples.echo_upstream:app", ECHO_PORT, {}, echo_log)
    gw_proc = _spawn(
        "examples.live_gateway:app",
        GW_PORT,
        {
            "ADMIN_TOKEN": ADMIN_TOKEN,
            "HIL_AUTO_APPROVE": "echo_delete_item",
            "GATEWAY_DB": str(db),
            "ECHO_URL": ECHO_URL,
        },
        gw_log,
    )
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print("Waiting for echo upstream...")
        if not loop.run_until_complete(_wait_mcp(ECHO_URL, 30, "echo")):
            print(echo_log.read_text(errors="replace")[-2000:])
            return 2
        print("Waiting for gateway (seeds + introspects 6 upstreams)...")
        if not loop.run_until_complete(_wait_admin(120)):
            print(gw_log.read_text(errors="replace")[-3000:])
            return 2
        print("Both up. Running validation.\n")
        return loop.run_until_complete(drive())
    finally:
        for p in (gw_proc, echo_proc):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
