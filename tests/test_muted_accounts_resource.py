"""Typed muted reads preserve request bounds and never execute embedded UI actions."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, ProtocolError, RequestPolicy, Session
from async_instalib.config.muted import MutedDecodeOptions
from async_instalib.config.relationship_lists import MutedListOptions
from async_instalib.resources.relationship_lists import RelationshipLists
from async_instalib.transport import Transport


def transport(handler):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


def empty_layout():
    return {
        "layout": {
            "bloks_payload": {
                "tree": {
                    "㐈": {" ": [{"㚞": {"#": {"㐈": {" ": [{"㐈": {" ": [{"㐗": {}}]}}]}}}}]}
                },
                "error_attribution": {
                    "logging_id": json.dumps(
                        {"callsite": json.dumps({"feature": "MutedUsersPage"})}
                    )
                },
            }
        },
        "status": "ok",
    }


@pytest.mark.parametrize("encoded", [False, True])
async def test_typed_muted_empty_screen_uses_one_read_and_retains_raw(encoded):
    calls = []

    def server(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/bloks/apps/com.instagram.growth.screens.muted_users/"
        form = parse_qs(request.content.decode())
        context = json.loads(form["bk_client_context"][0])
        assert context["theme_params"][0]["value"] == ["synthetic_theme"]
        result = empty_layout()
        if encoded:
            result = {"payload": json.dumps({"layout": result["layout"]}), "status": "ok"}
        return httpx.Response(200, json=result)

    client = transport(server)
    try:
        page = await RelationshipLists(client).muted_accounts(
            MutedListOptions(theme="synthetic_theme")
        )
        assert page.items == ()
        assert not page.has_more
        assert page.total_count is None
        assert page.raw["decoded_payload"]["layout"]["bloks_payload"]["tree"]
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_typed_muted_options_fail_before_io():
    client = transport(lambda _: pytest.fail("Unexpected request"))
    try:
        with pytest.raises(ValueError, match="MutedDecodeOptions"):
            await RelationshipLists(client).muted_accounts(decode_options={})
        with pytest.raises(ValueError, match="MutedListOptions"):
            await RelationshipLists(client).muted_accounts(options={})
    finally:
        await client.aclose()


@pytest.mark.parametrize("bounds", [MutedDecodeOptions(max_nodes=1), None])
async def test_unsupported_or_oversized_muted_screen_does_not_trigger_a_followup(bounds):
    calls = []

    def server(request):
        calls.append(request)
        result = empty_layout()
        if bounds is None:
            result["layout"]["bloks_payload"]["tree"] = {"unrecognized": "layout"}
        return httpx.Response(200, json=result)

    client = transport(server)
    try:
        with pytest.raises(ProtocolError):
            await RelationshipLists(client).muted_accounts(decode_options=bounds)
        assert len(calls) == 1
    finally:
        await client.aclose()
