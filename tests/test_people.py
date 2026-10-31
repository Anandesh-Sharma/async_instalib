"""Synthetic Accounts and GraphQL typeahead exchange tests."""

import json
from dataclasses import replace
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, Session
from async_instalib.config.people import SearchOptions
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import RateLimited, RequestBudgetExceeded
from async_instalib.models.pagination import Continuation, PageContext
from async_instalib.resources.people import People
from async_instalib.transport import Transport


def make_transport(handler, *, authenticated=True, max_requests=60):
    session = (
        Session(authorization="Bearer synthetic", user_id="11") if authenticated else Session()
    )
    options = ClientOptions(policy=RequestPolicy(min_interval=0, max_requests=max_requests))
    return Transport(session, options, httpx.MockTransport(handler))


async def test_accounts_search_encodes_query_and_ignores_final_page_token():
    query = "किरण & a+b?/%"

    def server(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/fbsearch/account_serp/"
        assert request.url.params["query"] == query
        assert request.url.params["count"] == "7"
        assert request.url.params["search_surface"] == "user_serp"
        assert request.url.params["timezone_offset"] == "0"
        assert "nav_chain" not in request.url.params
        assert "qpl_join_id" not in request.url.params
        assert UUID(request.url.params["search_session_id"])
        assert UUID(request.url.params["serp_session_id"])
        assert request.headers["authorization"] == "Bearer synthetic"
        return httpx.Response(
            200,
            json={
                "users": [{"pk": "42", "username": "synthetic"}],
                "num_results": 1,
                "has_more": False,
                "page_token": "nonempty-but-final",
                "status": "ok",
            },
        )

    transport = make_transport(server)
    try:
        page = await People(transport).search(query, SearchOptions(limit=7))
        assert page.items[0].id == "42"
        assert page.items[0].is_private is None
        assert not page.has_more
        assert page.continuation is None
        assert page.total_count is None  # num_results is a page count, not a global total.
        assert page.raw["page_token"] == "nonempty-but-final"
    finally:
        await transport.aclose()


@pytest.mark.parametrize("method", ["search", "suggest"])
async def test_people_requires_authentication_before_network(method):
    transport = make_transport(lambda _: pytest.fail("unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await getattr(People(transport), method)("query")
    finally:
        await transport.aclose()


@pytest.mark.parametrize("query", [None, "", " ", "line\nfeed", "null\x00byte", "delete\x7f"])
async def test_search_rejects_invalid_queries_before_network(query):
    transport = make_transport(lambda _: pytest.fail("unexpected request"))
    try:
        with pytest.raises(ValueError):
            await People(transport).search(query)
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "changes", [{"query": "different"}, {"account_id": "12"}, {"operation": "followers"}]
)
async def test_search_rejects_foreign_continuations_before_network(changes):
    context = PageContext("search", "11", query="query")
    cursor = Continuation("opaque", replace(context, **changes))
    transport = make_transport(lambda _: pytest.fail("unexpected request"))
    try:
        with pytest.raises(ValueError, match="different operation"):
            await People(transport).search("query", SearchOptions(cursor=cursor))
    finally:
        await transport.aclose()


async def test_search_rejects_continuation_missing_request_metadata():
    cursor = Continuation("opaque", PageContext("search", "11", query="query"))
    transport = make_transport(lambda _: pytest.fail("unexpected request"))
    try:
        with pytest.raises(ValueError, match="missing search continuation metadata"):
            await People(transport).search("query", SearchOptions(cursor=cursor))
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"users": [], "has_more": "false"},
        {"users": [], "has_more": True, "page_token": "unknown-contract"},
        {"users": {}, "has_more": False},
        {"users": [None], "has_more": False},
        {"users": [{"pk": True, "username": "bad"}], "has_more": False},
        {"users": [{"pk": "42", "username": "bad", "is_private": "false"}], "has_more": False},
    ],
)
async def test_malformed_or_unestablished_search_response_fails_explicitly(payload):
    transport = make_transport(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(ProtocolError):
            await People(transport).search("query")
    finally:
        await transport.aclose()


async def test_suggest_uses_recorded_graphql_and_only_returns_person_rows():
    def server(request):
        assert request.url.path == "/graphql/query"
        assert request.method == "POST"
        form = parse_qs(request.content.decode())
        assert form["client_doc_id"] == ["702998554512640115307956645"]
        assert form["fb_api_req_friendly_name"] == ["TypeaheadSearchQuery"]
        variables = json.loads(form["variables"][0])
        parameters = variables["request_data"]
        assert parameters["query"] == "a+b & test"
        assert parameters["lat"] is None and parameters["lng"] is None
        assert UUID(parameters["search_session_id"])
        assert UUID(parameters["client_uuid_rank_token"])
        assert "count" not in parameters
        assert variables["search_surface"] == "TYPEAHEAD_SEARCH"
        return httpx.Response(
            200,
            json={
                "data": {
                    "xdt_fbsearch__typeahead_stream": {
                        "results": [
                            {"__typename": "XDTTypeaheadSearchKeyword", "keyword_lazified": {}},
                            {
                                "__typename": "XDTTypeaheadSearchUser",
                                "user": {"id": "42", "username": "synthetic", "is_private": False},
                            },
                        ]
                    }
                }
            },
        )

    transport = make_transport(server)
    try:
        page = await People(transport).suggest("a+b & test")
        assert [person.id for person in page.items] == ["42"]
        assert page.items[0].is_private is False
        assert not page.has_more
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "stream", [None, {}, {"results": {}}, {"results": [None]}, {"results": [{}]}]
)
async def test_suggest_rejects_malformed_results(stream):
    transport = make_transport(
        lambda _: httpx.Response(200, json={"data": {"xdt_fbsearch__typeahead_stream": stream}})
    )
    try:
        with pytest.raises(ProtocolError):
            await People(transport).suggest("query")
    finally:
        await transport.aclose()


async def test_search_and_suggest_share_request_budget():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={"users": [], "has_more": False})

    transport = make_transport(server, max_requests=1)
    try:
        people = People(transport)
        assert (await people.search("query")).items == ()
        with pytest.raises(RequestBudgetExceeded):
            await people.suggest("query")
        assert len(calls) == 1
    finally:
        await transport.aclose()


async def test_rate_limit_is_not_retried_and_blocks_suggestions():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "120"})

    transport = make_transport(server)
    try:
        people = People(transport)
        with pytest.raises(RateLimited):
            await people.search("query")
        with pytest.raises(RateLimited):
            await people.suggest("query")
        assert len(calls) == 1
    finally:
        await transport.aclose()
