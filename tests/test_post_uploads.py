"""Media files and rupload exchanges, using synthetic media and a mock server."""

import json

import httpx
import pytest
from conftest import make_jpeg, make_mp4

from async_instalib import ClientOptions, Photo, Session, Video
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import APIError, NetworkError, ProtocolError
from async_instalib.resources.media_uploads import Destination, MediaUploads
from async_instalib.transport import Transport


def make_transport(handler):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


def upload_params(request):
    return json.loads(request.headers["X-Instagram-Rupload-Params"])


def test_photo_reads_jpeg_dimensions_and_rejects_other_files():
    photo = Photo.from_bytes(make_jpeg(640, 800))
    assert (photo.width, photo.height) == (640, 800)
    assert "bytes=" in repr(photo) and "\\xff" not in repr(photo)
    with pytest.raises(ValueError, match="JPEG"):
        Photo.from_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="dimensions"):
        Photo.from_bytes(b"\xff\xd8\xff\xd9")


def test_video_reads_mp4_size_and_duration():
    video = Video.from_bytes(make_mp4(720, 1280, 4023))
    assert (video.width, video.height, video.duration_ms) == (720, 1280, 4023)


def test_video_reads_version_one_headers():
    video = Video.from_bytes(make_mp4(1080, 1080, 5000, version=1))
    assert (video.width, video.height, video.duration_ms) == (1080, 1080, 5000)


@pytest.mark.parametrize(
    "data,message",
    [
        (make_jpeg(), "MP4"),
        (make_mp4()[:16] + b"\x00\x00\x00\x08free", "metadata"),
        (make_mp4().replace(b"vide", b"soun"), "no video track"),
    ],
)
def test_video_rejects_files_without_usable_metadata(data, message):
    with pytest.raises(ValueError, match=message):
        Video.from_bytes(data)


async def test_photo_upload_sends_the_unchanged_jpeg_with_recorded_headers(jpeg):
    def server(request):
        assert request.method == "POST"
        assert request.url.path.startswith("/rupload_igphoto/")
        name = request.url.path.removeprefix("/rupload_igphoto/")
        params = upload_params(request)
        assert name.startswith(params["upload_id"] + "_0_")
        assert request.content == jpeg
        assert request.headers["X-Entity-Name"] == name
        assert request.headers["X-Entity-Length"] == str(len(jpeg))
        assert request.headers["X-Entity-Type"] == "image/jpeg"
        assert request.headers["Offset"] == "0"
        assert request.headers["Content-Type"] == "application/octet-stream"
        assert params["media_type"] == "1"
        assert params["share_type"] == "feed"
        assert params["session_id"] == params["upload_id"]
        assert json.loads(params["image_compression"])["original_width"] == 1080
        assert "is_sidecar" not in params
        return httpx.Response(200, json={"upload_id": params["upload_id"], "status": "ok"})

    transport = make_transport(server)
    try:
        upload_id = await MediaUploads(transport).photo(Photo.from_bytes(jpeg), Destination("feed"))
        assert upload_id.isdigit()
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "destination,expected,absent",
    [
        (Destination("reels"), {"is_clips_video": "1", "extract_cover_frame": "1"}, "for_album"),
        (Destination("stories"), {"for_album": "1", "extract_cover_frame": "1"}, "is_sidecar"),
    ],
)
async def test_video_upload_probes_offset_then_sends_body(mp4, destination, expected, absent):
    requests = []

    def server(request):
        requests.append(request)
        params = upload_params(request)
        assert request.url.path.startswith("/rupload_igvideo/")
        assert {k: params[k] for k in expected} == expected
        assert absent not in params
        assert params["upload_media_duration_ms"] == "5782"
        assert request.headers["Segment-Type"] == "3"
        if request.method == "GET":
            assert request.content == b""
            return httpx.Response(200, json={"offset": 0})
        assert request.content == mp4
        assert request.headers["X-Entity-Type"] == "video/mp4"
        return httpx.Response(200, json={"media_id": 9, "status": "ok"})

    transport = make_transport(server)
    try:
        await MediaUploads(transport).video(Video.from_bytes(mp4), destination)
        assert [request.method for request in requests] == ["GET", "POST"]
        assert requests[0].url.path == requests[1].url.path
    finally:
        await transport.aclose()


async def test_carousel_video_uploads_its_cover_under_the_same_upload_id(mp4, jpeg):
    requests = []

    def server(request):
        requests.append(request)
        params = upload_params(request)
        assert params["session_id"] == "500"
        assert params["is_sidecar"] == "1"
        if request.method == "GET":
            return httpx.Response(200, json={"offset": 0})
        if request.url.path.startswith("/rupload_igphoto/"):
            assert params["media_type"] == "2"
            assert params["cover_photo_type"] == "first_frame"
            return httpx.Response(200, json={"upload_id": params["upload_id"], "status": "ok"})
        assert "extract_cover_frame" not in params
        return httpx.Response(200, json={"media_id": 9, "status": "ok"})

    transport = make_transport(server)
    try:
        video = Video.from_bytes(mp4, cover=Photo.from_bytes(jpeg))
        upload_id = await MediaUploads(transport).video(video, Destination("feed", "500"))
        assert [upload_params(r)["upload_id"] for r in requests] == [upload_id] * 3
    finally:
        await transport.aclose()


async def test_existing_video_offset_stops_before_sending_the_body(mp4):
    methods = []

    def server(request):
        methods.append(request.method)
        return httpx.Response(200, json={"offset": 4096})

    transport = make_transport(server)
    try:
        with pytest.raises(ProtocolError, match="existing data"):
            await MediaUploads(transport).video(Video.from_bytes(mp4), Destination("reels"))
        assert methods == ["GET"]
    finally:
        await transport.aclose()


async def test_upload_failures_are_typed_and_never_resent(jpeg):
    attempts = []

    def rejected(request):
        attempts.append(request)
        return httpx.Response(400, json={"status": "fail", "message": "upload rejected"})

    def broken(request):
        attempts.append(request)
        raise httpx.WriteError("connection dropped")

    for handler, error in ((rejected, APIError), (broken, NetworkError)):
        attempts.clear()
        transport = make_transport(handler)
        try:
            with pytest.raises(error):
                await MediaUploads(transport).photo(Photo.from_bytes(jpeg), Destination("feed"))
            assert len(attempts) == 1
        finally:
            await transport.aclose()


def test_upload_ids_stay_unique_within_one_client():
    uploads = MediaUploads.__new__(MediaUploads)
    uploads._last_id = 0
    identifiers = [uploads.new_id() for _ in range(50)]
    assert len(set(identifiers)) == 50
