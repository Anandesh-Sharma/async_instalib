"""Recorded profile social-context request with explicit mutual/suggestion separation."""

from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, RequestPolicy, Session
from async_instalib.config.people import MutualFollowersOptions
from async_instalib.resources.people_lists import PeopleLists
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


async def test_mutual_followers_uses_recorded_context_and_excludes_suggestions():
    calls = []

    def server(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/discover/surface_with_su/"
        form = parse_qs(request.content.decode())
        assert form == {
            "module": ["profile_social_context"],
            "target_id": ["123"],
            "_uuid": [client.session.device.id],
            "mutual_followers_limit": ["12"],
        }
        return httpx.Response(
            200,
            json={
                "mutual_followers": [{"pk": 1, "id": "1", "username": "synthetic_mutual"}],
                "suggested_users": [{"pk": 2, "username": "synthetic_suggestion"}],
                "show_see_all_followers_button": True,
                "follow_ranking_token": "synthetic-ranking-token",
                "status": "ok",
            },
        )

    client = transport(server)
    try:
        page = await PeopleLists(client).mutual_followers("123")
        assert [person.username for person in page.items] == ["synthetic_mutual"]
        assert len(page.raw["suggested_users"]) == 1
        assert page.raw["follow_ranking_token"] == "synthetic-ranking-token"
        assert not page.has_more
        assert page.total_count is None
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_mutual_followers_empty_and_explicit_count_override():
    def server(request):
        form = parse_qs(request.content.decode())
        assert form["mutual_followers_limit"] == ["3"]
        assert form["module"] == ["synthetic_entry"]
        return httpx.Response(200, json={"mutual_followers": [], "status": "ok"})

    client = transport(server)
    try:
        options = MutualFollowersOptions(limit=3, module="synthetic_entry")
        assert (await PeopleLists(client).mutual_followers("123", options)).items == ()
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"mutual_followers": {}},
        {"mutual_followers": [None]},
        {"mutual_followers": [{"pk": 1}]},
        {"mutual_followers": [], "next_max_id": "unobserved"},
    ],
)
async def test_mutual_followers_rejects_invalid_or_unestablished_response(response):
    client = transport(lambda _: httpx.Response(200, json=response))
    try:
        with pytest.raises(ProtocolError):
            await PeopleLists(client).mutual_followers("123")
    finally:
        await client.aclose()


async def test_mutual_followers_requires_authentication():
    client = transport(lambda _: pytest.fail("Unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await PeopleLists(client).mutual_followers("123")
    finally:
        await client.aclose()


@pytest.mark.parametrize("identifier", ["../123", "", "0", "١٢٣"])
async def test_mutual_followers_validates_target_before_network(identifier):
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(ValueError):
            await PeopleLists(client).mutual_followers(identifier)
    finally:
        await client.aclose()


@pytest.mark.parametrize("changes", [{"limit": 0}, {"limit": True}, {"module": "bad\nvalue"}])
def test_mutual_followers_options_validation(changes):
    with pytest.raises(ValueError):
        replace(MutualFollowersOptions(), **changes)
