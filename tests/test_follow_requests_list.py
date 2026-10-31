"""Recorded native incoming-request category, separate from notification previews."""

import json
from dataclasses import replace
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, RequestPolicy, Session
from async_instalib.config.relationship_lists import FollowRequestsListOptions
from async_instalib.resources.relationship_lists import RelationshipLists
from async_instalib.transport import Transport


def transport(server, *, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="42") if authenticated else Session()
    )
    return Transport(
        session,
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


def category():
    return {
        "name": "follow_requests",
        "cursor": None,
        "load_more_style": "FULLY_LOADED",
        "total_count": None,
        "see_all_enabled": True,
        "typed_suggestion_cards": [
            {
                "user_card": {
                    "user": {
                        "pk": "123",
                        "username": "synthetic",
                        "friendship_status": {"incoming_request": True},
                    }
                }
            }
        ],
    }


def envelope(value, account_id="42"):
    return {
        "data": {
            "fetch__XDTUserDict": {
                "id": account_id,
                "friending_center_info": {"categories": [value]},
            }
        }
    }


async def test_incoming_requests_uses_own_account_and_fresh_native_query_context():
    requests = []

    def server(request):
        assert request.method == "POST"
        assert request.url.path == "/graphql/query"
        form = parse_qs(request.content.decode())
        assert form["client_doc_id"] == ["42895048516799972088067729645"]
        assert form["fb_api_req_friendly_name"] == ["IGFriendingCenterTypedQuery"]
        assert form["purpose"] == ["fetch"]
        variables = json.loads(form["variables"][0])
        assert UUID(variables["requestUuid"]).version == 4
        assert variables["user_id"] == "42"
        assert variables["category"] == "follow_requests"
        assert variables["entryPoint"] == "self_follower_follow_request_row"
        assert variables["isTopCategoryFullyExpanded"] is False
        assert variables["forcedIds"] == []
        requests.append(variables)
        return httpx.Response(200, json=envelope(category()))

    client = transport(server)
    try:
        lists = RelationshipLists(client)
        first = await lists.incoming_requests()
        assert [person.id for person in first.items] == ["123"]
        assert first.items[0].relationship.incoming_request is True
        assert first.total_count is None
        assert not first.has_more  # see_all_enabled is not a paging indicator.
        assert len(requests) == 1
        await lists.incoming_requests()
        assert requests[0]["requestUuid"] != requests[1]["requestUuid"]
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "other_category"},
        {"load_more_style": "PAGINATED"},
        {"cursor": "opaque"},
        {"typed_suggestion_cards": {}},
        {"typed_suggestion_cards": [None]},
        {"typed_suggestion_cards": [{"other_card": {}}]},
        {"total_count": False},
        {"total_count": 0},
        {"total_count": 2},
    ],
)
async def test_incoming_requests_rejects_unknown_cards_or_paging(changes):
    client = transport(lambda _: httpx.Response(200, json=envelope({**category(), **changes})))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).incoming_requests()
    finally:
        await client.aclose()


async def test_incoming_requests_rejects_another_account_response():
    client = transport(lambda _: httpx.Response(200, json=envelope(category(), account_id="99")))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).incoming_requests()
    finally:
        await client.aclose()


async def test_incoming_requests_requires_authentication():
    client = transport(lambda _: pytest.fail("Unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await RelationshipLists(client).incoming_requests()
    finally:
        await client.aclose()


async def test_incoming_request_options_override_only_entry_context():
    def server(request):
        variables = json.loads(parse_qs(request.content.decode())["variables"][0])
        assert variables["entryPoint"] == "synthetic_entry"
        assert variables["isTopCategoryFullyExpanded"] is True
        assert variables["user_id"] == "42"
        return httpx.Response(200, json=envelope({**category(), "typed_suggestion_cards": []}))

    client = transport(server)
    try:
        options = FollowRequestsListOptions(entry_point="synthetic_entry", expand_top_category=True)
        page = await RelationshipLists(client).incoming_requests(options)
        assert page.items == ()
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes", [{"entry_point": ""}, {"entry_point": "bad\nvalue"}, {"expand_top_category": 1}]
)
def test_incoming_request_options_reject_invalid_context(changes):
    with pytest.raises(ValueError):
        replace(FollowRequestsListOptions(), **changes)
