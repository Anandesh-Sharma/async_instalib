import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.config.metadata import HighlightItemsOptions
from async_instalib.exceptions import ChallengeRequired, ProtocolError
from async_instalib.models.stories import StoryAuthor
from async_instalib.resources.content import ProfileContent
from async_instalib.transport import Transport


def transport(server):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


def documents():
    return [
        {"status": "ok", "reels": {}, "client_hints": {"type": "preload"}},
        {
            "status": "ok",
            "reels": {
                "highlight:123": {
                    "id": "highlight:123",
                    "title": "Example",
                    "media_count": 1,
                    "user": {"id": "7", "username": "synthetic"},
                    "items": [
                        {
                            "id": "456_7",
                            "media_type": 2,
                            "user": {"id": "7", "pk": 7, "is_private": False},
                        }
                    ],
                }
            },
        },
    ]


def stream(records):
    return "\n".join(json.dumps(record) for record in records)


async def test_highlight_items_read_exactly_one_highlight_without_seen_or_neighbor_prefetch():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, content=stream(documents()))

    client = transport(server)
    try:
        result = await ProfileContent(client).highlight_items("highlight:123")
        assert result.id == "highlight:123"
        assert result.items[0].user.id == "7"
        assert result.items[0].user.username is None
        assert len(requests) == 1
        request = requests[0]
        assert request.url.path == "/api/v1/feed/reels_media_stream/"
        body = json.loads(
            parse_qs(request.content.decode())["signed_body"][0].removeprefix("SIGNATURE.")
        )
        assert body["reel_ids"] == ["highlight:123"]
        assert body["exclude_media_ids"] == "[]"
        assert body["batch_size"] == "1"
        assert body["_uuid"] == client.session.device.id
        assert body["_uid"] == "42"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "records",
    [
        [{"status": "ok", "reels": {}}],
        [{"status": "ok", "reels": []}],
        [{"status": "ok", "reels": {"highlight:999": {"id": "highlight:999", "items": []}}}],
        documents() + [documents()[1]],
        [{"status": "ok", "reels": {"highlight:123": {"id": "highlight:999", "items": []}}}],
    ],
)
async def test_highlight_stream_fails_on_missing_malformed_or_wrong_highlight(records):
    client = transport(lambda request: httpx.Response(200, content=stream(records)))
    try:
        with pytest.raises(ProtocolError):
            await ProfileContent(client).highlight_items("highlight:123")
    finally:
        await client.aclose()


async def test_trailing_highlight_error_never_returns_partial_success():
    client = transport(
        lambda request: httpx.Response(
            200, content=stream(documents() + [{"status": "fail", "message": "challenge_required"}])
        )
    )
    try:
        with pytest.raises(ChallengeRequired):
            await ProfileContent(client).highlight_items("highlight:123")
        assert client.session.paused_reason == "challenge_required"
    finally:
        await client.aclose()


def test_partial_story_author_keeps_identity_and_rejects_conflicts():
    author = StoryAuthor.from_dict({"id": "7", "pk": 7, "is_private": False})
    assert author.username is None
    assert author.is_private is False
    with pytest.raises(ValueError):
        StoryAuthor.from_dict({"id": "7", "pk": 8})
    with pytest.raises(ValueError):
        HighlightItemsOptions(batch_size=True)
