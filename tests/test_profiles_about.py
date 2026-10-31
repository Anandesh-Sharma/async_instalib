"""About this account GraphQL request shape and opaque Bloks metadata handling."""

import json
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, LoginRequired, ProtocolError, RequestPolicy, Session
from async_instalib.config.about import AboutOptions
from async_instalib.exceptions import AccountPaused, ChallengeRequired
from async_instalib.resources.profiles import Profiles
from async_instalib.transport import Transport
from async_instalib.transport.graphql import GraphQLRequest


def transport(server, *, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="42") if authenticated else Session()
    )
    return Transport(
        session,
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


def response(tree='{"layout":{"text":"synthetic localized metadata"}}'):
    return {
        "data": {
            "bloks_app": {
                "screen_content": {
                    "component": {"bundle": {"bloks_bundle_tree": tree}},
                    "unique_root_id": "synthetic-root",
                }
            }
        },
        "extensions": {"is_final": True},
    }


async def test_about_uses_recorded_root_route_and_preserves_opaque_metadata():
    observed = []
    payload = response()

    def server(request):
        observed.append(request)
        return httpx.Response(200, json=payload)

    client = transport(server)
    try:
        result = await Profiles(client).about("123")
        assert result == payload
        assert len(observed) == 1
        request = observed[0]
        assert str(request.url) == "https://i.instagram.com/graphql_www"
        assert request.headers["authorization"] == "Bearer synthetic"
        assert request.headers["x-fb-friendly-name"] == (
            "IGBloksAppRootQuery-com.bloks.www.ig.about_this_account"
        )
        form = parse_qs(request.content.decode())
        assert form["client_doc_id"] == ["25336029838367092199423836565"]
        assert form["purpose"] == ["fetch"]
        assert form["method"] == ["post"]
        variables = json.loads(form["variables"][0])
        parameters = variables["params"]
        assert json.loads(parameters["params"]) == {
            "referer_type": "ProfileMore",
            "target_user_id": "123",
        }
        assert parameters["infra_params"] == {"device_id": client.session.device.id}
        assert parameters["bloks_versioning_id"] == client.session.profile.bloks_version
        assert parameters["app_id"] == "com.bloks.www.ig.about_this_account"
        context = variables["bk_context"]
        assert context["theme_params"][0]["value"] == ["three_neutral_gray"]
        assert context["android_os_api_level"] == 33
        assert context["gpu_memory_mb"] == 32768
        assert context["debug_tooling_metadata_token"] is None
        assert context["is_flipper_enabled"] is False
    finally:
        await client.aclose()


async def test_about_options_customize_rendering_without_changing_target_identity():
    captured = []

    def server(request):
        captured.append(json.loads(parse_qs(request.content.decode())["variables"][0]))
        return httpx.Response(200, json=response())

    client = transport(server)
    try:
        settings = AboutOptions(
            referer_type="SyntheticEntry",
            theme="synthetic_theme",
            design_system="synthetic_system",
            android_os_api_level=34,
            android_device_performance_class=1,
            gpu_memory_mb=8192,
        )
        await Profiles(client).about("123", settings)
        params = json.loads(captured[0]["params"]["params"])
        assert params == {"referer_type": "SyntheticEntry", "target_user_id": "123"}
        assert captured[0]["bk_context"]["theme_params"][0] == {
            "user_selected_theme": None,
            "value": ["synthetic_theme"],
            "design_system_name": "synthetic_system",
        }
        assert captured[0]["bk_context"]["android_os_api_level"] == 34
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {}},
        {"data": {"bloks_app": None}},
        {"data": {"bloks_app": {"screen_content": {}}}},
        response(tree=None),
        response(tree={}),
        response(tree=""),
    ],
)
async def test_about_rejects_missing_or_invalid_screen_bundle(payload):
    client = transport(lambda _: httpx.Response(200, json=payload))
    try:
        with pytest.raises(ProtocolError):
            await Profiles(client).about("123")
    finally:
        await client.aclose()


async def test_about_requires_authentication_before_dispatch():
    client = transport(lambda _: pytest.fail("Unexpected request"), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await Profiles(client).about("123")
    finally:
        await client.aclose()


async def test_about_rejects_invalid_options_before_dispatch():
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(ValueError, match="AboutOptions"):
            await Profiles(client).about("123", object())
    finally:
        await client.aclose()


async def test_about_http200_challenge_stops_further_reads():
    client = transport(
        lambda _: httpx.Response(
            200, json={"errors": [{"extensions": {"error_type": "challenge_required"}}]}
        )
    )
    try:
        with pytest.raises(ChallengeRequired):
            await Profiles(client).about("123")
        with pytest.raises(AccountPaused):
            await Profiles(client).by_id("123")
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "changes",
    [
        {"referer_type": ""},
        {"theme": "bad\nvalue"},
        {"design_system": 123},
        {"android_os_api_level": True},
        {"android_os_api_level": 0},
        {"gpu_memory_mb": -1},
        {"android_device_performance_class": -1},
    ],
)
def test_about_options_reject_invalid_context(changes):
    with pytest.raises(ValueError):
        replace(AboutOptions(), **changes)


@pytest.mark.parametrize("name", ["", "bad\r\nInjected: value", "bad/name", "é", "a" * 257])
def test_graphql_friendly_names_remain_bounded_ascii_header_values(name):
    with pytest.raises(ValueError):
        GraphQLRequest(name, {}, client_doc_id="123")


def test_graphql_purpose_is_explicit_and_validated():
    assert "purpose" not in GraphQLRequest("SyntheticQuery", {}, client_doc_id="123").form()
    with pytest.raises(ValueError):
        GraphQLRequest("SyntheticQuery", {}, client_doc_id="123", purpose="unobserved")
