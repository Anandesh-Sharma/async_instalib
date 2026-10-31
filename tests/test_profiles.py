import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.exceptions import LoginRequired, ProtocolError
from async_instalib.resources.profiles import Profiles
from async_instalib.transport import Transport


def transport(server, *, authenticated=True):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42") if authenticated else Session(),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


async def test_profile_progressive_fields_merge_and_use_current_device():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(
            200,
            content="\n".join(
                json.dumps(record)
                for record in [
                    {"status": "ok", "user": {"pk": 7, "id": "7", "username": "synthetic"}},
                    {"status": "ok", "user": {"id": "7", "follower_count": 3}},
                    {"status": "ok", "user": {"id": "7", "biography": "A synthetic bio"}},
                ]
            ),
        )

    client = transport(server)
    try:
        profile = await Profiles(client).by_id("7")
        assert profile.username == "synthetic"
        assert profile.follower_count == 3
        assert profile.biography == "A synthetic bio"
        assert profile.is_private is None
        assert len(requests) == 1
        assert requests[0].url.path == "/api/v1/users/7/info_stream/"
        assert parse_qs(requests[0].content.decode())["_uuid"] == [client.session.device.id]
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "records",
    [
        [{"user": {"pk": 8, "username": "wrong"}, "status": "ok"}],
        [{"user": {"pk": 7, "username": "synthetic"}}, {"user": {"id": "8"}}],
        [{"user": {"pk": 7, "username": "synthetic"}}, {"status": "ok"}],
        [{"user": {"pk": 7, "username": "synthetic", "follower_count": -1}}],
    ],
)
async def test_malformed_profile_never_returns_wrong_identity_or_partial_success(records):
    client = transport(
        lambda request: httpx.Response(200, content="\n".join(json.dumps(r) for r in records))
    )
    try:
        with pytest.raises(ProtocolError):
            await Profiles(client).by_id("7")
    finally:
        await client.aclose()


async def test_unauthenticated_profile_does_not_dispatch():
    def server(request):
        pytest.fail("Unauthenticated profile request reached the wire")

    client = transport(server, authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await Profiles(client).by_id("7")
        with pytest.raises(ValueError):
            await Profiles(client).by_id("../7")
    finally:
        await client.aclose()


async def test_related_uses_encoded_target_and_does_not_invent_next_page():
    from async_instalib.config.content import RelatedOptions

    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "user": {"id": "7", "pk": 7},
                "users": [{"id": "8", "username": "suggested"}],
                "has_chaining": True,
                "follow_ranking_token": "opaque",
            },
        )

    client = transport(server)
    try:
        page = await Profiles(client).related(
            "7", RelatedOptions(username="example.name", parameters={"module": "a&b"})
        )
        assert page.items[0].username == "suggested"
        assert not page.has_more
        assert len(requests) == 1
        assert requests[0].url.params["target_id"] == "7"
        assert requests[0].url.params["target_username"] == "example.name"
        assert requests[0].url.params["module"] == "a&b"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"users": None},
        {"users": [{"username": "missing_id"}]},
        {"users": [], "user": {"id": "8"}},
    ],
)
async def test_related_rejects_malformed_data(payload):
    client = transport(lambda request: httpx.Response(200, json={"status": "ok", **payload}))
    try:
        with pytest.raises(ProtocolError):
            await Profiles(client).related("7")
    finally:
        await client.aclose()
