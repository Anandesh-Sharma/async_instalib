"""Synthetic relationship exchanges; no mutation reaches a live account."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.config.relationships import (
    ActionOptions,
    BlockOptions,
    MuteOptions,
    NotificationOptions,
)
from async_instalib.exceptions import APIError, NetworkError, RequestBudgetExceeded
from async_instalib.resources.relationships import Relationships
from async_instalib.transport import Transport


def make_transport(handler, *, authenticated=True, max_requests=60):
    session = (
        Session(authorization="Bearer synthetic", user_id="11") if authenticated else Session()
    )
    options = ClientOptions(policy=RequestPolicy(min_interval=0, max_requests=max_requests))
    return Transport(session, options, httpx.MockTransport(handler))


def signed_fields(request):
    form = parse_qs(request.content.decode())
    assert set(form) == {"signed_body"}
    assert form["signed_body"][0].startswith("SIGNATURE.")
    return json.loads(form["signed_body"][0].split(".", 1)[1])


async def test_get_relationship_preserves_unknown_flags():
    def server(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v1/friendships/show/42/"
        return httpx.Response(
            200, json={"following": False, "outgoing_request": True, "status": "ok"}
        )

    transport = make_transport(server)
    try:
        result = await Relationships(transport).get("42")
        assert result.user_id == "42"
        assert result.following is False
        assert result.outgoing_request is True
        assert result.blocking is None
    finally:
        await transport.aclose()


async def test_follow_uses_session_identity_and_returns_pending_state():
    def server(request):
        assert request.url.path == "/api/v1/friendships/create/42/"
        fields = signed_fields(request)
        assert fields["user_id"] == "42"
        assert fields["_uid"] == "11"
        assert fields["_uuid"] == transport.session.device.id
        assert fields["device_id"] == transport.session.device.android_id
        assert fields["include_follow_friction_check"] == "1"
        assert fields["container_module"] == "profile"
        assert "nav_chain" not in fields
        return httpx.Response(
            200,
            json={
                "friendship_status": {"following": False, "outgoing_request": True},
                "status": "ok",
            },
        )

    transport = make_transport(server)
    try:
        result = await Relationships(transport).follow("42")
        assert result.following is False
        assert result.outgoing_request is True
    finally:
        await transport.aclose()


@pytest.mark.parametrize("action", ["mute", "unmute"])
@pytest.mark.parametrize(
    "content,field", [("posts", "target_posts_author_id"), ("stories", "target_reel_author_id")]
)
async def test_mute_changes_only_requested_content(action, content, field):
    def server(request):
        assert request.url.path == f"/api/v1/friendships/{action}_posts_or_story_from_follow/"
        fields = signed_fields(request)
        assert fields[field] == "42"
        assert set(fields) == {"_uid", "_uuid", field, "container_module"}
        assert fields["container_module"] == "media_mute_sheet"
        return httpx.Response(200, json={"friendship_status": {"following": True}, "status": "ok"})

    transport = make_transport(server)
    try:
        result = await getattr(Relationships(transport), action)("42", MuteOptions(content=content))
        assert result.following is True
        assert result.muting is None  # Never infer a returned state from the requested action.
    finally:
        await transport.aclose()


@pytest.mark.parametrize("user_id", [None, True, 42, "", "0", "-1", "4/2", "42?extra=1", "４２"])
async def test_invalid_target_never_reaches_network(user_id):
    transport = make_transport(lambda _: pytest.fail("unexpected request"))
    try:
        with pytest.raises(ValueError):
            await Relationships(transport).follow(user_id)
    finally:
        await transport.aclose()


async def test_unauthenticated_mutation_never_reaches_network():
    transport = make_transport(lambda _: pytest.fail("unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await Relationships(transport).follow("42")
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "_uid",
        "_uuid",
        "device_id",
        "target_reel_author_id",
        "Authorization",
        "nav_chain",
        "signed_body",
    ],
)
def test_extra_action_parameters_cannot_override_identity_or_target(field):
    with pytest.raises(ValueError):
        ActionOptions(parameters={field: "unexpected"})
    with pytest.raises(ValueError):
        MuteOptions(parameters={field: "unexpected"})


def test_parameters_are_copied_and_hidden_from_repr():
    source = {"include_follow_friction_check": "1", "extra_flag": "private-marker"}
    options = ActionOptions(parameters=source)
    source["extra_flag"] = "changed"
    assert options.parameters["extra_flag"] == "private-marker"
    assert "private-marker" not in repr(options)
    with pytest.raises(TypeError):
        options.parameters["extra_flag"] = "changed"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok"},
        {"status": "ok", "friendship_status": {}},
        {"status": "ok", "friendship_status": {"unknown_field": True}},
        {"friendship_status": {"following": True}},
        {"status": "ok", "friendship_status": {"following": "false"}},
    ],
)
async def test_follow_does_not_claim_success_without_valid_state(payload):
    transport = make_transport(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(ProtocolError):
            await Relationships(transport).follow("42")
    finally:
        await transport.aclose()


async def test_ambiguous_network_failure_is_never_retried():
    calls = []

    def server(request):
        calls.append(request)
        raise httpx.ReadError("synthetic failure", request=request)

    transport = make_transport(server)
    try:
        with pytest.raises(NetworkError):
            await Relationships(transport).follow("42")
        assert len(calls) == 1
    finally:
        await transport.aclose()


async def test_mutations_share_transport_budget():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={"friendship_status": {"following": True}, "status": "ok"})

    transport = make_transport(server, max_requests=1)
    try:
        relationships = Relationships(transport)
        await relationships.follow("42")
        with pytest.raises(RequestBudgetExceeded):
            await relationships.mute("42")
        assert len(calls) == 1
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "content,endpoint",
    [("posts", "favorite"), ("stories", "favorite_for_stories"), ("reels", "favorite_for_clips")],
)
@pytest.mark.parametrize(
    "mode,preference", [("all", "all"), ("relevant", "default"), ("off", "none")]
)
async def test_notifications_return_acknowledgement_without_inventing_state(
    content, endpoint, mode, preference
):
    def server(request):
        assert request.url.path == f"/api/v1/friendships/{endpoint}/42/"
        assert signed_fields(request) == {
            "preference": preference,
            "user_id": "42",
            "_uid": "11",
            "_uuid": transport.session.device.id,
        }
        return httpx.Response(200, json={"status": "ok", "extra": "private-marker"})

    transport = make_transport(server)
    try:
        result = await Relationships(transport).notifications(
            "42", NotificationOptions(content, mode)
        )
        assert result.status == "ok"
        assert result.user_id == "42"
        assert not hasattr(result, "following")
        assert "private-marker" not in repr(result)
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "mode,preference", [("all", "all"), ("relevant", "default"), ("off", "none")]
)
async def test_live_notifications_use_unsigned_recorded_form(mode, preference):
    def server(request):
        assert request.url.path == "/api/v1/live/42/set_subscription_preference/"
        assert parse_qs(request.content.decode()) == {
            "preference": [preference],
            "_uuid": [transport.session.device.id],
        }
        return httpx.Response(200, json={"status": "ok"})

    transport = make_transport(server)
    try:
        result = await Relationships(transport).notifications(
            "42", NotificationOptions("live", mode)
        )
        assert result.status == "ok"
    finally:
        await transport.aclose()


async def test_captured_restrict_failure_propagates_without_retry():
    calls = []

    def server(request):
        calls.append(request)
        assert request.url.path == "/api/v1/restrict_action/restrict_many/"
        assert signed_fields(request) == {
            "user_ids": "42",
            "_uid": "11",
            "_uuid": transport.session.device.id,
            "container_module": "restrict_half_sheet",
        }
        return httpx.Response(500, json={"status": "fail", "message": "Restrict users failed"})

    transport = make_transport(server)
    try:
        with pytest.raises(APIError):
            await Relationships(transport).restrict(
                "42", ActionOptions(container_module="restrict_half_sheet")
            )
        assert len(calls) == 1
        assert transport.session.paused_reason is None
    finally:
        await transport.aclose()


async def test_unfollow_uses_recorded_fields_and_returns_server_state():
    def server(request):
        assert request.url.path == "/api/v1/friendships/destroy/42/"
        fields = signed_fields(request)
        assert fields["container_module"] == "following_sheet"
        assert fields["include_follow_friction_check"] == "1"
        assert fields["user_id"] == "42"
        assert "device_id" not in fields
        assert "nav_chain" not in fields
        return httpx.Response(200, json={"status": "ok", "friendship_status": {"following": False}})

    transport = make_transport(server)
    try:
        result = await Relationships(transport).unfollow("42")
        assert result.following is False
        assert result.outgoing_request is None
    finally:
        await transport.aclose()


@pytest.mark.parametrize("auto_block", [True, False])
async def test_block_scope_is_explicit_and_rejection_is_not_retried(auto_block):
    calls = []

    def server(request):
        calls.append(request)
        assert request.url.path == "/api/v1/friendships/block/42/"
        fields = signed_fields(request)
        assert fields["user_id"] == "42"
        assert fields["is_auto_block_enabled"] == str(auto_block).lower()
        assert fields["surface"] == "profile"
        assert fields["container_module"] == "profile"
        return httpx.Response(
            400, json={"status": "fail", "message": "You can't block yourself and linked accounts"}
        )

    transport = make_transport(server)
    try:
        with pytest.raises(APIError):
            await Relationships(transport).block("42", BlockOptions(auto_block=auto_block))
        assert len(calls) == 1
        assert transport.session.paused_reason is None
    finally:
        await transport.aclose()
