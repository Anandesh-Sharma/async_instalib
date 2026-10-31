"""Recorded account comment settings; synthetic responses only."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import ProtocolError
from async_instalib.resources.comment_settings import CommentSettings
from async_instalib.transport import Transport

APP = "com.bloks.www.com.instagram.settings.privacy.comments."


def make_settings(handler):
    transport = Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )
    return CommentSettings(transport), transport


@pytest.mark.parametrize(
    "method,value,handler,server_params",
    [
        (
            "allow_comments_from",
            "mutual_followers",
            "set_audience_control",
            {
                "audience_control": "mutual_followers",
                "updated_value": "Followers you follow back",
                "should_update_carbon_state": 0,
            },
        ),
        (
            "allow_story_comments_from",
            "off",
            "set_hype_audience_control_handler",
            {"audience_control": 7, "updated_value": "Off", "should_update_global_state": 0},
        ),
        ("allow_gif_comments", False, "set_gif_comment_settings_handler", None),
        ("allow_photo_comments", True, "set_photo_comment_settings_handler", None),
    ],
)
async def test_settings_send_the_recorded_bloks_handlers(method, value, handler, server_params):
    sent = []

    def server(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        sent.append((request, form))
        return httpx.Response(200, json={"data": {"bloks_action": {"action": {}}}})

    settings, transport = make_settings(server)
    try:
        await getattr(settings, method)(value)
        request, form = sent[0]
        assert request.url.path == "/graphql_www"
        assert form["fb_api_req_friendly_name"] == "IGBloksAppRootQuery-" + APP + handler
        assert form["client_doc_id"] == "356548512611906258864738750706"
        assert form["purpose"] == "fetch"
        assert request.headers["X-Root-Field-Name"] == "bloks_action"
        variables = json.loads(form["variables"])
        assert variables["params"]["app_id"] == APP + handler
        params = json.loads(json.loads(variables["params"]["params"])["params"])["server_params"]
        expected = (
            server_params
            or {
                "allow_gif_comments": {"are_gif_comments_disabled": 1},
                "allow_photo_comments": {"enable_photo_comments": 1},
            }[method]
        )
        assert params == expected
        assert variables["bk_context"]["android_os_api_level"] == 33
    finally:
        await transport.aclose()


async def test_blocked_commenters_use_the_recorded_form():
    forms = []

    def server(request):
        assert request.url.path.endswith(".set_blocked_commenters/")
        forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={"layout": {"bloks_payload": {}}, "status": "ok"})

    settings, transport = make_settings(server)
    try:
        await settings.block_commenter("42")
        await settings.unblock_commenter("42")
        assert forms[0]["block_status"] == "block" and forms[0]["user_id"] == "42"
        assert forms[0]["should_revalidate_blocked_commenters_screen"] == "0"
        assert forms[1]["block_status"] == "unblock"
        assert "should_revalidate_blocked_commenters_screen" not in forms[1]
        assert json.loads(forms[0]["bk_client_context"])["styles_id"] == "instagram"
    finally:
        await transport.aclose()


async def test_settings_reject_unrecorded_values_and_replies():
    settings, transport = make_settings(lambda request: httpx.Response(200, json={"data": {}}))
    try:
        with pytest.raises(ValueError):
            await settings.allow_comments_from("everyone")
        with pytest.raises(ValueError):
            await settings.allow_gif_comments("yes")
        with pytest.raises(ProtocolError):
            await settings.allow_gif_comments(True)
    finally:
        await transport.aclose()
