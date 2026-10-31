"""Recorded highlight changes and place search; synthetic data only."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, HighlightChanges, Place, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import ProtocolError
from async_instalib.models.stories import Highlight
from async_instalib.resources.highlights import Highlights
from async_instalib.resources.places import Places
from async_instalib.transport import Transport

REEL = {
    "id": "highlight:900",
    "title": "Lab",
    "media_count": 1,
    "cover_media": {"media_id": "5_11", "crop_rect": [0.0, 0.25, 1.0, 0.75]},
}


def make_transport(handler):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(handler),
    )


def signed_fields(request):
    form = parse_qs(request.content.decode())
    assert set(form) == {"signed_body"}
    return json.loads(form["signed_body"][0].split(".", 1)[1])


async def test_create_uses_first_story_as_square_cover():
    def server(request):
        assert request.url.path == "/api/v1/highlights/create_reel/"
        fields = signed_fields(request)
        assert fields["title"] == "Lab"
        assert json.loads(fields["media_ids"]) == ["5_11", "6_11"]
        assert json.loads(fields["cover"]) == {
            "media_id": "5_11",
            "crop_rect": "[0.0,0.21875,1.0,0.78125]",
        }
        assert fields["should_add_to_main_grid"] == "false"
        assert fields["creation_id"].isdigit()
        return httpx.Response(200, json={"status": "ok", "reel": REEL})

    transport = make_transport(server)
    try:
        highlight = await Highlights(transport).create("Lab", ["5", "6_11"])
        assert highlight.id == "highlight:900" and highlight.title == "Lab"
        with pytest.raises(ValueError):
            await Highlights(transport).create("Lab", [])
        with pytest.raises(ValueError):
            await Highlights(transport).create("Lab", ["5_12"])
    finally:
        await transport.aclose()


async def test_edit_keeps_cover_and_sends_added_and_removed_stories():
    def server(request):
        assert request.url.path == "/api/v1/highlights/highlight:900/edit_reel/"
        fields = signed_fields(request)
        assert fields["title"] == "Lab"
        assert json.loads(fields["added_media_ids"]) == ["7_11"]
        assert json.loads(fields["removed_media_ids"]) == ["5_11"]
        assert json.loads(fields["cover"]) == {
            "media_id": "5_11",
            "crop_rect": "[0.0,0.25,1.0,0.75]",
        }
        return httpx.Response(200, json={"status": "ok", "reel": {**REEL, "media_count": 1}})

    transport = make_transport(server)
    try:
        current = Highlight.from_dict(REEL)
        result = await Highlights(transport).edit(
            current, HighlightChanges(add=("7",), remove=("5_11",))
        )
        assert result.media_count == 1
        with pytest.raises(ValueError, match="cover"):
            await Highlights(transport).edit(Highlight("highlight:1"), HighlightChanges())
    finally:
        await transport.aclose()


async def test_delete_and_bad_responses():
    def server(request):
        assert request.url.path == "/api/v1/highlights/highlight:900/delete_reel/"
        assert set(signed_fields(request)) == {"_uid", "_uuid"}
        return httpx.Response(200, json={"status": "ok"})

    transport = make_transport(server)
    try:
        await Highlights(transport).delete("highlight:900")
        for value in ("900", "highlight:x", "highlight:../1"):
            with pytest.raises(ValueError):
                await Highlights(transport).delete(value)
    finally:
        await transport.aclose()

    transport = make_transport(lambda request: httpx.Response(200, json={"status": "ok"}))
    try:
        with pytest.raises(ProtocolError, match="highlight"):
            await Highlights(transport).create("Lab", ["5"])
    finally:
        await transport.aclose()


async def test_place_search_sends_the_recorded_query_and_parses_places():
    def server(request):
        assert request.url.path == "/graphql_www"
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["fb_api_req_friendly_name"] == "IGPlaceSearchQuery"
        assert form["client_doc_id"] == "182496963816957186458261742509"
        variables = json.loads(form["variables"])["request"]
        assert variables["search_query"] == "Mumbai"
        assert variables["entry_point"] == "DEFAULT"
        assert request.headers["X-Root-Field-Name"] == "ig_place_search"
        place = {"id": "1", "name": "Mumbai", "subtitle": "City", "latitude": 1.5, "longitude": 2}
        return httpx.Response(
            200, json={"data": {"ig_place_search": {"rank_token": "r", "meta_places": [place]}}}
        )

    transport = make_transport(server)
    try:
        page = await Places(transport).search("Mumbai")
        place = page.items[0]
        assert (place.id, place.name, place.address, place.latitude) == ("1", "Mumbai", "City", 1.5)
        assert json.loads(place.location_field()) == {
            "name": "Mumbai",
            "address": "City",
            "lat": 1.5,
            "lng": 2,
            "external_source": "meta_place",
            "meta_place_id": "1",
        }
        with pytest.raises(ValueError):
            await Places(transport).search(" ")
    finally:
        await transport.aclose()


def test_place_requires_identity_and_numeric_coordinates():
    with pytest.raises(ValueError):
        Place.from_dict({"id": "1", "name": ""})
    with pytest.raises(ValueError):
        Place.from_dict({"id": "1", "name": "x", "latitude": "1"})
    with pytest.raises(ValueError):
        HighlightChanges(title=" ")
