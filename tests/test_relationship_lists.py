"""Synthetic checks for app-recorded account-owned relationship list reads."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, RequestPolicy, Session
from async_instalib.config.relationship_lists import CloseFriendsListOptions, MutedListOptions
from async_instalib.exceptions import AccountPaused, ChallengeRequired
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


def blocked_response():
    return {
        "blocked_list": [
            {
                "pk": 123,
                "username": "synthetic",
                "friendship_status": {"blocking": True},
                "block_at": 1700000000,
                "is_auto_block_enabled": True,
            }
        ],
        "blocked_count": 1,
        "page_size": 200,
        "status": "ok",
    }


async def test_blocked_list_uses_recorded_read_and_preserves_block_metadata():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, json=blocked_response())

    client = transport(server)
    try:
        page = await RelationshipLists(client).blocked()
        assert len(requests) == 1
        assert requests[0].method == "GET"
        assert requests[0].url.path == "/api/v1/users/blocked_list/"
        assert not requests[0].url.query
        assert not requests[0].content
        assert page.items[0].id == "123"
        assert page.items[0].relationship.blocking is True
        assert page.items[0].raw["block_at"] == 1700000000
        assert page.items[0].raw["is_auto_block_enabled"] is True
        assert page.total_count == 1
        assert not page.has_more
    finally:
        await client.aclose()


async def test_blocked_list_empty_response():
    client = transport(
        lambda _: httpx.Response(200, json={"blocked_list": [], "blocked_count": 0, "status": "ok"})
    )
    try:
        page = await RelationshipLists(client).blocked()
        assert page.items == ()
        assert page.total_count == 0
    finally:
        await client.aclose()


async def test_relationship_list_requires_authentication():
    client = transport(lambda _: pytest.fail("Unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await RelationshipLists(client).blocked()
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"blocked_list": {}},
        {"blocked_list": [None]},
        {"blocked_list": [{"pk": "123"}]},
        {"blocked_count": None},
        {"blocked_count": True},
        {"blocked_count": -1},
        {"blocked_count": 2},
        {"next_max_id": "unrecorded"},
        {"has_more": True},
    ],
)
async def test_blocked_list_rejects_invalid_or_unestablished_partial_response(changes):
    payload = {**blocked_response(), **changes}
    client = transport(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).blocked()
    finally:
        await client.aclose()


async def test_relationship_list_obeys_existing_challenge_pause():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={"message": "challenge_required", "status": "fail"})

    client = transport(server)
    try:
        with pytest.raises(ChallengeRequired):
            await RelationshipLists(client).blocked()
        with pytest.raises(AccountPaused):
            await RelationshipLists(client).blocked()
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_close_friends_keeps_members_separate_from_suggestions_and_fb_count():
    calls = []

    def server(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/stories/private_stories/members/"
        assert request.url.params["is_list_creation"] == "false"
        assert request.url.params["pagination_enabled"] == "true"
        assert request.url.params["page_size"] == "40"
        return httpx.Response(
            200,
            json={
                "members": [{"pk": 123, "id": "123", "username": "synthetic_member"}],
                "suggested_users": [{"pk": 456, "username": "synthetic_suggestion"}],
                "fb_close_friends_count": 0,
                "status": "ok",
            },
        )

    client = transport(server)
    try:
        page = await RelationshipLists(client).close_friends()
        assert [person.username for person in page.items] == ["synthetic_member"]
        assert page.total_count is None
        assert page.raw["fb_close_friends_count"] == 0
        assert len(page.raw["suggested_users"]) == 1
        assert not page.has_more
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_close_friends_custom_page_size_and_empty_members():
    def server(request):
        assert request.url.params["page_size"] == "7"
        return httpx.Response(200, json={"members": [], "suggested_users": [], "status": "ok"})

    client = transport(server)
    try:
        page = await RelationshipLists(client).close_friends(CloseFriendsListOptions(page_size=7))
        assert page.items == ()
    finally:
        await client.aclose()


@pytest.mark.parametrize("members", [None, {}, [None], [{"pk": "1"}]])
async def test_close_friends_rejects_missing_or_invalid_members(members):
    client = transport(lambda _: httpx.Response(200, json={"members": members, "status": "ok"}))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).close_friends()
    finally:
        await client.aclose()


async def test_close_friends_rejects_unestablished_continuation():
    client = transport(
        lambda _: httpx.Response(200, json={"members": [], "next_max_id": "opaque", "status": "ok"})
    )
    try:
        with pytest.raises(ProtocolError, match="unestablished"):
            await RelationshipLists(client).close_friends()
    finally:
        await client.aclose()


@pytest.mark.parametrize("page_size", [True, 0, -1, 1.5, "40"])
def test_close_friends_page_size_validation(page_size):
    with pytest.raises(ValueError):
        CloseFriendsListOptions(page_size=page_size)


async def test_restricted_list_uses_recorded_read_and_preserves_state():
    calls = []

    def server(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/restrict_action/get_restricted_users/"
        assert not request.url.query
        assert not request.content
        return httpx.Response(
            200,
            json={
                "users": [
                    {
                        "pk": 123,
                        "username": "synthetic",
                        "friendship_status": {"is_restricted": True},
                    }
                ],
                "user_count": 1,
                "status": "ok",
            },
        )

    client = transport(server)
    try:
        page = await RelationshipLists(client).restricted()
        assert len(page.items) == 1
        assert page.items[0].relationship.is_restricted is True
        assert page.total_count == 1
        assert not page.has_more
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_restricted_list_does_not_infer_omitted_flags():
    client = transport(
        lambda _: httpx.Response(
            200, json={"users": [{"pk": 123, "username": "synthetic"}], "user_count": 1}
        )
    )
    try:
        page = await RelationshipLists(client).restricted()
        assert page.items[0].relationship is None
    finally:
        await client.aclose()


async def test_favorites_reads_members_without_fetching_suggestions_or_notifications():
    calls = []

    def server(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/friendships/feed_favorites/"
        assert not request.url.query
        assert not request.content
        return httpx.Response(
            200,
            json={
                "users": [{"pk": 123, "username": "synthetic"}],
                "has_more": False,
                "page_size": 1,
                "status": "ok",
            },
        )

    client = transport(server)
    try:
        page = await RelationshipLists(client).favorites()
        assert [person.id for person in page.items] == ["123"]
        assert page.items[0].relationship is None
        assert page.total_count is None
        assert not page.has_more
        assert len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("has_more", [None, "false", 0, True])
async def test_favorites_rejects_invalid_or_unestablished_continuation(has_more):
    client = transport(lambda _: httpx.Response(200, json={"users": [], "has_more": has_more}))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).favorites()
    finally:
        await client.aclose()


async def test_muted_returns_bounded_inert_layout_and_preserves_envelope():
    calls = []
    payload = json.dumps({"layout": {"bloks_payload": {"tree": {"action": "(do-not-execute)"}}}})

    def server(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/bloks/apps/com.instagram.growth.screens.muted_users/"
        form = parse_qs(request.content.decode())
        context = json.loads(form["bk_client_context"][0])
        assert context["styles_id"] == "instagram"
        assert context["theme_params"][0]["value"] == ["three_neutral_gray"]
        assert form["_uuid"] == [client.session.device.id]
        assert form["bloks_versioning_id"] == [client.session.profile.bloks_version]
        assert context["bloks_version"] == client.session.profile.bloks_version
        return httpx.Response(200, json={"payload": payload, "status": "ok"})

    client = transport(server)
    try:
        result = await RelationshipLists(client).muted()
        assert result["payload"] == payload
        assert result["status"] == "ok"
        assert result["decoded_payload"]["layout"]["bloks_payload"]["tree"] == {
            "action": "(do-not-execute)"
        }
        assert len(calls) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "not-json",
        "{}",
        "{} {}",
        json.dumps({"layout": {"bloks_payload": {"tree": []}}}),
        '{"layout":' + "[" * 70 + "0" + "]" * 70 + "}",
    ],
)
async def test_muted_rejects_invalid_or_excessive_encoded_payload(payload):
    client = transport(lambda _: httpx.Response(200, json={"payload": payload, "status": "ok"}))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).muted()
    finally:
        await client.aclose()


@pytest.mark.parametrize("value", [None, "", "line\nfeed", 123])
def test_muted_rendering_options_reject_invalid_metadata(value):
    with pytest.raises(ValueError):
        MutedListOptions(theme=value)


async def test_muted_accepts_direct_layout_envelope_without_inventing_payload_wrapper():
    layout = {"bloks_payload": {"tree": {"opaque_component": {"text": "synthetic"}}}}
    client = transport(lambda _: httpx.Response(200, json={"layout": layout, "status": "ok"}))
    try:
        result = await RelationshipLists(client).muted()
        assert result["layout"] == layout
        assert result["decoded_payload"] == {"layout": layout}
        assert result["status"] == "ok"
        assert "payload" not in result
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "response",
    [
        {"layout": []},
        {"layout": {"bloks_payload": {"tree": []}}},
        {"layout": {"bloks_payload": {"tree": {}}}, "payload": "{}"},
    ],
)
async def test_muted_rejects_invalid_or_ambiguous_direct_layout(response):
    client = transport(lambda _: httpx.Response(200, json=response))
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).muted()
    finally:
        await client.aclose()
