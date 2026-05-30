"""Tests for ``SqliteStore`` server and group CRUD against an in-memory database."""

from __future__ import annotations

import pytest

from mcp_gateway.models import (
    CatalogTool,
    GroupCreate,
    GroupPatch,
    ServerCreate,
    ServerPatch,
    Transport,
)
from mcp_gateway.store.sqlite import SqliteStore


@pytest.fixture
async def store() -> SqliteStore:
    store = SqliteStore(":memory:")
    await store.initialize()
    return store


def sample(name: str = "weather") -> ServerCreate:
    return ServerCreate(
        name=name,
        transport=Transport.HTTP,
        url="https://example.com/mcp",
        static_headers={"x-api-key": "secret"},
        tags=["demo"],
    )


async def test_create_then_get_roundtrips(store: SqliteStore) -> None:
    created = await store.create_server(sample())
    assert created.id
    assert created.name == "weather"

    fetched = await store.get_server(created.id)
    assert fetched is not None
    assert fetched == created
    assert fetched.static_headers == {"x-api-key": "secret"}
    assert fetched.transport is Transport.HTTP


async def test_get_missing_returns_none(store: SqliteStore) -> None:
    assert await store.get_server("nope") is None


async def test_list_returns_all_sorted_by_name(store: SqliteStore) -> None:
    await store.create_server(sample("zulu"))
    await store.create_server(sample("alpha"))
    names = [s.name for s in await store.list_servers()]
    assert names == ["alpha", "zulu"]


async def test_duplicate_name_rejected(store: SqliteStore) -> None:
    await store.create_server(sample())
    with pytest.raises(ValueError, match="already exists"):
        await store.create_server(sample())


async def test_update_applies_only_set_fields(store: SqliteStore) -> None:
    created = await store.create_server(sample())
    updated = await store.update_server(created.id, ServerPatch(enabled=False))

    assert updated.enabled is False
    assert updated.url == created.url
    assert updated.name == created.name
    assert updated.static_headers == created.static_headers


async def test_update_missing_raises(store: SqliteStore) -> None:
    with pytest.raises(KeyError):
        await store.update_server("nope", ServerPatch(enabled=False))


async def test_update_to_duplicate_name_rejected(store: SqliteStore) -> None:
    await store.create_server(sample("alpha"))
    other = await store.create_server(sample("beta"))
    with pytest.raises(ValueError, match="already exists"):
        await store.update_server(other.id, ServerPatch(name="alpha"))


async def test_delete_removes_record(store: SqliteStore) -> None:
    created = await store.create_server(sample())
    await store.delete_server(created.id)
    assert await store.get_server(created.id) is None


async def test_delete_missing_raises(store: SqliteStore) -> None:
    with pytest.raises(KeyError):
        await store.delete_server("nope")


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


def sample_group(name: str = "devs") -> GroupCreate:
    return GroupCreate(
        name=name,
        member_server_ids=["srv-1", "srv-2"],
        allow=["read_*"],
        deny=["write_*"],
    )


async def test_group_create_then_get_roundtrips(store: SqliteStore) -> None:
    created = await store.create_group(sample_group())
    assert created.id
    assert created.name == "devs"
    assert created.member_server_ids == ["srv-1", "srv-2"]
    assert created.allow == ["read_*"]
    assert created.deny == ["write_*"]

    fetched = await store.get_group(created.id)
    assert fetched is not None
    assert fetched == created


async def test_group_get_missing_returns_none(store: SqliteStore) -> None:
    assert await store.get_group("nope") is None


async def test_group_list_returns_all_sorted_by_name(store: SqliteStore) -> None:
    await store.create_group(sample_group("zulu"))
    await store.create_group(sample_group("alpha"))
    names = [g.name for g in await store.list_groups()]
    assert names == ["alpha", "zulu"]


async def test_group_duplicate_name_rejected(store: SqliteStore) -> None:
    await store.create_group(sample_group())
    with pytest.raises(ValueError, match="already exists"):
        await store.create_group(sample_group())


async def test_group_update_applies_only_set_fields(store: SqliteStore) -> None:
    created = await store.create_group(sample_group())
    updated = await store.update_group(created.id, GroupPatch(name="admins"))

    assert updated.name == "admins"
    assert updated.member_server_ids == created.member_server_ids
    assert updated.allow == created.allow
    assert updated.deny == created.deny


async def test_group_update_member_server_ids(store: SqliteStore) -> None:
    created = await store.create_group(sample_group())
    updated = await store.update_group(created.id, GroupPatch(member_server_ids=["srv-3"]))
    assert updated.member_server_ids == ["srv-3"]
    assert updated.name == created.name


async def test_group_update_allow_and_deny(store: SqliteStore) -> None:
    created = await store.create_group(sample_group())
    updated = await store.update_group(created.id, GroupPatch(allow=["*"], deny=[]))
    assert updated.allow == ["*"]
    assert updated.deny == []
    assert updated.name == created.name


async def test_group_update_missing_raises(store: SqliteStore) -> None:
    with pytest.raises(KeyError):
        await store.update_group("nope", GroupPatch(name="x"))


async def test_group_update_to_duplicate_name_rejected(store: SqliteStore) -> None:
    await store.create_group(sample_group("alpha"))
    other = await store.create_group(sample_group("beta"))
    with pytest.raises(ValueError, match="already exists"):
        await store.update_group(other.id, GroupPatch(name="alpha"))


async def test_group_delete_removes_record(store: SqliteStore) -> None:
    created = await store.create_group(sample_group())
    await store.delete_group(created.id)
    assert await store.get_group(created.id) is None


async def test_group_delete_missing_raises(store: SqliteStore) -> None:
    with pytest.raises(KeyError):
        await store.delete_group("nope")


# ---------------------------------------------------------------------------
# Catalog persistence + search
# ---------------------------------------------------------------------------


def catalog_sample() -> list[CatalogTool]:
    return [
        CatalogTool(
            server_id="s1",
            namespace="math",
            name="math_add",
            bare_name="add",
            description="Add two numbers together.",
            tags=["arith"],
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
            output_schema={"type": "integer"},
        ),
        CatalogTool(
            server_id="s1",
            namespace="math",
            name="math_subtract",
            bare_name="subtract",
            description="Subtract one number from another.",
            tags=["arith"],
        ),
        CatalogTool(
            server_id="s2",
            namespace="text",
            name="text_upper",
            bare_name="upper",
            description="Uppercase a string.",
            tags=["string"],
        ),
    ]


async def test_replace_then_list_catalog_roundtrips(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    names = [t.name for t in await store.list_catalog()]
    assert names == ["math_add", "math_subtract", "text_upper"]


async def test_replace_catalog_preserves_schema_fields(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    tool = await store.get_catalog_tool("math_add")
    assert tool is not None
    assert tool.bare_name == "add"
    assert tool.namespace == "math"
    assert tool.tags == ["arith"]
    assert tool.parameters["properties"]["a"]["type"] == "integer"
    assert tool.output_schema == {"type": "integer"}


async def test_replace_catalog_is_idempotent(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    await store.replace_catalog(catalog_sample())
    assert len(await store.list_catalog()) == 3


async def test_replace_catalog_drops_previous_tools(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    await store.replace_catalog([catalog_sample()[0]])
    names = [t.name for t in await store.list_catalog()]
    assert names == ["math_add"]


async def test_replace_with_empty_clears_catalog(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    await store.replace_catalog([])
    assert await store.list_catalog() == []


async def test_get_catalog_tool_missing_returns_none(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    assert await store.get_catalog_tool("nope") is None


async def test_search_catalog_matches_name(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    names = [t.name for t in await store.search_catalog("add")]
    assert names == ["math_add"]


async def test_search_catalog_matches_description_token(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    names = {t.name for t in await store.search_catalog("number")}
    assert names == {"math_add", "math_subtract"}


async def test_search_catalog_matches_tag(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    names = [t.name for t in await store.search_catalog("string")]
    assert names == ["text_upper"]


async def test_search_catalog_empty_query_browses(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    names = [t.name for t in await store.search_catalog("", limit=2)]
    assert names == ["math_add", "math_subtract"]


async def test_search_catalog_respects_limit(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    assert len(await store.search_catalog("", limit=1)) == 1


async def test_search_catalog_nonalnum_query_browses(store: SqliteStore) -> None:
    """A query with no usable tokens degrades to a browse rather than erroring."""
    await store.replace_catalog(catalog_sample())
    assert len(await store.search_catalog("!!!")) == 3


async def test_search_catalog_no_match_returns_empty(store: SqliteStore) -> None:
    await store.replace_catalog(catalog_sample())
    assert await store.search_catalog("zzzznomatch") == []
