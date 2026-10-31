"""Direct username streams through the public facade, with no search fallback."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import (
    APIError,
    ChallengeRequired,
    ClientOptions,
    Instagram,
    LoginRequired,
    PersonNotFound,
    ProfileOptions,
    ProtocolError,
    RequestPolicy,
    Session,
)


def client(handler):
    return Instagram(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


def stream(records):
    return httpx.Response(200, content="\n".join(json.dumps(record) for record in records))


async def test_username_uses_one_direct_request_and_merges_progressive_fields():
    paths = []

    def server(request):
        paths.append(request.url.path)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/users/target/usernameinfo_stream/"
        assert parse_qs(request.content.decode()) == {
            "entry_point": ["profile"],
            "from_module": ["deep_link_util"],
            "_uuid": [ig.session.device.id],
        }
        return stream(
            [
                {"status": "ok", "user": {"pk": 42, "username": "Target", "full_name": "Example"}},
                {"status": "ok", "user": {"id": "42", "follower_count": 5}},
                {
                    "status": "ok",
                    "user": {
                        "pk": "42",
                        "id": "42",
                        "username": "target",
                        "biography": "Bio",
                        "fbid_v2": 4242,
                    },
                },
            ]
        )

    async with client(server) as ig:
        person = await ig.people.by_username("@TARGET")
        assert person.id == "42"
        assert person.full_name == "Example"
        assert person.follower_count == 5
        assert person.biography == "Bio"
        assert person.fbid_v2 == "4242"
    assert len(paths) == 1


async def test_explicit_profile_options_preserve_by_id_fields_without_search():
    paths = []
    options = ProfileOptions(
        module="custom_module", entry_point="custom_entry", push_disabled=False
    )

    def server(request):
        paths.append(request.url.path)
        if request.url.path.endswith("usernameinfo_stream/"):
            assert parse_qs(request.content.decode())["from_module"] == ["deep_link_util"]
            return stream([{"user": {"pk": "42", "username": "target"}, "status": "ok"}])
        assert request.url.path == "/api/v1/users/42/info_stream/"
        assert parse_qs(request.content.decode()) == {
            "module": ["custom_module"],
            "entry_point": ["custom_entry"],
            "push_disabled": ["false"],
            "_uuid": [ig.session.device.id],
        }
        return stream(
            [
                {
                    "user": {"pk": "42", "username": "target", "biography": "Final details"},
                    "status": "ok",
                }
            ]
        )

    async with client(server) as ig:
        person = await ig.people.by_username("target", options)
        assert person.biography == "Final details"
    assert len(paths) == 2


async def test_plain_404_becomes_person_not_found_without_fallback_or_pause():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(404, json={"status": "fail", "message": "not found"})

    async with client(server) as ig:
        with pytest.raises(PersonNotFound) as error:
            await ig.people.by_username("target")
        assert error.value.info.status_code == 404
        assert ig.session.paused_reason is None
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status,payload,error_type",
    [
        (401, {"status": "fail", "message": "login_required"}, LoginRequired),
        (404, {"status": "fail", "message": "login_required"}, LoginRequired),
        (404, {"status": "fail", "message": "challenge_required"}, ChallengeRequired),
        (403, {"status": "fail", "message": "not allowed"}, APIError),
        (500, {"status": "fail"}, APIError),
    ],
)
async def test_other_failures_keep_their_existing_typed_errors(status, payload, error_type):
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(status, json=payload)

    async with client(server) as ig:
        with pytest.raises(error_type):
            await ig.people.by_username("target")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "records",
    [
        [{"user": {"pk": "42", "username": "someone_else"}}],
        [{"user": {"pk": "42", "id": "43", "username": "target"}}],
        [
            {"user": {"pk": "42", "username": "target"}},
            {"user": {"id": "43", "username": "target"}},
        ],
        [
            {"user": {"pk": "42", "username": "someone_else"}},
            {"user": {"pk": "42", "username": "target"}},
        ],
        [{"user": {"pk": "42", "username": "target"}}, {"user": {"username": "someone_else"}}],
        [{"user": {"pk": "42", "username": "target"}}, {"status": "ok"}],
        [{"user": {"username": "target"}}],
    ],
)
async def test_malformed_or_conflicting_stream_never_returns_another_identity(records):
    async with client(lambda _: stream(records)) as ig:
        with pytest.raises(ProtocolError):
            await ig.people.by_username("target")


async def test_late_stream_failure_is_not_masked_by_early_profile():
    async with client(
        lambda _: stream(
            [
                {"user": {"pk": "42", "username": "target"}, "status": "ok"},
                {"status": "fail", "message": "login_required"},
            ]
        )
    ) as ig:
        with pytest.raises(LoginRequired):
            await ig.people.by_username("target")
        assert not ig.session.authenticated


@pytest.mark.parametrize("username", ["", "@", ".", "..", "a/b", "a?x=1", "@a b", "a\n", "K", None])
async def test_username_validation_happens_before_http(username):
    async with client(lambda _: pytest.fail("unexpected HTTP")) as ig:
        with pytest.raises(ValueError):
            await ig.people.by_username(username)


async def test_invalid_profile_options_do_not_spend_a_request():
    async with client(lambda _: pytest.fail("unexpected HTTP")) as ig:
        with pytest.raises(ValueError, match="ProfileOptions"):
            await ig.people.by_username("target", object())


async def test_unauthenticated_direct_lookup_does_not_dispatch():
    async with client(lambda _: pytest.fail("unexpected HTTP")) as ig:
        ig.session.authorization = None
        with pytest.raises(LoginRequired):
            await ig.people.profiles.by_username("target")
