import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.config.content import ContentOptions
from async_instalib.exceptions import ProtocolError
from async_instalib.models.content import Media
from async_instalib.resources.content import ProfileContent
from async_instalib.transport import Transport


def transport(server):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )


def envelope(**kwargs):
    return {
        "data": {
            "xdt_api__v1__profile_timeline": {
                "profile_grid_items": [
                    {"item_type": "MEDIA", "media": {"id": "123_7", "media_type": 1}}
                ],
                "more_available": False,
                **kwargs,
            }
        }
    }


async def test_grid_merges_deferred_media_fields_and_never_autoloads():
    requests = []

    def server(request):
        requests.append(request)
        initial = envelope(
            more_available=True,
            next_max_id="opaque-page",
            profile_grid_items_cursor="opaque-grid",
            pinned_profile_grid_items_ids="",
        )
        deferred = {
            "path": ["xdt_api__v1__profile_timeline", "profile_grid_items", 0, "media"],
            "data": {"caption": {"text": "Synthetic caption"}, "like_count": 5},
        }
        return httpx.Response(200, content=json.dumps(initial) + "\n" + json.dumps(deferred))

    client = transport(server)
    try:
        page = await ProfileContent(client).posts("7")
        assert len(requests) == 1
        assert page.items[0].caption == "Synthetic caption"
        assert page.items[0].like_count == 5
        assert page.items[0].comment_count is None
        assert page.has_more
        form = parse_qs(requests[0].content.decode())
        assert form["fb_api_req_friendly_name"] == ["IGProfileTimelineQuery"]
        variables = json.loads(form["variables"][0])
        assert variables["user_id"] == "7"
        assert variables["exclude_pinned_posts"] is False
        with pytest.raises(ValueError):
            await ProfileContent(client).posts("8", ContentOptions(cursor=page.continuation))
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "body",
    [
        envelope(profile_grid_items=None),
        envelope(profile_grid_items=[{"media": {"id": "bad"}}]),
        envelope(more_available=True),
        envelope(more_available="true"),
        {"data": {"xdt_api__v1__profile_timeline": None}},
    ],
)
async def test_invalid_grid_fails_instead_of_appearing_empty(body):
    client = transport(lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(ProtocolError):
            await ProfileContent(client).posts("7")
    finally:
        await client.aclose()


async def test_story_and_highlight_reads_do_not_mark_seen():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "reel": None, "tray": []})

    client = transport(server)
    try:
        content = ProfileContent(client)
        assert (await content.stories("7"))["reel"] is None
        assert (await content.highlights("7"))["tray"] == []
        assert [r.method for r in requests] == ["GET", "GET"]
        assert [r.url.path for r in requests] == [
            "/api/v1/feed/user/7/story/",
            "/api/v1/highlights/7/highlights_tray/",
        ]
    finally:
        await client.aclose()


def test_media_preserves_unknown_fields_and_hides_raw():
    source = {"id": "123_7", "caption": {"text": "Synthetic"}, "future": {"x": 1}}
    media = Media.from_dict(source)
    source["future"]["x"] = 2
    assert media.raw["future"]["x"] == 1
    assert "future" not in repr(media)
    with pytest.raises(ValueError):
        ContentOptions(variables={"user_id": "8"})


@pytest.mark.parametrize("variables", [{"flag": float("nan")}, {"flag": {1, 2}}])
def test_content_options_reject_non_json_variables(variables):
    with pytest.raises(ValueError):
        ContentOptions(variables=variables)


async def test_wrong_option_classes_fail_before_dispatch():
    from async_instalib.resources.profiles import Profiles

    def server(request):
        pytest.fail("Invalid options reached the wire")

    client = transport(server)
    try:
        for method in [
            ProfileContent(client).posts,
            ProfileContent(client).stories,
            ProfileContent(client).highlights,
            Profiles(client).by_id,
            Profiles(client).related,
        ]:
            with pytest.raises(ValueError):
                await method("7", {})
    finally:
        await client.aclose()


def test_related_options_do_not_override_bound_identity():
    from async_instalib.config.content import RelatedOptions

    with pytest.raises(ValueError):
        RelatedOptions(parameters={"target_id": "8"})
    with pytest.raises(ValueError):
        RelatedOptions(username="../target")


async def test_grid_continuation_preserves_all_three_provider_fields_and_context():
    requests = []

    def server(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=envelope(
                    more_available=True,
                    next_max_id="next-media",
                    profile_grid_items_cursor="opaque-grid-cursor",
                    pinned_profile_grid_items_ids="pinned-one,pinned-two",
                ),
            )
        return httpx.Response(200, json=envelope())

    client = transport(server)
    try:
        content = ProfileContent(client)
        options = ContentOptions(variables={"exclude_pinned_posts": True})
        page = await content.posts("7", options)
        with pytest.raises(ValueError):
            await content.posts("7", ContentOptions(cursor=page.continuation))
        assert len(requests) == 1
        next_page = await content.posts(
            "7", ContentOptions(limit=12, cursor=page.continuation, variables=options.variables)
        )
        assert not next_page.has_more
        variables = json.loads(parse_qs(requests[1].content.decode())["variables"][0])
        assert variables["max_id"] == "next-media"
        assert variables["profile_grid_items_cursor"] == "opaque-grid-cursor"
        assert variables["pinned_profile_grid_items_ids"] == "pinned-one,pinned-two"
        assert variables["request_media_chunk"] is False
        assert variables["enable_carousel_media_count_in_deferred"] is False
        assert variables["exclude_pinned_posts"] is True
        assert "lightweight_carousel_type" not in variables
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "cursor_value", ["legacy-max-id", "{}", '{"max_id": "x", "profile_grid_items_cursor": "y"}']
)
async def test_incomplete_grid_cursor_rejected_before_dispatch(cursor_value):
    from async_instalib.models.pagination import Continuation, PageContext

    def server(request):
        pytest.fail("Incomplete cursor reached the wire")

    client = transport(server)
    try:
        cursor = Continuation(
            cursor_value, PageContext("profile_posts", "42", target="7", query="{}")
        )
        with pytest.raises(ValueError):
            await ProfileContent(client).posts("7", ContentOptions(cursor=cursor))
    finally:
        await client.aclose()


async def test_highlights_merge_progressive_tray_and_preserve_initial_hints():
    first = {"status": "ok", "tray": [], "client_hints": {"display": "grid"}}
    second = {
        "status": "ok",
        "tray": [{"id": "highlight:123", "title": "Synthetic"}],
        "cursor": None,
    }
    client = transport(
        lambda request: httpx.Response(200, content=json.dumps(first) + "\n" + json.dumps(second))
    )
    try:
        result = await ProfileContent(client).highlights("7")
        assert len(result["tray"]) == 1
        assert result["client_hints"] == {"display": "grid"}
        assert result["cursor"] is None
    finally:
        await client.aclose()


async def test_reels_deferred_paging_and_second_request_use_captured_contract():
    from async_instalib.config.content import ReelsOptions

    requests = []

    def server(request):
        requests.append(request)
        root = {
            "data": {
                "xdt_user_clips_graphql": {
                    "items": [{"media": {"id": "123_7", "media_type": 2}}],
                    "paging_info": {},
                }
            }
        }
        patch = {
            "path": ["xdt_user_clips_graphql"],
            "data": {
                "paging_info": {"more_available": len(requests) == 1, "max_id": "opaque-reels"}
            },
        }
        return httpx.Response(200, content=json.dumps(root) + "\n" + json.dumps(patch))

    client = transport(server)
    try:
        content = ProfileContent(client)
        first = await content.reels("7")
        assert first.has_more and len(requests) == 1
        with pytest.raises(ValueError):
            await content.reels("7", ReelsOptions(cursor=first.continuation, sort_by_views=True))
        with pytest.raises(ValueError):
            await content.posts("7", ContentOptions(cursor=first.continuation))
        second = await content.reels("7", ReelsOptions(cursor=first.continuation))
        assert not second.has_more
        form = parse_qs(requests[1].content.decode())
        assert form["fb_api_req_friendly_name"] == ["ClipsProfileQuery"]
        variables = json.loads(form["variables"][0])
        assert variables["data"]["max_id"] == "opaque-reels"
        assert variables["data"]["page_size"] is None
        assert variables["data"]["target_user_id"] == "7"
        assert variables["initial_stream_count"] == 6
    finally:
        await client.aclose()


@pytest.mark.parametrize("paging", [{"more_available": True}, {"more_available": "true"}, None])
async def test_reels_reject_incomplete_paging(paging):
    client = transport(
        lambda request: httpx.Response(
            200, json={"data": {"xdt_user_clips_graphql": {"items": [], "paging_info": paging}}}
        )
    )
    try:
        with pytest.raises(ProtocolError):
            await ProfileContent(client).reels("7")
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"chunk_size": 0},
        {"sort_by_views": 1},
        {"parameters": {"target_user_id": "8"}},
        {"variables": {"data": {}}},
    ],
)
def test_reel_options_validate_controlled_parameters(kwargs):
    from async_instalib.config.content import ReelsOptions

    with pytest.raises(ValueError):
        ReelsOptions(**kwargs)


async def test_tagged_media_retains_count_and_does_not_autoload():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "items": [{"id": "123_7", "media_type": 1}],
                "more_available": True,
                "next_max_id": "next-tagged",
                "total_count": 14,
            },
        )

    client = transport(server)
    try:
        page = await ProfileContent(client).tagged("7")
        assert page.has_more
        assert page.total_count == 14
        assert page.items[0].id == "123_7"
        assert len(requests) == 1
        assert requests[0].url.path == "/api/v1/usertags/7/feed/"
        assert requests[0].url.params["count"] == "12"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "more_available": True},
        {"items": {}, "more_available": False},
        {"items": [], "more_available": False, "total_count": "14"},
    ],
)
async def test_tagged_invalid_pages_are_not_silently_empty(payload):
    client = transport(lambda request: httpx.Response(200, json={"status": "ok", **payload}))
    try:
        with pytest.raises(ProtocolError):
            await ProfileContent(client).tagged("7")
    finally:
        await client.aclose()


async def test_tagged_integer_cursor_normalizes_to_query_string():
    from async_instalib.config.content import TaggedOptions

    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(
            200, json={"status": "ok", "items": [], "more_available": True, "next_max_id": 12345}
        )

    client = transport(server)
    try:
        content = ProfileContent(client)
        first = await content.tagged("7")
        await content.tagged("7", TaggedOptions(cursor=first.continuation))
        assert requests[1].url.params["max_id"] == "12345"
    finally:
        await client.aclose()
