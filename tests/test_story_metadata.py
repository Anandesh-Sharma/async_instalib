import json

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.exceptions import ProtocolError
from async_instalib.models.stories import HighlightTray, StoryCollection
from async_instalib.resources.content import ProfileContent
from async_instalib.transport import Transport


def transport(server):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


def story_payload():
    return {
        "status": "ok",
        "reel": {
            "id": "7",
            "user": {"id": "7", "username": "synthetic"},
            "media_count": 1,
            "seen": 0,
            "items": [
                {
                    "id": "123_7",
                    "media_type": 2,
                    "expiring_at": 999,
                    "can_reply": False,
                    "user": {"id": "7", "username": "synthetic"},
                }
            ],
        },
    }


def highlight_payload():
    return {
        "status": "ok",
        "tray": [
            {
                "id": "highlight:123",
                "title": "Example",
                "media_count": 3,
                "user": {"id": "7", "username": "synthetic"},
                "cover_media": {
                    "cropped_image_version": {
                        "url": "https://example.test/cover.jpg",
                        "width": 100,
                        "height": 100,
                    }
                },
            }
        ],
        "cursor": None,
        "has_fetched_all_remaining_highlights": None,
    }


def test_story_metadata_keeps_unknown_fields_and_does_not_confuse_null_with_missing():
    payload = story_payload()
    collection = StoryCollection.from_dict(payload)
    assert collection.reel.items[0].expiring_at == 999
    assert collection.reel.items[0].can_reply is False
    assert collection.reel.items[0].can_reshare is None
    payload["reel"]["items"][0]["expiring_at"] = 1
    assert collection.reel.items[0].raw["expiring_at"] == 999
    assert StoryCollection.from_dict({"reel": None}).reel is None
    with pytest.raises(ValueError):
        StoryCollection.from_dict({})


def test_highlight_cover_and_unknown_completeness_are_preserved():
    payload = highlight_payload()
    tray = HighlightTray.from_dict(payload)
    assert tray.has_fetched_all is None
    assert tray.items[0].id == "highlight:123"
    assert tray.items[0].cover.cropped_image.width == 100
    assert tray.items[0].cover.full_image is None
    payload["tray"][0]["title"] = "changed"
    assert tray.items[0].title == "Example"


async def test_typed_story_and_highlights_only_make_metadata_reads():
    requests = []

    def server(request):
        requests.append(request)
        if "highlights_tray" in request.url.path:
            return httpx.Response(
                200,
                content=json.dumps({"tray": [], "status": "ok"})
                + "\n"
                + json.dumps(highlight_payload()),
            )
        return httpx.Response(200, json=story_payload())

    client = transport(server)
    try:
        content = ProfileContent(client)
        collection = await content.story_collection("7")
        tray = await content.highlight_tray("7")
        assert collection.reel.id == "7"
        assert len(tray.items) == 1
        assert len(requests) == 2
        assert all(request.method == "GET" for request in requests)
        assert all(request.url.host == "i.instagram.com" for request in requests)
        assert isinstance(await content.stories("7"), dict)
        assert isinstance(await content.highlights("7"), dict)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("story_collection", {"reel": {"id": "8", "items": []}}),
        ("story_collection", {"reel": {"id": "7", "items": "bad"}}),
        (
            "highlight_tray",
            {"tray": [{"id": "highlight:123", "user": {"id": "8", "username": "wrong"}}]},
        ),
        ("highlight_tray", {"tray": [{"id": "../123"}]}),
        ("highlight_tray", {"tray": [{"id": "highlight:123", "media_count": True}]}),
    ],
)
async def test_typed_metadata_rejects_wrong_identity_and_malformed_fields(operation, payload):
    client = transport(lambda request: httpx.Response(200, json={"status": "ok", **payload}))
    try:
        with pytest.raises(ProtocolError):
            await getattr(ProfileContent(client), operation)("7")
    finally:
        await client.aclose()
