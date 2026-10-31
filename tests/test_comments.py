"""Recorded comment requests; synthetic IDs, text, and responses only."""

import json
from urllib.parse import parse_qs, parse_qsl

import httpx
import pytest

from async_instalib import ClientOptions, CommentListOptions, Photo, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import APIError, LoginRequired, ProtocolError
from async_instalib.resources.comments import Comments
from async_instalib.transport import Transport

COMMENT = {"pk": "900", "text": "hi", "media_id": 5, "user": {"pk": "11", "username": "me"}}


class Server:
    """Route by path and keep each request for assertions."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        for prefix, reply in self.routes.items():
            if request.url.path.startswith(prefix):
                return reply(request) if callable(reply) else httpx.Response(200, json=reply)
        raise AssertionError(f"Unexpected request {request.url.path}")


def signed(request):
    form = parse_qs(request.content.decode())
    assert set(form) == {"signed_body"}
    return json.loads(form["signed_body"][0].split(".", 1)[1])


def make_comments(server, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="11") if authenticated else Session()
    )
    transport = Transport(
        session, ClientOptions(policy=RequestPolicy(min_interval=0)), httpx.MockTransport(server)
    )
    return Comments(transport), transport


def stream(*documents):
    return httpx.Response(200, content="\n".join(json.dumps(d) for d in documents).encode())


async def test_list_merges_stream_documents_and_binds_the_cursor():
    page_one = [
        {"comments": [{**COMMENT, "pk": "1"}], "status": "ok"},
        {
            "comments": [{**COMMENT, "pk": "2"}],
            "has_more_headload_comments": True,
            "next_min_id": '{"cached_comments_cursor":"2"}',
            "comment_count": 40,
            "status": "ok",
        },
    ]
    server = Server({"/api/v1/media/5_7/stream_comments/": lambda request: stream(*page_one)})
    comments, transport = make_comments(server)
    try:
        page = await comments.list("5_7", CommentListOptions(sort="recent"))
        assert [c.id for c in page.items] == ["1", "2"] and page.total_count == 40
        query = dict(parse_qsl(server.requests[0].url.query.decode()))
        assert query["sort_order"] == "recent" and "min_id" not in query
        await comments.list("5_7", CommentListOptions(sort="recent", cursor=page.continuation))
        query = dict(parse_qsl(server.requests[1].url.query.decode()))
        assert query["min_id"] == '{"cached_comments_cursor":"2"}'
        with pytest.raises(ValueError, match="different"):
            await comments.list("5_7", CommentListOptions(cursor=page.continuation))
        with pytest.raises(ValueError, match="different"):
            await comments.list("6_7", CommentListOptions(sort="recent", cursor=page.continuation))
    finally:
        await transport.aclose()


async def test_list_rejects_more_without_a_cursor_and_replies_without_paging():
    server = Server(
        {
            "/api/v1/media/5_11/stream_comments/": lambda request: stream(
                {"comments": [], "has_more_comments": True, "status": "ok"}
            ),
            "/api/v1/media/5_11/comments/900/inline_child_comments/": {
                "child_comments": [{**COMMENT, "pk": "901", "parent_comment_id": "900"}],
                "has_more_tail_child_comments": False,
                "child_comment_count": 1,
                "status": "ok",
            },
        }
    )
    comments, transport = make_comments(server)
    try:
        with pytest.raises(ProtocolError, match="cursor"):
            await comments.list("5")
        replies = await comments.replies("5", "900")
        assert replies.items[0].parent_id == "900" and replies.total_count == 1
        server.routes["/api/v1/media/5_11/comments/900/inline_child_comments/"] = {
            "child_comments": [],
            "has_more_tail_child_comments": True,
            "status": "ok",
        }
        with pytest.raises(ProtocolError, match="continuation"):
            await comments.replies("5", "900")
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "call,expected",
    [
        (lambda c: c.post("5", "hello @someone #tag"), {"comment_text": "hello @someone #tag"}),
        (lambda c: c.reply("5", "900", "thanks"), {"replied_to_comment_id": "900"}),
        (lambda c: c.post_gif("5", "KUoa57G4"), {"comment_text": ""}),
    ],
)
async def test_comment_writes_use_one_idempotent_request(call, expected):
    server = Server({"/api/v1/media/5_11/comment/": {"comment": COMMENT, "status": "ok"}})
    comments, transport = make_comments(server)
    try:
        comment = await call(comments)
        fields = signed(server.requests[0])
        assert {k: fields[k] for k in expected} == expected
        assert fields["idempotence_token"] == fields["comment_creation_key"]
        assert fields["_uid"] == "11" and fields["delivery_class"] == "organic"
        if "gif_params" in fields:
            assert json.loads(fields["gif_params"]) == {
                "gif_media_id": "KUoa57G4",
                "is_sticker": False,
            }
        assert comment.id == "900" and comment.user.username == "me"
        assert len(server.requests) == 1
    finally:
        await transport.aclose()


async def test_photo_comment_uploads_then_configures(jpeg):
    def upload(request):
        params = json.loads(request.headers["X-Instagram-Rupload-Params"])
        assert params["upload_engine_config_enum"] == "25" and "share_type" not in params
        assert "original_width" not in json.loads(params["image_compression"])
        return httpx.Response(200, json={"upload_id": params["upload_id"], "status": "ok"})

    server = Server(
        {
            "/rupload_igphoto/": upload,
            "/api/v1/media/configure_to_comment/": {"comment": COMMENT, "status": "ok"},
        }
    )
    comments, transport = make_comments(server)
    try:
        comment = await comments.post_photo("5_7", Photo.from_bytes(jpeg))
        fields = signed(server.requests[1])
        upload_params = json.loads(server.requests[0].headers["X-Instagram-Rupload-Params"])
        assert fields["media_id"] == "5_7" and fields["upload_id"] == upload_params["upload_id"]
        assert fields["comment_text"] == "" and comment.id == "900"
    finally:
        await transport.aclose()


async def test_edit_uses_the_recorded_mutation():
    def graphql(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["fb_api_req_friendly_name"] == "CommentEditMutation"
        assert form["client_doc_id"] == "421612756810108204149064538236"
        assert json.loads(form["variables"])["data"]["comment_id"] == "900"
        assert request.headers["X-Root-Field-Name"] == "xig_comment_edit"
        payload = {
            "__typename": "XIGCommentEditMutationSuccessResponse",
            "comment": {"pk": "900", "text": "new"},
        }
        return httpx.Response(200, json={"data": {"xig_comment_edit": payload}})

    server = Server({"/graphql_www": graphql})
    comments, transport = make_comments(server)
    try:
        assert (await comments.edit("900", "new")).text == "new"
        server.routes["/graphql_www"] = {"data": {"xig_comment_edit": {"__typename": "Error"}}}
        with pytest.raises(APIError):
            await comments.edit("900", "new")
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "call,path",
    [
        (lambda c: c.delete("5", "900"), "/api/v1/media/5_11/comment/bulk_delete/"),
        (lambda c: c.like("900"), "/api/v1/media/900/comment_like/"),
        (lambda c: c.unlike("900"), "/api/v1/media/900/comment_unlike/"),
        (lambda c: c.pin("5", "900"), "/api/v1/media/5_11/pin_comment/900/"),
        (lambda c: c.unpin("5", "900"), "/api/v1/media/5_11/unpin_comment/900/"),
    ],
)
async def test_moderation_actions_use_recorded_routes(call, path):
    server = Server({"/api/v1/media/": {"status": "ok"}})
    comments, transport = make_comments(server)
    try:
        await call(comments)
        assert server.requests[0].url.path == path
        fields = signed(server.requests[0])
        assert fields["_uid"] == "11"
        if path.endswith("bulk_delete/"):
            assert fields["comment_ids_to_delete"] == "900"
    finally:
        await transport.aclose()


async def test_likers_and_input_validation():
    likers = {"users": [{"pk": "12", "username": "fan"}], "status": "ok"}
    server = Server({"/api/v1/media/900/comment_likers/": likers})
    comments, transport = make_comments(server)
    try:
        page = await comments.likers("900")
        assert page.items[0].username == "fan"
        assert "search_surface=comment_likers_page" in str(server.requests[0].url)
        for bad in (lambda: comments.post("5", " "), lambda: comments.post("x", "hi")):
            with pytest.raises(ValueError):
                await bad()
        with pytest.raises(ValueError):
            await comments.post_gif("5", "../x")
    finally:
        await transport.aclose()
    comments, transport = make_comments(server, authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await comments.post("5", "hi")
    finally:
        await transport.aclose()
