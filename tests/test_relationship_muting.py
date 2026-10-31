"""Recorded extended muting protocols, using synthetic account identities only."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.config.relationships import MuteOptions
from async_instalib.exceptions import APIError, LoginRequired, ProtocolError
from async_instalib.resources.relationships import Relationships
from async_instalib.transport import Transport


def make_transport(handler):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("action", ["mute", "unmute"])
async def test_notes_use_captured_signed_rest_and_preserve_state(action):
    def server(request):
        assert request.url.path == f"/api/v1/friendships/{action}_note/"
        form = parse_qs(request.content.decode())
        fields = json.loads(form["signed_body"][0].split(".", 1)[1])
        assert fields == {
            "user_id": "42",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "media_mute_sheet",
        }
        return httpx.Response(
            200, json={"status": "ok", "friendship_status": {"is_muting_notes": False}}
        )

    transport = make_transport(server)
    try:
        result = await getattr(Relationships(transport), action)("42", MuteOptions(content="notes"))
        assert result.is_muting_notes is False
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "content,action,operation,document,root,flag,route",
    [
        (
            "activity",
            "mute",
            "MuteMediaRepostUser",
            "414453952317846787797149409804",
            "xdt_mute_media_repost_user",
            "is_muting_media_reposts",
            "/graphql/query",
        ),
        (
            "activity",
            "unmute",
            "UnmuteMediaRepostUser",
            "10341362783250650041331021829",
            "xdt_unmute_media_repost_user",
            "is_muting_media_reposts",
            "/graphql/query",
        ),
        (
            "map_notes",
            "mute",
            "FriendsMapUserMute",
            "410922747613505651012983125813",
            "xdt_mute_friend_map_user",
            "is_muting_friend_map",
            "/graphql/query",
        ),
        (
            "map_notes",
            "unmute",
            "FriendsMapUserUnmute",
            "291357725114377867684511072045",
            "xdt_unmute_friend_map_user",
            "is_muting_friend_map",
            "/graphql/query",
        ),
        (
            "map_location",
            "mute",
            "FriendMapMuteUserLocationMutation",
            "193229921913023103313420678044",
            "xig_mute_friend_map_location",
            "is_muting_friend_map_location",
            "/graphql_www",
        ),
        (
            "map_location",
            "unmute",
            "FriendMapUnmuteUserLocationMutation",
            "7689563045696688235702775609",
            "xig_unmute_friend_map_location",
            "is_muting_friend_map_location",
            "/graphql_www",
        ),
    ],
)
async def test_graphql_muting_matches_recorded_operation_and_identity(
    content, action, operation, document, root, flag, route
):
    def server(request):
        assert request.url.path == route
        form = parse_qs(request.content.decode())
        assert form["client_doc_id"] == [document]
        assert form["fb_api_req_friendly_name"] == [operation]
        legacy_headers = {
            "x-client-doc-id": document,
            "x-root-field-name": root,
            "x-ig-validate-null-in-legacy-dict": "true",
            "x-ig-validate-null-in-legacy-eager-dict": "true",
            "x-graphql-client-library": "pando",
        }
        if content == "activity":
            assert {key: request.headers.get(key) for key in legacy_headers} == legacy_headers
        else:
            assert all(key not in request.headers for key in legacy_headers)
        assert set(form) == {
            "method",
            "format",
            "server_timestamps",
            "locale",
            "fb_api_req_friendly_name",
            "client_doc_id",
            "variables",
        }
        assert form["method"] == ["post"]
        assert form["format"] == ["json"]
        assert form["server_timestamps"] == ["true"]
        assert form["locale"] == ["user"]
        variables = json.loads(form["variables"][0])
        assert set(variables) == {"data"}
        data = variables["data"]
        if content == "map_location":
            assert data == {
                "target_user_igid": "42",
                "target_user_id": "4242",
                "entry_point": "PROFILE",
            }
        else:
            assert data["user_id"] == "42"
            if content == "activity":
                metadata = (
                    {"tracking_token": None, "floating_context_item_type": None}
                    if action == "mute"
                    else {
                        "ranking_info_token": None,
                        "inventory_source": None,
                        "nav_chain": None,
                        "event_source": None,
                        "container_module": None,
                        "carousel_index": None,
                    }
                )
                assert data == {"user_id": "42", **metadata}
        state = {flag: False}
        payload = state if content == "activity" else {"friendship_status": state}
        return httpx.Response(200, json={"data": {root: payload}})

    transport = make_transport(server)
    try:
        options = MuteOptions(
            content=content, fbid_v2="4242" if content == "map_location" else None
        )
        result = await getattr(Relationships(transport), action)("42", options)
        assert getattr(result, flag) is False  # Never infer flags from the requested action.
        assert result.user_id == "42"
        assert result.following is None
    finally:
        await transport.aclose()


@pytest.mark.parametrize("fbid", [None, True, "", "0", "-1", "bad"])
def test_map_location_requires_profile_secondary_identity(fbid):
    with pytest.raises(ValueError):
        MuteOptions(content="map_location", fbid_v2=fbid)


@pytest.mark.parametrize(
    "action,operation,document,root",
    [
        (
            "mute_quick_snap",
            "IGQuickSnapMuteUserMutation",
            "35558766283600583069591271069",
            "xdt_mute_quick_snap_user",
        ),
        (
            "unmute_quick_snap",
            "IGQuickSnapUnMuteUserMutation",
            "2175173217533977044046084064",
            "xdt_unmute_quick_snap_user",
        ),
    ],
)
async def test_quick_snap_returns_acknowledgement_for_explicit_null_error(
    action, operation, document, root
):
    def server(request):
        assert request.url.path == "/graphql_www"
        form = parse_qs(request.content.decode())
        assert form["fb_api_req_friendly_name"] == [operation]
        assert form["client_doc_id"] == [document]
        assert json.loads(form["variables"][0]) == {"input": {"target_user_id": "42"}}
        return httpx.Response(200, json={"data": {root: {"error": None}}})

    transport = make_transport(server)
    try:
        result = await getattr(Relationships(transport), action)("42")
        assert result.status == "ok"
        assert result.action == action
        assert not hasattr(result, "muting")
    finally:
        await transport.aclose()


@pytest.mark.parametrize("payload,error", [({}, ProtocolError), ({"error": "rejected"}, APIError)])
async def test_quick_snap_rejects_missing_or_non_null_error_without_retry(payload, error):
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"xdt_mute_quick_snap_user": payload}})

    transport = make_transport(server)
    try:
        with pytest.raises(error):
            await Relationships(transport).mute_quick_snap("42")
        assert len(calls) == 1
    finally:
        await transport.aclose()


async def test_quick_snap_requires_authentication_before_network():
    transport = make_transport(lambda _: pytest.fail("unexpected request"))
    transport.session.authorization = None
    try:
        with pytest.raises(LoginRequired):
            await Relationships(transport).mute_quick_snap("42")
    finally:
        await transport.aclose()


async def test_live_observed_null_activity_result_never_implies_success_or_retries():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": {"xdt_mute_media_repost_user": None},
                "extensions": {"is_final": True},
                "status": "ok",
            },
        )

    transport = make_transport(server)
    try:
        with pytest.raises(ProtocolError):
            await Relationships(transport).mute("42", MuteOptions(content="activity"))
        assert len(calls) == 1
    finally:
        await transport.aclose()
