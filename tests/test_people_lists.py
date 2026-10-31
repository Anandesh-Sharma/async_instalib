"""Recorded list request contracts with synthetic people and no live account traffic."""

import json
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, RequestPolicy, Session
from async_instalib.config.people import PeopleListOptions
from async_instalib.exceptions import APIError
from async_instalib.models.pagination import Continuation, PageContext
from async_instalib.resources.people_lists import PeopleLists
from async_instalib.transport import Transport


def transport(handler, *, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="42") if authenticated else Session()
    )
    return Transport(
        session,
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


def envelope(operation, *, users=None, cursor=None, **fields):
    return {
        "data": {
            f"xdt_api__v1__friendships__{operation}": {
                "users": [{"id": "123", "username": "synthetic"}] if users is None else users,
                "next_max_id": cursor,
                **fields,
            }
        }
    }


async def test_followers_uses_recorded_shape_and_exposes_visibility_limit():
    calls = []

    def server(request):
        calls.append(request)
        form = parse_qs(request.content.decode())
        assert form["fb_api_req_friendly_name"] == ["FollowersList"]
        assert form["client_doc_id"] == ["284797047911918316998205836755"]
        variables = json.loads(form["variables"][0])
        assert variables["user_id"] == "123"
        assert variables["search_surface"] == "follow_list_page"
        assert variables["request_data"] == {"rank_token": "", "enableGroups": True}
        assert variables["include_unseen_count"] is False
        assert variables["skip_friendship_followers_fields"] is True
        assert variables["query"] == ""
        assert "count" not in variables and "max_id" not in variables
        users = [{"id": str(i + 1), "username": f"synthetic_{i}"} for i in range(50)]
        return httpx.Response(
            200,
            json=envelope("followers", users=users, should_limit_list_of_followers=True),
        )

    client = transport(server)
    try:
        page = await PeopleLists(client).followers("123")
        assert len(page.items) == 50
        assert page.items[0].username == "synthetic_0"
        assert page.items[-1].id == "50"
        assert page.raw["should_limit_list_of_followers"] is True
        assert page.total_count is None
        assert not page.has_more
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_following_paginates_only_on_explicit_call_and_keeps_every_row():
    requests = []

    def server(request):
        form = parse_qs(request.content.decode())
        variables = json.loads(form["variables"][0])
        requests.append(variables)
        assert form["fb_api_req_friendly_name"] == ["FollowingList"]
        assert form["client_doc_id"] == ["16104639286363954576550227636"]
        assert variables["include_unseen_count"] is False
        assert variables["include_profile_update_info"] is False
        assert variables["skip_friendship_followers_fields"] is False
        assert variables["request_data"]["rank_token"] == ""
        assert variables["request_data"]["includes_hashtags"] is True
        assert variables["request_data"]["page_size"] is None
        assert variables["request_data"]["limit"] is None
        first = len(requests) == 1
        assert variables.get("max_id") == (None if first else "opaque-synthetic-cursor")
        users = [{"id": str(i + 1), "username": f"synthetic_{i}"} for i in range(25)]
        return httpx.Response(
            200,
            json=envelope(
                "following",
                users=users,
                cursor="opaque-synthetic-cursor" if first else None,
                follow_ranking_token="response-token-not-replayed",
            ),
        )

    client = transport(server)
    try:
        lists = PeopleLists(client)
        first = await lists.following("123")
        assert len(first.items) == 25
        assert len(requests) == 1
        assert first.has_more
        assert first.continuation.context == PageContext("following", "42", target="123")
        second = await lists.following("123", PeopleListOptions(cursor=first.continuation))
        assert len(second.items) == 25
        assert not second.has_more
        assert len(requests) == 2
    finally:
        await client.aclose()


@pytest.mark.parametrize("operation", ["followers", "following"])
@pytest.mark.parametrize(
    "changes",
    [{"account_id": "other"}, {"operation": "search"}, {"target": "456"}, {"query": "other"}],
)
async def test_lists_reject_foreign_cursor_before_network(changes, operation):
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        context = PageContext(operation, "42", target="123")
        cursor = Continuation("opaque", replace(context, **changes))
        with pytest.raises(ValueError, match="different operation"):
            await getattr(PeopleLists(client), operation)("123", PeopleListOptions(cursor=cursor))
    finally:
        await client.aclose()


@pytest.mark.parametrize("operation", ["followers", "following"])
async def test_lists_require_authentication(operation):
    client = transport(lambda _: pytest.fail("Unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await getattr(PeopleLists(client), operation)("123")
    finally:
        await client.aclose()


@pytest.mark.parametrize("identifier", ["../123", "١٢٣", "", "0", "-1"])
async def test_lists_validate_target_before_network(identifier):
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(ValueError):
            await PeopleLists(client).following(identifier)
    finally:
        await client.aclose()


@pytest.mark.parametrize("target", ["42", "123"])
async def test_followers_query_preserves_explicit_target_and_recorded_request_shape(target):
    query = "किरण & café+/?"

    def server(request):
        form = parse_qs(request.content.decode())
        assert form["fb_api_req_friendly_name"] == ["FollowersList"]
        variables = json.loads(form["variables"][0])
        assert variables["query"] == query
        assert variables["user_id"] == target
        assert variables["request_data"] == {"rank_token": "", "enableGroups": True}
        return httpx.Response(200, json=envelope("followers", users=[]))

    client = transport(server)
    try:
        page = await PeopleLists(client).followers(target, PeopleListOptions(query=query))
        assert page.items == ()
        assert not page.has_more
    finally:
        await client.aclose()


async def test_following_filter_uses_recorded_top_level_query_and_scope():
    query = "किरण & café+/?"

    def server(request):
        variables = json.loads(parse_qs(request.content.decode())["variables"][0])
        assert variables["query"] == query
        assert variables["request_data"]["query"] is None
        return httpx.Response(200, json=envelope("following", cursor="query-cursor"))

    client = transport(server)
    try:
        result = await PeopleLists(client).following("123", PeopleListOptions(query=query))
        assert result.continuation.context.query == query
        with pytest.raises(ValueError, match="different operation"):
            await PeopleLists(client).following(
                "123", PeopleListOptions(query="different", cursor=result.continuation)
            )
    finally:
        await client.aclose()


@pytest.mark.parametrize("query", ["line\nfeed", "null\x00byte", "delete\x7f"])
@pytest.mark.parametrize("operation", ["followers", "following"])
async def test_list_filter_rejects_control_characters(query, operation):
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(ValueError):
            await getattr(PeopleLists(client), operation)("123", PeopleListOptions(query=query))
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "value",
    [
        {"users": [], "next_max_id": 3},
        {"users": [], "next_max_id": ""},
        {"users": []},
        {"users": {}, "next_max_id": None},
        {"users": [None], "next_max_id": None},
        {"users": [{"id": "1", "username": "x", "is_private": "false"}], "next_max_id": None},
    ],
)
async def test_invalid_list_response_is_rejected(value):
    client = transport(
        lambda _: httpx.Response(200, json={"data": {"xdt_api__v1__friendships__following": value}})
    )
    try:
        with pytest.raises(ProtocolError):
            await PeopleLists(client).following("123")
    finally:
        await client.aclose()


async def test_optional_field_errors_are_not_silently_ignored():
    result = envelope("following")
    result["errors"] = [
        {"message": "execution error", "path": ["xdt_api__v1__friendships__following", "users", 0]}
    ]
    client = transport(lambda _: httpx.Response(200, json=result))
    try:
        with pytest.raises(APIError):
            await PeopleLists(client).following("123")
    finally:
        await client.aclose()


async def test_followers_cursor_chain_preserves_overlaps_without_implicit_fetch():
    requests = []

    def server(request):
        form = parse_qs(request.content.decode())
        assert form["fb_api_req_friendly_name"] == ["FollowersList"]
        variables = json.loads(form["variables"][0])
        requests.append(variables)
        page_index = len(requests) - 1
        assert variables.get("max_id") == (None if page_index == 0 else f"cursor-{page_index}")
        assert variables["request_data"] == {"rank_token": "", "enableGroups": True}
        assert variables["query"] == ""
        assert variables["user_id"] == "123"
        assert variables["include_unseen_count"] is False
        assert "rank_token" not in variables
        users = [
            {"id": str(page_index * 20 + index + 1), "username": f"synthetic_{page_index}_{index}"}
            for index in range(25)
        ]
        return httpx.Response(
            200,
            json=envelope(
                "followers",
                users=users,
                cursor=f"cursor-{page_index + 1}" if page_index < 3 else None,
                follow_ranking_token="not-a-pagination-input",
                should_limit_list_of_followers=False,
            ),
        )

    client = transport(server)
    try:
        lists = PeopleLists(client)
        cursor = None
        seen = set()
        for index in range(4):
            page = await lists.followers("123", PeopleListOptions(cursor=cursor))
            assert len(requests) == index + 1
            assert len(page.items) == 25
            ids = {person.id for person in page.items}
            assert len(seen & ids) == (0 if index == 0 else 5)
            seen.update(ids)
            assert page.total_count is None
            assert page.has_more is (index < 3)
            cursor = page.continuation
            if cursor is not None:
                assert cursor.context == PageContext("followers", "42", target="123")
                assert cursor.rank_token is None
        assert len(seen) == 85
        assert len(requests) == 4
    finally:
        await client.aclose()
