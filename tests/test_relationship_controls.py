"""Synthetic tests for recorded person controls beyond follow and mute."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ActionOptions, ClientOptions, NetworkError, ProtocolError, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.resources.relationships import Relationships
from async_instalib.transport import Transport


def make_transport(handler):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


def signed_fields(request):
    form = parse_qs(request.content.decode())
    assert set(form) == {"signed_body"}
    assert form["signed_body"][0].startswith("SIGNATURE.")
    return json.loads(form["signed_body"][0].split(".", 1)[1])


def returned_state(restricted):
    return {
        "status": "ok",
        "users": [
            {
                "pk": "42",
                "id": "42",
                "username": "synthetic",
                "friendship_status": {"is_restricted": restricted},
            }
        ],
    }


@pytest.mark.parametrize("pending", [False, True, None])
async def test_cancel_request_matches_capture_and_preserves_actual_pending_state(pending):
    calls = []

    def server(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/friendships/destroy/42/"
        assert signed_fields(request) == {
            "include_follow_friction_check": "1",
            "user_id": "42",
            "radio_type": "wifi-none",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "friending_center",
        }
        state = {"following": False, "is_private": True}
        if pending is not None:
            state["outgoing_request"] = pending
        return httpx.Response(200, json={"status": "ok", "friendship_status": state})

    transport = make_transport(server)
    try:
        result = await Relationships(transport).cancel_request("42")
        assert result.outgoing_request is pending
        assert result.following is False
        assert result.is_private is True
        assert len(calls) == 1
    finally:
        await transport.aclose()


async def test_cancel_request_respects_context_options_without_retrying_uncertain_outcome():
    calls = []

    def server(request):
        calls.append(request)
        fields = signed_fields(request)
        assert fields["container_module"] == "profile"
        assert fields["radio_type"] == "wifi-none"
        assert fields["include_follow_friction_check"] == "0"
        assert fields["user_id"] == "42"
        raise httpx.ReadError("synthetic connection loss", request=request)

    transport = make_transport(server)
    try:
        with pytest.raises(NetworkError):
            await Relationships(transport).cancel_request(
                "42", ActionOptions(parameters={"include_follow_friction_check": "0"})
            )
        assert len(calls) == 1
    finally:
        await transport.aclose()


@pytest.mark.parametrize("action", ["restrict", "unrestrict"])
async def test_restriction_returns_matching_target_state_and_recorded_form(action):
    def server(request):
        if action == "restrict":
            assert request.url.path == "/api/v1/restrict_action/restrict_many/"
            assert signed_fields(request) == {
                "user_ids": "42",
                "_uid": "11",
                "_uuid": transport.session.device.id,
                "container_module": "profile",
            }
        else:
            assert request.url.path == "/api/v1/restrict_action/unrestrict/"
            assert parse_qs(request.content.decode()) == {
                "target_user_id": ["42"],
                "_uuid": [transport.session.device.id],
                "container_module": ["profile"],
            }
        return httpx.Response(200, json=returned_state(action == "restrict"))

    transport = make_transport(server)
    try:
        result = await getattr(Relationships(transport), action)("42")
        assert result.is_restricted is (action == "restrict")
        assert result.following is None
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "users",
    [
        [],
        [None],
        [{"pk": "43", "username": "other", "friendship_status": {"is_restricted": True}}],
        [{"pk": "42", "username": "synthetic"}],
    ],
)
async def test_restriction_refuses_unconfirmed_or_wrong_target_state(users):
    transport = make_transport(lambda _: httpx.Response(200, json={"status": "ok", "users": users}))
    try:
        with pytest.raises(ProtocolError):
            await Relationships(transport).restrict("42")
    finally:
        await transport.aclose()


async def test_block_returns_actual_relationship_instead_of_inferred_acknowledgement():
    transport = make_transport(
        lambda _: httpx.Response(
            200, json={"status": "ok", "friendship_status": {"blocking": True, "following": False}}
        )
    )
    try:
        result = await Relationships(transport).block("42")
        assert result.blocking is True
        assert result.following is False
        assert result.is_restricted is None
    finally:
        await transport.aclose()


async def test_unblock_uses_fresh_request_id_and_returns_actual_server_flags():
    request_ids = []

    def server(request):
        from uuid import UUID

        assert request.url.path == "/api/v1/friendships/unblock/42/"
        fields = signed_fields(request)
        assert UUID(fields["client_request_id"]).version == 4
        request_ids.append(fields.pop("client_request_id"))
        assert fields == {
            "user_id": "42",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "blocked_list",
        }
        return httpx.Response(200, json={"status": "ok", "friendship_status": {"blocking": False}})

    transport = make_transport(server)
    try:
        for _ in range(2):
            result = await Relationships(transport).unblock("42")
            assert result.blocking is False
            assert result.following is None
        assert len(set(request_ids)) == 2
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "flag",
    [
        "is_private",
        "is_feed_favorite",
        "is_muting_notes",
        "is_muting_media_notes",
        "is_muting_media_reposts",
        "is_muting_friend_map",
        "is_muting_friend_map_location",
        "is_muting_quick_snap",
        "is_blocking_reel",
        "subscribed",
    ],
)
def test_observed_relationship_flags_preserve_unknown_and_reject_coercion(flag):
    from async_instalib.models.people import Relationship

    assert getattr(Relationship.from_dict({}), flag) is None
    assert getattr(Relationship.from_dict({flag: False}), flag) is False
    assert getattr(Relationship.from_dict({flag: True}), flag) is True
    with pytest.raises(ValueError):
        Relationship.from_dict({flag: "false"})


@pytest.mark.parametrize(
    "action,endpoint",
    [("add_close_friend", "add_member"), ("remove_close_friend", "remove_member")],
)
async def test_close_friend_change_is_single_member_delta_not_list_replacement(action, endpoint):
    def server(request):
        assert request.url.path == f"/api/v1/stories/private_stories/{endpoint}/"
        assert parse_qs(request.content.decode()) == {
            "module": ["audience_selection"],
            "source": ["settings"],
            "user_id": ["42"],
            "_uuid": [transport.session.device.id],
        }
        return httpx.Response(200, json={"status": "ok"})

    transport = make_transport(server)
    try:
        result = await getattr(Relationships(transport), action)("42")
        assert result.status == "ok"
        assert result.user_id == "42"
        assert result.action == action
        assert not hasattr(result, "is_bestie")
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "action,module",
    [("add_favorite", "following_sheet"), ("remove_favorite", "favorites_management")],
)
async def test_favorite_change_uses_one_member_delta_and_checks_response_identity(action, module):
    def server(request):
        assert request.url.path == "/api/v1/friendships/update_feed_favorites/"
        fields = signed_fields(request)
        assert fields["add"] == (["42"] if action == "add_favorite" else [])
        assert fields["remove"] == (["42"] if action == "remove_favorite" else [])
        assert fields["block_on_empty_thread_creation"] == "false"
        assert fields["module"] == module
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "user_id_and_relationship_infos": [
                    {"user_id": "42", "relationship_info": {"is_feed_favorite": True}}
                ],
            },
        )

    transport = make_transport(server)
    try:
        result = await getattr(Relationships(transport), action)("42")
        assert result.is_feed_favorite is True
        assert result.following is None
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [None],
        [{"user_id": "43", "relationship_info": {"is_feed_favorite": True}}],
        [{"user_id": "42", "relationship_info": {}}],
    ],
)
async def test_favorite_rejects_missing_or_mismatched_state(rows):
    transport = make_transport(
        lambda _: httpx.Response(200, json={"status": "ok", "user_id_and_relationship_infos": rows})
    )
    try:
        with pytest.raises(ProtocolError):
            await Relationships(transport).add_favorite("42")
    finally:
        await transport.aclose()


async def test_approve_request_uses_notification_context_and_actual_returned_state():
    def server(request):
        assert request.url.path == "/api/v1/friendships/approve/42/"
        assert signed_fields(request) == {
            "include_follow_friction_check": "0",
            "check_pastis": "true",
            "has_seen_uk_osa_prompt": "true",
            "user_id": "42",
            "radio_type": "wifi-none",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "newsfeed_you",
        }
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "friendship_status": {"incoming_request": False, "followed_by": True},
            },
        )

    transport = make_transport(server)
    try:
        result = await Relationships(transport).approve_request("42")
        assert result.incoming_request is False
        assert result.followed_by is True
        assert result.following is None
    finally:
        await transport.aclose()


@pytest.mark.parametrize("model", ["summary", "profile"])
@pytest.mark.parametrize("identifier", [4242, "4242", None])
def test_person_exposes_normalized_secondary_identity(model, identifier):
    from async_instalib.models.people import PersonProfile, PersonSummary

    parser = PersonSummary if model == "summary" else PersonProfile
    result = parser.from_dict({"pk": "42", "username": "synthetic", "fbid_v2": identifier})
    assert result.fbid_v2 == ("4242" if identifier is not None else None)
    assert parser.from_dict({"pk": "42", "username": "synthetic"}).fbid_v2 is None


@pytest.mark.parametrize("identifier", [True, False, 0, "0", -1, "not-a-number", 1.5])
def test_person_rejects_malformed_secondary_identity(identifier):
    from async_instalib.models.people import PersonSummary

    with pytest.raises(ValueError):
        PersonSummary.from_dict({"pk": "42", "username": "synthetic", "fbid_v2": identifier})


async def test_remove_follower_changes_incoming_relation_without_inventing_unfollow():
    def server(request):
        assert request.url.path == "/api/v1/friendships/remove_follower/42/"
        assert signed_fields(request) == {
            "include_follow_friction_check": "0",
            "user_id": "42",
            "radio_type": "wifi-none",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "self_followers",
        }
        return httpx.Response(
            200,
            json={"status": "ok", "friendship_status": {"followed_by": False, "following": True}},
        )

    transport = make_transport(server)
    try:
        result = await Relationships(transport).remove_follower("42")
        assert result.followed_by is False
        assert result.following is True
    finally:
        await transport.aclose()


async def test_decline_request_matches_recorded_form_and_returns_actual_state():
    def server(request):
        assert request.url.path == "/api/v1/friendships/ignore/42/"
        assert signed_fields(request) == {
            "include_follow_friction_check": "0",
            "user_id": "42",
            "radio_type": "wifi-none",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "friending_center",
        }
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "friendship_status": {"incoming_request": False, "followed_by": False},
            },
        )

    transport = make_transport(server)
    try:
        state = await Relationships(transport).decline_request("42")
        assert state.incoming_request is False
        assert state.followed_by is False
        assert state.following is None
        assert state.blocking is None
    finally:
        await transport.aclose()


async def test_decline_failure_is_not_retried_or_reported_as_success():
    from async_instalib.exceptions import APIError

    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(500, json={"status": "fail"})

    transport = make_transport(server)
    try:
        with pytest.raises(APIError):
            await Relationships(transport).decline_request("42")
        assert len(calls) == 1
    finally:
        await transport.aclose()
