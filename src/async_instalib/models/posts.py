"""Typed results of publishing: tagged places and Instagram's publish acknowledgement."""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .content import Media
from .people import _identifier, _object, _string


@dataclass(frozen=True, slots=True)
class Place:
    """A Meta place from location search; pass it to options to tag a location."""

    id: str
    name: str
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Place":
        payload = _object(value)
        name = _string(payload, "name")
        if not name:
            raise ValueError("Place requires a name.")
        coordinates = [payload.get(key) for key in ("latitude", "longitude")]
        if any(item is not None and type(item) not in {int, float} for item in coordinates):
            raise ValueError("Place coordinates must be numbers or null.")
        return cls(
            id=_identifier(payload.get("id")),
            name=name,
            address=_string(payload, "subtitle") or "",
            latitude=coordinates[0],
            longitude=coordinates[1],
            raw=deepcopy(payload),
        )

    def location_field(self) -> str:
        """The recorded ``location`` form value for configure and edit requests."""
        return json.dumps(
            {
                "name": self.name,
                "address": self.address,
                "lat": self.latitude,
                "lng": self.longitude,
                "external_source": "meta_place",
                "meta_place_id": self.id,
            },
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class Publication:
    """Instagram's acknowledgement of one publish request.

    ``status`` is ``COMPLETED`` once the media is live. ``PENDING`` means Instagram is
    still processing a video; read it again with ``posts.status(upload_id)``.
    ``media`` is the published item and ``children`` are a carousel's items.
    """

    id: str
    upload_id: str
    status: str
    media: Media | None = None
    children: tuple[Media, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def completed(self) -> bool:
        return self.status == "COMPLETED"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Publication":
        payload = _object(value)
        posts = payload.get("posts")
        if not isinstance(posts, list) or len(posts) != 1:
            raise ValueError("Publish response must describe exactly one post.")
        post = _object(posts[0])
        status = _string(post, "status")
        upload_id = _string(post, "post_client_id")
        if not status or not upload_id:
            raise ValueError("Publish response lacks the post status or client ID.")
        identifier = _identifier(post.get("id"))
        published: Media | None = None
        children = []
        for row in payload.get("medias") or []:
            media = Media.from_dict(_object(_object(row).get("media_dict")))
            if media.id.split("_")[0] == identifier:
                published = media
            else:
                children.append(media)
        return cls(identifier, upload_id, status, published, tuple(children), deepcopy(payload))
