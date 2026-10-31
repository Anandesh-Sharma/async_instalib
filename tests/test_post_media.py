"""Recorded media management requests; synthetic IDs and responses only."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, EditOptions, Media, Place, Session, UserTag
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import LoginRequired, ProtocolError
from async_instalib.resources.media_actions import MediaActions
from async_instalib.transport import Transport


class Recorder:
    """Answer every request with ``response`` and keep what was sent."""

    def __init__(self, response=None):
        self.response = {"status": "ok"} if response is None else response
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=self.response)

    @property
    def last(self):
        request = self.requests[-1]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if "signed_body" in form:
            form = json.loads(form["signed_body"].split(".", 1)[1])
        return request.url.path, request.url.query.decode(), form


def make_actions(server, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="11") if authenticated else Session()
    )
    options = ClientOptions(policy=RequestPolicy(min_interval=0))
    transport = Transport(session, options, httpx.MockTransport(server))
    return MediaActions(transport), transport


async def test_edit_replaces_caption_and_sets_recorded_details():
    server = Recorder({"status": "ok", "media": {"id": "5_11", "caption": {"text": "new"}}})
    actions, transport = make_actions(server)
    try:
        place = Place("2478723138984164", "Mahalaxmi", "K K Road", 18.98, 72.82)
        options = EditOptions(
            caption="new", alt_text="alt v2", location=place, user_tags=(UserTag("11", 0.5, 0.6),)
        )
        media = await actions.edit("5", options)
        path, _, fields = server.last
        assert path == "/api/v1/media/5_11/edit_media/"
        assert fields["caption_text"] == "new"
        assert fields["custom_accessibility_caption"] == "alt v2"
        assert json.loads(fields["location"])["meta_place_id"] == "2478723138984164"
        assert json.loads(fields["usertags"]) == {
            "in": [{"user_id": 11, "position": [0.5, 0.6]}],
            "added": ["11"],
        }
        assert fields["container_module"] == "edit_media_info"
        assert media.caption == "new"
        await actions.edit("5", EditOptions(caption=""))
        assert {"location", "usertags", "custom_accessibility_caption"}.isdisjoint(server.last[2])
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "media,query,extra",
    [
        (Media("5_11", 1, product_type="feed"), "media_type=PHOTO", "igtv_feed_preview"),
        (Media("5_11", 8, product_type="carousel_container"), "media_type=CAROUSEL", None),
        (Media("5_11", 8), "media_type=CAROUSEL", "igtv_feed_preview"),
        (Media("5_11", 2, product_type="clips"), "", None),
        (Media("5_11", 1, product_type="story"), "media_type=PHOTO", "deep_delete_waterfall"),
        (Media("5_11", 2, product_type="story"), "media_type=VIDEO", "deep_delete_waterfall"),
    ],
)
async def test_delete_uses_the_recorded_form_for_each_kind(media, query, extra):
    server = Recorder({"status": "ok", "did_delete": True})
    actions, transport = make_actions(server)
    try:
        await actions.delete(media)
        path, sent_query, fields = server.last
        assert (path, sent_query) == ("/api/v1/media/5_11/delete/", query)
        assert fields["media_id"] == "5_11" and fields["_uid"] == "11"
        if extra is not None:
            assert extra in fields
        if media.product_type == "clips":
            assert set(fields) == {"media_id", "_uid", "_uuid"}
    finally:
        await transport.aclose()


async def test_delete_refuses_unknown_kinds_and_unconfirmed_results():
    server = Recorder({"status": "ok", "did_delete": False})
    actions, transport = make_actions(server)
    try:
        with pytest.raises(ValueError, match="type"):
            await actions.delete(Media("5_11", 1))
        assert server.requests == []
        with pytest.raises(ProtocolError):
            await actions.delete(Media("5_11", 1, product_type="feed"))
    finally:
        await transport.aclose()


async def test_archive_round_trip_and_archive_page():
    server = Recorder()
    actions, transport = make_actions(server)
    try:
        await actions.archive("5_11")
        assert server.last[0] == "/api/v1/media/5_11/only_me/"
        assert server.last[2] == {
            "media_id": "5_11",
            "_uid": "11",
            "_uuid": transport.session.device.id,
        }
        await actions.unarchive("5")
        assert server.last[0] == "/api/v1/media/5_11/undo_only_me/"
        server.response = {"status": "ok", "items": [{"id": "5_11"}], "more_available": False}
        page = await actions.archived()
        assert [item.id for item in page.items] == ["5_11"] and not page.has_more
        server.response = {"status": "ok", "items": [], "more_available": True, "max_id": "x"}
        with pytest.raises(ProtocolError, match="continuation"):
            await actions.archived()
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "method,path,name",
    [
        ("pin", "/api/v1/users/pin_timeline_media/", "post_id"),
        ("unpin", "/api/v1/users/unpin_timeline_media/", "post_id"),
    ],
)
async def test_pins_name_the_bare_media_number(method, path, name):
    server = Recorder()
    actions, transport = make_actions(server)
    try:
        await getattr(actions, method)("5_11")
        assert server.last[0] == path
        assert server.last[2] == {name: "5", "_uuid": transport.session.device.id}
    finally:
        await transport.aclose()


async def test_grid_changes_check_the_returned_grid_state():
    server = Recorder({"status": "ok", "media": {"is_in_profile_grid": False}})
    actions, transport = make_actions(server)
    try:
        await actions.remove_from_grid("5_11")
        assert server.last[0] == "/api/v1/feed/profile_grid/remove/"
        assert server.last[2]["media_id"] == "5"
        with pytest.raises(ProtocolError, match="grid"):
            await actions.add_to_grid("5_11")
        assert server.last[0] == "/api/v1/feed/profile_grid/add/"
    finally:
        await transport.aclose()


async def test_comment_and_count_switches():
    server = Recorder()
    actions, transport = make_actions(server)
    try:
        await actions.disable_comments("5")
        assert server.last == (
            "/api/v1/media/5_11/disable_comments/",
            "",
            {"_uuid": transport.session.device.id},
        )
        await actions.enable_comments("5")
        assert server.last[0] == "/api/v1/media/5_11/enable_comments/"
        await actions.set_counts("5", hide_likes=False, hide_shares=True)
        assert server.last[0] == "/api/v1/media/update_like_and_view_counts_visibility/"
        assert server.last[2] == {
            "like_and_view_counts_disabled": "false",
            "share_count_disabled": "true",
            "media_id": "5_11",
            "_uuid": transport.session.device.id,
        }
        with pytest.raises(ValueError):
            await actions.set_counts("5", 1, 0)
    finally:
        await transport.aclose()


async def test_actions_only_touch_the_signed_in_accounts_media():
    server = Recorder()
    actions, transport = make_actions(server)
    try:
        for value in ("5_12", "abc", "5_11_1", ""):
            with pytest.raises(ValueError):
                await actions.archive(value)
        assert server.requests == []
    finally:
        await transport.aclose()
    actions, transport = make_actions(server, authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await actions.pin("5")
    finally:
        await transport.aclose()
