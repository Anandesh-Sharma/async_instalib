"""Captured Accounts search continuation with both session IDs and server tokens."""

from dataclasses import replace
from uuid import UUID

import httpx
import pytest

from async_instalib import ClientOptions, ProtocolError, RequestPolicy, Session
from async_instalib.config.people import SearchOptions
from async_instalib.models.pagination import Continuation, PageContext
from async_instalib.resources.people import People
from async_instalib.transport import Transport


def transport(server):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


async def test_accounts_search_retains_captured_page_context_and_advances_only_explicitly():
    requests = []

    def server(request):
        query = dict(request.url.params)
        index = len(requests)
        requests.append(query)
        assert query["query"] == "alex & synthetic"
        assert query["count"] == "30"
        assert query["session_state"] == "typeahead"
        assert "nav_chain" not in query and "qpl_join_id" not in query
        if index == 0:
            assert UUID(query["search_session_id"])
            assert UUID(query["serp_session_id"])
            assert "page_index" not in query
            assert "rank_token" not in query
            assert "page_token" not in query and "next_max_id" not in query
        else:
            assert query["search_session_id"] == requests[0]["search_session_id"]
            assert query["serp_session_id"] == requests[0]["serp_session_id"]
            assert query["page_index"] == str(index)
            assert query["rank_token"] == f"rank-{index}"
            assert query["page_token"] == f"cursor-{index}"
            assert query["next_max_id"] == f"cursor-{index}"
        return httpx.Response(
            200,
            json={
                "users": [{"pk": index + 1, "username": f"synthetic_{index}"}],
                "num_results": 1,
                "has_more": index < 2,
                "rank_token": f"rank-{index + 1}",
                "page_token": f"cursor-{index + 1}",
                "status": "ok",
            },
        )

    client = transport(server)
    try:
        people = People(client)
        first = await people.search("alex & synthetic", SearchOptions(limit=30))
        assert len(requests) == 1
        assert first.continuation.context == PageContext("search", "42", query="alex & synthetic")
        assert first.continuation.page_index == 1
        assert first.total_count is None
        second = await people.search(
            "alex & synthetic", SearchOptions(limit=30, cursor=first.continuation)
        )
        assert len(requests) == 2
        assert second.continuation.page_index == 2
        last = await people.search(
            "alex & synthetic", SearchOptions(limit=30, cursor=second.continuation)
        )
        assert len(requests) == 3
        assert not last.has_more
        assert last.continuation is None  # Ignore final-page tokens when has_more is false.
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "missing", ["rank_token", "search_session_id", "serp_session_id", "page_index"]
)
async def test_accounts_search_rejects_incomplete_continuation_before_network(missing):
    cursor = Continuation(
        "opaque",
        PageContext("search", "42", query="synthetic"),
        rank_token="rank",
        search_session_id="search-session",
        serp_session_id="serp-session",
        page_index=1,
    )
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(ValueError, match="missing search continuation metadata"):
            await People(client).search(
                "synthetic", SearchOptions(cursor=replace(cursor, **{missing: None}))
            )
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "fields",
    [
        {"page_token": None, "rank_token": "rank"},
        {"page_token": "", "rank_token": "rank"},
        {"page_token": "cursor", "rank_token": None},
        {"page_token": "cursor", "rank_token": ""},
        {"page_token": True, "rank_token": "rank"},
    ],
)
async def test_accounts_search_rejects_has_more_without_complete_tokens(fields):
    client = transport(
        lambda _: httpx.Response(200, json={"users": [], "has_more": True, **fields})
    )
    try:
        with pytest.raises(ProtocolError, match="continuation tokens"):
            await People(client).search("synthetic")
    finally:
        await client.aclose()


@pytest.mark.parametrize("page_index", [True, 0, -1, "1"])
def test_continuation_page_index_must_be_positive_integer(page_index):
    with pytest.raises(ValueError):
        Continuation("cursor", PageContext("search", "42"), page_index=page_index)


def test_non_search_continuations_keep_existing_contract():
    cursor = Continuation("opaque", PageContext("following", "42", target="123"))
    assert cursor.serp_session_id is None
    assert cursor.page_index is None
