import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.config.content import RepostsOptions
from async_instalib.exceptions import ProtocolError
from async_instalib.resources.content import ProfileContent
from async_instalib.transport import Transport


def transport(server):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


def envelope(user_id="7"):
    return {
        "data": {
            "fetch__XDTUserDict": {
                "id": user_id,
                "user_reposts_timeline": {
                    "repost_grid_items": [{"media": {"id": "123_8", "media_type": 2}}],
                    "repost_more_available": True,
                    "repost_next_max_id": "opaque-reposts",
                },
            }
        }
    }


async def test_reposts_follow_recorded_cursor_without_secondary_media_fetch():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, json=envelope())

    client = transport(server)
    try:
        content = ProfileContent(client)
        first = await content.reposts("7")
        assert first.items[0].id == "123_8"
        assert first.has_more and len(requests) == 1
        with pytest.raises(ValueError):
            await content.reposts(
                "7", RepostsOptions(cursor=first.continuation, prepend_author_ids=())
            )
        await content.reposts("7", RepostsOptions(cursor=first.continuation))
        assert len(requests) == 2
        first_vars = json.loads(parse_qs(requests[0].content.decode())["variables"][0])
        second_vars = json.loads(parse_qs(requests[1].content.decode())["variables"][0])
        assert first_vars == {"id": "7", "prepend_media_repost_author_ids": ["7"]}
        assert second_vars["max_id"] == "opaque-reposts"
    finally:
        await client.aclose()


async def test_reposts_reject_wrong_target_identity():
    client = transport(lambda request: httpx.Response(200, json=envelope("8")))
    try:
        with pytest.raises(ProtocolError):
            await ProfileContent(client).reposts("7")
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prepend_author_ids": [True]},
        {"prepend_author_ids": ["../8"]},
        {"variables": {"id": "8"}},
        {"variables": {"flag": float("nan")}},
    ],
)
def test_reposts_options_validate_identity_and_serialization(kwargs):
    with pytest.raises(ValueError):
        RepostsOptions(**kwargs)
