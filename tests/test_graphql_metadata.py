"""Operation-local headers required by recorded Pando activity mutations."""

from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import (
    AccountPaused,
    ChallengeRequired,
    ClientOptions,
    LoginRequired,
    RateLimited,
    RequestPolicy,
    Session,
)
from async_instalib.config.relationships import MuteOptions
from async_instalib.resources.relationships import Relationships
from async_instalib.transport import Transport
from async_instalib.transport.graphql import GraphQLRequest

LEGACY_HEADERS = (
    "x-client-doc-id",
    "x-root-field-name",
    "x-ig-validate-null-in-legacy-dict",
    "x-ig-validate-null-in-legacy-eager-dict",
    "x-graphql-client-library",
)


@pytest.mark.parametrize("header", [*LEGACY_HEADERS, "X-FB-Friendly-Name"])
def test_operation_owned_headers_cannot_leak_through_global_client_configuration(header):
    with pytest.raises(ValueError, match="managed"):
        ClientOptions(extra_headers={header: "unexpected"})


@pytest.mark.parametrize("root", ["", "x\r\nInjected: value", "x.y", "x-y", "résultat", True])
def test_root_metadata_requires_a_valid_graphql_field(root):
    with pytest.raises(ValueError):
        GraphQLRequest("SyntheticMutation", {}, client_doc_id="123", legacy_root_field=root)


def test_legacy_metadata_requires_client_document_identity():
    with pytest.raises(ValueError):
        GraphQLRequest(
            "SyntheticMutation", {}, doc_id="123", legacy_root_field="synthetic_mutation"
        )


async def test_activity_metadata_is_scoped_per_operation_and_does_not_reach_reads():
    seen = []

    def server(request):
        if request.url.path.endswith("friendships/show/42/"):
            assert all(key not in request.headers for key in LEGACY_HEADERS)
            assert "x-fb-friendly-name" not in request.headers
            seen.append("rest")
            return httpx.Response(200, json={"status": "ok", "following": False})
        form = parse_qs(request.content.decode())
        operation = form["fb_api_req_friendly_name"][0]
        seen.append(operation)
        if operation == "SyntheticRead":
            assert all(key not in request.headers for key in LEGACY_HEADERS)
            assert request.headers["x-fb-friendly-name"] == "SyntheticRead"
            return httpx.Response(200, json={"data": {"read": True}})
        root, document = {
            "MuteMediaRepostUser": ("xdt_mute_media_repost_user", "414453952317846787797149409804"),
            "UnmuteMediaRepostUser": (
                "xdt_unmute_media_repost_user",
                "10341362783250650041331021829",
            ),
        }[operation]
        assert {key: request.headers.get(key) for key in LEGACY_HEADERS} == {
            "x-client-doc-id": document,
            "x-root-field-name": root,
            "x-ig-validate-null-in-legacy-dict": "true",
            "x-ig-validate-null-in-legacy-eager-dict": "true",
            "x-graphql-client-library": "pando",
        }
        assert form["client_doc_id"] == [document]
        return httpx.Response(
            200,
            json={"data": {root: {"is_muting_media_reposts": operation == "MuteMediaRepostUser"}}},
        )

    transport = Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )
    try:
        relationships = Relationships(transport)
        assert (
            await relationships.mute("42", MuteOptions(content="activity"))
        ).is_muting_media_reposts is True
        await transport.graphql(GraphQLRequest("SyntheticRead", {}, client_doc_id="456"))
        await relationships.get("42")
        assert (
            await relationships.unmute("42", MuteOptions(content="activity"))
        ).is_muting_media_reposts is False
        assert seen == ["MuteMediaRepostUser", "SyntheticRead", "rest", "UnmuteMediaRepostUser"]
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "category,first_error,next_error",
    [
        ("challenge_required", ChallengeRequired, AccountPaused),
        ("rate_limit_error", RateLimited, RateLimited),
        ("login_required", LoginRequired, LoginRequired),
    ],
)
async def test_legacy_metadata_does_not_bypass_account_error_guards(
    category, first_error, next_error
):
    calls = []

    def server(request):
        calls.append(request)
        assert request.headers["x-root-field-name"] == "xdt_mute_media_repost_user"
        return httpx.Response(
            200,
            headers={"retry-after": "120"},
            json={
                "data": {"xdt_mute_media_repost_user": None},
                "errors": [{"extensions": {"error_type": category}}],
            },
        )

    transport = Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )
    try:
        relationships = Relationships(transport)
        with pytest.raises(first_error):
            await relationships.mute("42", MuteOptions(content="activity"))
        with pytest.raises(next_error):
            await relationships.get("42")
        assert len(calls) == 1
    finally:
        await transport.aclose()
