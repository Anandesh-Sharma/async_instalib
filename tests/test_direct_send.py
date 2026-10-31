"""Recorded direct sends and message actions; synthetic media and IDs only."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
from conftest import make_mp4

from async_instalib import Audio, ClientOptions, Media, Message, Photo, Session, Video
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import ProtocolError
from async_instalib.resources.direct import Direct
from async_instalib.transport import Transport

THEIRS = Message("9002", "340", user_id="42", type="text", client_context="7522060602617868500")


def ack(kind_request):
    return {
        "action": "item_ack",
        "payload": {"item_id": "9100", "client_context": "1", "thread_id": "340"},
        "status": "ok",
    }


class Server:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/messenger_"):
            if request.method == "GET":
                return httpx.Response(200, json={"offset": 0})
            return httpx.Response(200, json={"media_id": 555})
        if path.startswith("/graphql_www"):
            name = parse_qs(request.content.decode())["fb_api_req_friendly_name"][0]
            data = {
                "IGDirectClipShareMutationWithResponse": {
                    "xig_direct_clip_share_with_response": {"ig_item_id": "9200"}
                },
                "IGDirectBroadcastProfile": {"ig_direct_broadcast_profile": True},
                "IGDirectEditMessageMutation": {"direct_edit_message": True},
                "IGDirectItemSeenMutation": {"xig_direct_item_seen": True},
            }[name]
            return httpx.Response(200, json={"data": data})
        if "/broadcast/" in path:
            return httpx.Response(200, json=ack(request))
        return httpx.Response(200, json={"status": "ok"})

    def form(self, index=-1):
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode()).items()}


def make_direct(server):
    transport = Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )
    return Direct(transport), transport


async def test_text_and_reply_use_the_recorded_broadcast_form():
    server = Server()
    direct, transport = make_direct(server)
    try:
        sent = await direct.send.text("340", "hello", reply_to=THEIRS)
        request = server.requests[0]
        form = server.form()
        assert request.url.path == "/api/v1/direct_v2/threads/broadcast/text/"
        assert form["thread_ids"] == "[340]" and form["text"] == "hello"
        assert form["client_context"] == form["mutation_token"] == form["offline_threading_id"]
        assert form["replied_to_item_id"] == "9002"
        assert form["replied_to_client_context"] == "7522060602617868500"
        assert form["replied_to_user_id"] == "42" and form["replied_to_target_type"] == "text"
        assert (sent.id, sent.thread_id, sent.is_sent_by_viewer) == ("9100", "340", True)
        with pytest.raises(ValueError, match="text messages"):
            await direct.send.text(
                "340", "x", reply_to=Message("1", "340", type="like", client_context="2")
            )
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "call,kind,expected",
    [
        (lambda s: s.like("340"), "like", {}),
        (lambda s: s.gif("340", "PBLYoj3d"), "animated_media", {"id": "PBLYoj3d"}),
        (
            lambda s: s.link("340", "see https://x.test/a", ["https://x.test/a"]),
            "link",
            {"link_urls": '["https://x.test/a"]', "link_text": "see https://x.test/a"},
        ),
        (
            lambda s: s.forward("340", THEIRS),
            "forward",
            {"forwarded_from_thread_id": "340", "forwarded_from_thread_item_id": "9002"},
        ),
    ],
)
async def test_simple_kinds(call, kind, expected):
    server = Server()
    direct, transport = make_direct(server)
    try:
        await call(direct.send)
        assert server.requests[0].url.path == f"/api/v1/direct_v2/threads/broadcast/{kind}/"
        form = server.form()
        assert {key: form[key] for key in expected} == expected
    finally:
        await transport.aclose()


async def test_media_uploads_go_to_the_messenger_routes_with_attachment_headers(jpeg):
    server = Server()
    direct, transport = make_direct(server)
    try:
        await direct.send.photo("340", Photo.from_bytes(jpeg))
        upload, send = server.requests
        assert upload.url.path.startswith("/messenger_image/")
        assert upload.headers["image_type"] == "FILE_ATTACHMENT"
        assert json.loads(upload.headers["X-Instagram-Rupload-Params"]) == {
            "is_optimistic_upload": "true"
        }
        assert send.url.path.endswith("/broadcast/photo_attachment/")
        assert server.form()["attachment_fbid"] == "555"

        server.requests.clear()
        await direct.send.video("340", Video.from_bytes(make_mp4()))
        probe, body, send = server.requests
        assert probe.method == "GET" and probe.headers["video_type"] == "FILE_ATTACHMENT"
        assert "X-Instagram-Rupload-Params" not in body.headers
        assert body.headers["Segment-Type"] == "3"
        assert server.form()["video_result"] == "555"

        server.requests.clear()
        audio = Audio.from_bytes(make_mp4(duration_ms=1500))
        await direct.send.voice("340", audio)
        probe, body, send = server.requests
        assert (
            probe.headers["audio_type"] == "FILE_ATTACHMENT" and "Segment-Type" not in probe.headers
        )
        assert body.headers["X-Entity-Type"] == "audio/mp4"
        form = server.form()
        assert json.loads(form["waveform"]) == [0.0] * 15
        assert form["waveform_sampling_frequency_hz"] == "10"
        assert body.url.path.endswith(form["upload_id"] + body.url.path.split(form["upload_id"])[1])
    finally:
        await transport.aclose()


async def test_production_uploads_use_the_meta_upload_host(jpeg):
    hosts = []

    def server(request):
        hosts.append(request.url.host)
        if request.url.path.startswith("/messenger_"):
            return httpx.Response(200, json={"media_id": 1})
        return httpx.Response(200, json=ack(request))

    transport = Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(
            base_url="https://i.instagram.com/api/v1/", policy=RequestPolicy(min_interval=0)
        ),
        httpx.MockTransport(server),
    )
    try:
        await Direct(transport).send.photo("340", Photo.from_bytes(jpeg))
        assert hosts == ["rupload.facebook.com", "i.instagram.com"]
    finally:
        await transport.aclose()


async def test_shares_use_recorded_requests():
    server = Server()
    direct, transport = make_direct(server)
    try:
        reel = Media("7_42", 2, product_type="clips")
        assert await direct.send.media("340", reel, "look") == "9200"
        variables = json.loads(server.form()["variables"])
        assert variables["ig_media_igid"] == "7" and variables["ig_thread_igid"] == "340"
        assert variables["data"]["text"] == {"sensitive_string_value": "look"}
        photo = Media("8_42", 1, product_type="feed")
        assert await direct.send.media("340", photo) == "9100"
        assert server.requests[-1].url.query.decode() == "media_type=photo"
        assert server.form()["media_id"] == "8_42"
        await direct.send.profile("340", "173")
        assert json.loads(server.form()["variables"])["ig_profile_user_igid"] == "173"
        with pytest.raises(ValueError, match="recorded"):
            await direct.send.media("340", Media("9_42", 8))
    finally:
        await transport.aclose()


async def test_message_actions():
    server = Server()
    direct, transport = make_direct(server)
    try:
        await direct.messages.react(THEIRS, "😂")
        form = server.form()
        assert form["reaction_status"] == "created" and form["emoji"] == "😂"
        assert form["item_id"] == "9002" and form["super_react_type"] == "none"
        assert form["original_message_client_context"] == "7522060602617868500"
        await direct.messages.unreact(THEIRS)
        assert server.form()["reaction_status"] == "deleted"
        assert "super_react_type" not in server.form()
        await direct.messages.edit(THEIRS, "changed")
        variables = json.loads(server.form()["variables"])
        assert variables["target_item_id"] == "9002"
        assert variables["offline_threading_id"] == "7522060602617868500"
        assert variables["body"] == {"sensitive_string_value": "changed"}
        await direct.messages.unsend(THEIRS)
        assert server.requests[-1].url.path == "/api/v1/direct_v2/threads/340/items/9002/delete/"
        await direct.messages.mark_seen(THEIRS)
        assert json.loads(server.form()["variables"])["data"]["item_id"] == "9002"
        await direct.messages.pin(THEIRS)
        assert server.requests[-1].url.path == "/api/v1/direct_v2/threads/340/pin_message/"
        assert server.form()["pin_message_item_id"] == "9002"
        await direct.messages.unpin(THEIRS)
        assert server.form()["unpin_message_item_id"] == "9002"
        with pytest.raises(ValueError):
            await direct.messages.react(Message("1", "340"))
    finally:
        await transport.aclose()


async def test_unacknowledged_sends_are_protocol_errors():
    def server(request):
        return httpx.Response(200, json={"status": "ok"})

    direct, transport = make_direct(server)
    try:
        with pytest.raises(ProtocolError, match="acknowledge"):
            await direct.send.like("340")
    finally:
        await transport.aclose()


def test_audio_reads_duration_and_validates_waveform():
    audio = Audio.from_bytes(make_mp4(duration_ms=2050))
    assert audio.duration_ms == 2050 and len(audio.waveform) == 21
    with pytest.raises(ValueError):
        Audio(b"x", 1000, (2.0,))
