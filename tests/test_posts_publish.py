"""Recorded publish requests for photos, carousels, reels, and stories; synthetic data only."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import (
    CarouselItem,
    ClientOptions,
    LoginRequired,
    Photo,
    Place,
    PostOptions,
    ReelOptions,
    Session,
    StoryOptions,
    UserTag,
    Video,
)
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import APIError, ProtocolError
from async_instalib.resources.posts import Posts
from async_instalib.transport import Transport

PLACE = Place("760278631855210", "Mumbai, India.", "", 18.975, 72.8258)


class FakeInstagram:
    """Accept uploads and answer configure requests like the recorded responses."""

    def __init__(self, status="COMPLETED", error_type="NO_ERROR"):
        self.status = status
        self.error_type = error_type
        self.uploads = []
        self.configure = None

    def __call__(self, request):
        path = request.url.path
        if path.startswith(("/rupload_igphoto/", "/rupload_igvideo/")):
            params = json.loads(request.headers["X-Instagram-Rupload-Params"])
            if request.method == "GET":
                return httpx.Response(200, json={"offset": 0})
            self.uploads.append((path.split("/")[1], params))
            if path.startswith("/rupload_igvideo/"):
                return httpx.Response(200, json={"media_id": 1, "status": "ok"})
            return httpx.Response(200, json={"upload_id": params["upload_id"], "status": "ok"})
        form = parse_qs(request.content.decode())
        if "signed_body" in form:
            self.configure = (path, request.url.query.decode(), signed(form))
            upload_id = self.configure[2]["upload_id"]
        else:
            self.configure = (path, "", {k: v[0] for k, v in form.items()})
            upload_id = form["post_client_id"][0]
        return httpx.Response(200, json=publish_response(upload_id, self.status, self.error_type))


def signed(form):
    assert set(form) == {"signed_body"}
    return json.loads(form["signed_body"][0].split(".", 1)[1])


def publish_response(upload_id, status, error_type):
    return {
        "posts": [
            {
                "id": 77,
                "post_client_id": upload_id,
                "status": status,
                "media_ids": [77],
                "error_info": {"error_type": error_type, "message": ""},
            }
        ],
        "medias": [
            {
                "id": "77",
                "client_id": upload_id,
                "media_dict": {"id": "77_11", "pk": 77, "media_type": 1, "product_type": "feed"},
            }
        ],
        "status": "ok",
    }


def make_posts(server, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="11") if authenticated else Session()
    )
    options = ClientOptions(policy=RequestPolicy(min_interval=0))
    transport = Transport(session, options, httpx.MockTransport(server))
    return Posts(transport), transport


async def test_photo_post_sends_every_recorded_option(jpeg):
    server = FakeInstagram()
    posts, transport = make_posts(server)
    try:
        options = PostOptions(
            caption="lab photo #tag",
            location=PLACE,
            user_tags=(UserTag("11", 0.25, 0.75),),
            alt_text="a lake",
            comments_disabled=True,
            hide_like_count=True,
            hide_share_count=True,
        )
        result = await posts.publish_photo(Photo.from_bytes(jpeg), options)
        path, _, fields = server.configure
        assert path == "/api/v1/media/configure/"
        assert fields["upload_id"] == server.uploads[0][1]["upload_id"]
        assert fields["caption"] == "lab photo #tag"
        assert json.loads(fields["location"]) == {
            "name": "Mumbai, India.",
            "address": "",
            "lat": 18.975,
            "lng": 72.8258,
            "external_source": "meta_place",
            "meta_place_id": "760278631855210",
        }
        assert json.loads(fields["usertags"]) == {"in": [{"user_id": 11, "position": [0.25, 0.75]}]}
        assert fields["custom_accessibility_caption"] == "a lake"
        assert fields["disable_comments"] == fields["like_and_view_counts_disabled"] == "1"
        assert fields["share_count_disabled"] == "1"
        assert (fields["_uid"], fields["_uuid"]) == ("11", transport.session.device.id)
        assert fields["device"] == {
            "manufacturer": "samsung",
            "model": "SM-S931B",
            "android_version": 36,
            "android_release": "16",
        }
        assert (fields["original_width"], fields["original_height"]) == ("1080", "1350")
        assert result.completed and result.id == "77" and result.media.id == "77_11"
        assert result.upload_id == fields["upload_id"]
    finally:
        await transport.aclose()


async def test_feed_switches_are_omitted_when_off(jpeg):
    server = FakeInstagram()
    posts, transport = make_posts(server)
    try:
        await posts.publish_photo(Photo.from_bytes(jpeg))
        fields = server.configure[2]
        for name in ("disable_comments", "like_and_view_counts_disabled", "share_count_disabled"):
            assert name not in fields
        assert "location" not in fields and "usertags" not in fields
    finally:
        await transport.aclose()


async def test_carousel_children_keep_order_and_per_item_details(jpeg, mp4):
    server = FakeInstagram(status="PENDING")
    posts, transport = make_posts(server)
    try:
        video = Video.from_bytes(mp4, cover=Photo.from_bytes(jpeg))
        photo = CarouselItem(Photo.from_bytes(jpeg), (UserTag("11"),), "photo alt")
        result = await posts.publish_carousel(
            [video, photo], PostOptions(caption="album", location=PLACE, hide_like_count=True)
        )
        path, _, fields = server.configure
        assert path == "/api/v1/media/configure_sidecar/"
        assert fields["client_sidecar_id"] == fields["upload_id"]
        assert all(params["session_id"] == fields["upload_id"] for _, params in server.uploads)
        assert [route for route, _ in server.uploads] == [
            "rupload_igvideo",
            "rupload_igphoto",
            "rupload_igphoto",
        ]
        first, second = fields["children_metadata"]
        assert first["upload_id"] == server.uploads[0][1]["upload_id"]
        assert json.loads(first["clips"]) == [{"length": 5.782, "source_type": "4"}]
        assert (first["length"], first["audio_muted"]) == ("5.782", "false")
        assert "usertags" not in first
        assert json.loads(second["usertags"])["in"][0]["user_id"] == 11
        assert second["custom_accessibility_caption"] == "photo alt"
        assert fields["like_and_view_counts_disabled"] == "1"
        assert "location" in fields and "usertags" not in fields
        assert not result.completed
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "items,options,message",
    [
        (["photo"], None, "2 to 10"),
        (["photo", "uncovered"], None, "cover"),
        (["photo", "photo"], PostOptions(alt_text="x"), "per photo"),
    ],
)
async def test_invalid_carousels_fail_before_any_request(jpeg, mp4, items, options, message):
    media = {"photo": Photo.from_bytes(jpeg), "uncovered": Video.from_bytes(mp4)}

    def server(request):
        raise AssertionError("No request should be sent.")

    posts, transport = make_posts(server)
    try:
        with pytest.raises(ValueError, match=message):
            await posts.publish_carousel([media[name] for name in items], options)
    finally:
        await transport.aclose()


async def test_reel_sends_switches_and_unpositioned_tags(mp4):
    server = FakeInstagram()
    posts, transport = make_posts(server)
    try:
        options = ReelOptions(
            caption="reel",
            location=PLACE,
            tagged_user_ids=("11",),
            hide_share_count=True,
            share_to_feed=False,
        )
        await posts.publish_reel(Video.from_bytes(mp4), options)
        path, query, fields = server.configure
        assert (path, query) == ("/api/v1/media/configure_to_clips/", "video=1")
        assert fields["clips_share_preview_to_feed"] == "0"
        assert fields["disable_comments"] == fields["like_and_view_counts_disabled"] == "0"
        assert fields["share_count_disabled"] == "1"
        assert json.loads(fields["usertags"]) == {"in": [{"user_id": 11, "position": [0.0, 0.0]}]}
        assert fields["length"] == 5.782 and fields["capture_type"] == "clips_v2"
        assert server.uploads[0][1]["is_clips_video"] == "1"
    finally:
        await transport.aclose()


async def test_stories_select_route_audience_and_caption(jpeg, mp4):
    server = FakeInstagram()
    posts, transport = make_posts(server)
    try:
        await posts.publish_story(Photo.from_bytes(jpeg), StoryOptions(caption="hello"))
        path, query, fields = server.configure
        assert (path, query) == ("/api/v1/media/configure_to_story/", "")
        assert fields["audience"] == "default"
        assert fields["comment_as_caption"] == "hello"
        assert fields["configure_mode"] == "1"
        await posts.publish_story(Video.from_bytes(mp4), StoryOptions(close_friends=True))
        path, query, fields = server.configure
        assert (path, query) == ("/api/v1/media/configure_to_story/", "video=1")
        assert fields["audience"] == "besties"
        assert "comment_as_caption" not in fields
        assert server.uploads[-1][1]["for_album"] == "1"
    finally:
        await transport.aclose()


async def test_status_reads_by_upload_id():
    server = FakeInstagram(status="PENDING")
    posts, transport = make_posts(server)
    try:
        result = await posts.status("1234")
        path, _, form = server.configure
        assert path == "/api/v1/media/get_upload_status_REST/"
        assert form == {"_uuid": transport.session.device.id, "post_client_id": "1234"}
        assert result.status == "PENDING" and not result.completed
        with pytest.raises(ValueError):
            await posts.status("12a")
    finally:
        await transport.aclose()


async def test_publishing_errors_are_typed(jpeg):
    for server, error in (
        (FakeInstagram(error_type="UPLOAD_FAILED"), APIError),
        (lambda request: httpx.Response(200, json={"status": "ok"}), ProtocolError),
    ):
        posts, transport = make_posts(server)
        try:
            with pytest.raises(error):
                await posts.status("1")
        finally:
            await transport.aclose()

    posts, transport = make_posts(FakeInstagram(), authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await posts.publish_photo(Photo.from_bytes(jpeg))
    finally:
        await transport.aclose()


def test_options_reject_values_that_were_not_recorded():
    with pytest.raises(ValueError):
        UserTag("11", 1.5, 0)
    with pytest.raises(ValueError):
        PostOptions(user_tags=(UserTag("11"), UserTag("11")))
    with pytest.raises(ValueError):
        ReelOptions(tagged_user_ids=("abc",))
    with pytest.raises(ValueError, match="photos only"):
        CarouselItem(Video(b"x", 1, 1, 1), alt_text="x")
    with pytest.raises(ValueError):
        PostOptions(location="Mumbai")
