"""Tests for the admin group CRUD endpoints, driven through a TestClient so the
startup lifespan (store init + reload) runs like in production."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fast_gateway import Hooks, SqliteStore, create_gateway


@pytest.fixture
def client() -> Iterator[TestClient]:
    gateway = create_gateway(SqliteStore(":memory:"), Hooks())
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    with TestClient(app) as test_client:
        yield test_client


def make_payload(name: str = "devs") -> dict[str, object]:
    return {
        "name": name,
        "member_server_ids": ["srv-1"],
        "allow": ["read_*"],
        "deny": [],
    }


def test_create_and_get_group(client: TestClient) -> None:
    created = client.post("/admin/groups", json=make_payload())
    assert created.status_code == 201
    group_id = created.json()["id"]
    assert group_id

    fetched = client.get(f"/admin/groups/{group_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["name"] == "devs"
    assert body["member_server_ids"] == ["srv-1"]
    assert body["allow"] == ["read_*"]


def test_list_groups(client: TestClient) -> None:
    client.post("/admin/groups", json=make_payload("zulu"))
    client.post("/admin/groups", json=make_payload("alpha"))
    resp = client.get("/admin/groups")
    assert resp.status_code == 200
    names = [g["name"] for g in resp.json()]
    assert names == ["alpha", "zulu"]


def test_get_missing_returns_404(client: TestClient) -> None:
    assert client.get("/admin/groups/nope").status_code == 404


def test_create_duplicate_name_conflicts(client: TestClient) -> None:
    assert client.post("/admin/groups", json=make_payload()).status_code == 201
    duplicate = client.post("/admin/groups", json=make_payload())
    assert duplicate.status_code == 409


def test_patch_group(client: TestClient) -> None:
    group_id = client.post("/admin/groups", json=make_payload()).json()["id"]
    patched = client.patch(f"/admin/groups/{group_id}", json={"name": "leads"})
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "leads"
    assert body["member_server_ids"] == ["srv-1"]


def test_patch_missing_returns_404(client: TestClient) -> None:
    assert client.patch("/admin/groups/nope", json={"name": "x"}).status_code == 404


def test_patch_duplicate_name_conflicts(client: TestClient) -> None:
    client.post("/admin/groups", json=make_payload("alpha"))
    other_id = client.post("/admin/groups", json=make_payload("beta")).json()["id"]
    resp = client.patch(f"/admin/groups/{other_id}", json={"name": "alpha"})
    assert resp.status_code == 409


def test_delete_group(client: TestClient) -> None:
    group_id = client.post("/admin/groups", json=make_payload()).json()["id"]
    assert client.delete(f"/admin/groups/{group_id}").status_code == 204
    assert client.get(f"/admin/groups/{group_id}").status_code == 404


def test_delete_missing_returns_404(client: TestClient) -> None:
    assert client.delete("/admin/groups/nope").status_code == 404


def test_put_servers_sets_membership(client: TestClient) -> None:
    group_id = client.post("/admin/groups", json=make_payload()).json()["id"]
    resp = client.put(
        f"/admin/groups/{group_id}/servers",
        json=["srv-a", "srv-b", "srv-c"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["member_server_ids"] == ["srv-a", "srv-b", "srv-c"]
    assert body["name"] == "devs"


def test_put_servers_missing_group_returns_404(client: TestClient) -> None:
    resp = client.put("/admin/groups/nope/servers", json=["srv-1"])
    assert resp.status_code == 404
