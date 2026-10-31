"""Typed story and highlight metadata from the recorded Android responses."""

from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Any

from .content import Media
from .people import PersonSummary, _boolean, _count, _identifier, _object, _string


def _person(value: object) -> PersonSummary | None:
    return PersonSummary.from_dict(_object(value)) if value is not None else None


def _highlight_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("highlight:"):
        raise ValueError("Highlight ID must be the provider's highlight:<numeric ID> value.")
    return "highlight:" + _identifier(value.removeprefix("highlight:"))


@dataclass(frozen=True, slots=True)
class StoryAuthor:
    """Story-level author identity; highlight items may omit the username."""

    id: str
    username: str | None = None
    full_name: str | None = None
    is_private: bool | None = None
    is_verified: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StoryAuthor":
        payload = _object(value)
        identifiers = [_identifier(payload[key]) for key in ("pk", "id") if key in payload]
        if not identifiers or len(set(identifiers)) != 1:
            raise ValueError("Story author requires a consistent numeric identity.")
        return cls(
            id=identifiers[0],
            username=_string(payload, "username"),
            full_name=_string(payload, "full_name"),
            is_private=_boolean(payload, "is_private"),
            is_verified=_boolean(payload, "is_verified"),
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class StoryItem(Media):
    """A story media item, with expiry and interaction availability as reported."""

    expiring_at: int | None = None
    can_reply: bool | None = None
    can_reshare: bool | None = None
    user: StoryAuthor | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StoryItem":
        media = Media.from_dict(value)
        return cls(
            **{item.name: getattr(media, item.name) for item in fields(Media)},
            expiring_at=_count(value, "expiring_at"),
            can_reply=_boolean(value, "can_reply"),
            can_reshare=_boolean(value, "can_reshare"),
            user=(
                StoryAuthor.from_dict(_object(value["user"]))
                if value.get("user") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class StoryReel:
    """One accessible story collection; seen is server metadata, never a write."""

    id: str
    items: tuple[StoryItem, ...]
    user: PersonSummary | None = None
    media_count: int | None = None
    expiring_at: int | None = None
    latest_reel_media: int | None = None
    seen: int | None = None
    can_reply: bool | None = None
    can_reshare: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StoryReel":
        payload = _object(value)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("Story reel requires an items list.")
        return cls(
            id=_identifier(payload.get("id")),
            items=tuple(StoryItem.from_dict(_object(item)) for item in items),
            user=_person(payload.get("user")),
            media_count=_count(payload, "media_count"),
            expiring_at=_count(payload, "expiring_at"),
            latest_reel_media=_count(payload, "latest_reel_media"),
            seen=_count(payload, "seen"),
            can_reply=_boolean(payload, "can_reply"),
            can_reshare=_boolean(payload, "can_reshare"),
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class StoryCollection:
    """Story response preserving null reels, broadcasts, and unknown provider metadata."""

    reel: StoryReel | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StoryCollection":
        payload = _object(value)
        if "reel" not in payload:
            raise ValueError("Story response must explicitly include reel, possibly null.")
        reel = payload["reel"]
        return cls(
            reel=StoryReel.from_dict(_object(reel)) if reel is not None else None,
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class HighlightImage:
    """A cover image descriptor; parsing it does not download its URL."""

    url: str
    width: int | None = None
    height: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HighlightImage":
        payload = _object(value)
        url = _string(payload, "url")
        if not url:
            raise ValueError("Highlight image requires a URL.")
        return cls(
            url=url,
            width=_count(payload, "width"),
            height=_count(payload, "height"),
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class HighlightCover:
    """Original and cropped highlight-cover images, including provider-native extras."""

    cropped_image: HighlightImage | None = None
    full_image: HighlightImage | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HighlightCover":
        payload = _object(value)
        cropped = payload.get("cropped_image_version")
        full = payload.get("full_image_version")
        return cls(
            cropped_image=HighlightImage.from_dict(_object(cropped))
            if cropped is not None
            else None,
            full_image=HighlightImage.from_dict(_object(full)) if full is not None else None,
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class Highlight:
    """A highlight-tray entry; its stories require a separate explicit read."""

    id: str
    title: str | None = None
    user: PersonSummary | None = None
    cover: HighlightCover | None = None
    media_count: int | None = None
    created_at: int | None = None
    updated_timestamp: int | None = None
    latest_reel_media: int | None = None
    is_pinned: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Highlight":
        payload = _object(value)
        cover = payload.get("cover_media")
        return cls(
            id=_highlight_id(payload.get("id")),
            title=_string(payload, "title"),
            user=_person(payload.get("user")),
            cover=HighlightCover.from_dict(_object(cover)) if cover is not None else None,
            media_count=_count(payload, "media_count"),
            created_at=_count(payload, "created_at"),
            updated_timestamp=_count(payload, "updated_timestamp"),
            latest_reel_media=_count(payload, "latest_reel_media"),
            is_pinned=_boolean(payload, "is_pinned_highlight"),
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class HighlightTray:
    """One explicit tray response; null completeness remains unknown."""

    items: tuple[Highlight, ...]
    cursor: str | None = field(default=None, repr=False)
    has_fetched_all: bool | None = None
    tray_type: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HighlightTray":
        payload = _object(value)
        items = payload.get("tray")
        if not isinstance(items, list):
            raise ValueError("Highlight response requires a tray list.")
        return cls(
            items=tuple(Highlight.from_dict(_object(item)) for item in items),
            cursor=_string(payload, "cursor"),
            has_fetched_all=_boolean(payload, "has_fetched_all_remaining_highlights"),
            tray_type=_string(payload, "highlights_tray_type"),
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class HighlightItems(Highlight):
    """One explicitly loaded highlight, preserving partial story-author fields."""

    items: tuple[StoryItem, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HighlightItems":
        highlight = Highlight.from_dict(value)
        items = value.get("items")
        if not isinstance(items, list):
            raise ValueError("Highlight item response requires an items list.")
        return cls(
            **{item.name: getattr(highlight, item.name) for item in fields(Highlight)},
            items=tuple(StoryItem.from_dict(_object(item)) for item in items),
        )
