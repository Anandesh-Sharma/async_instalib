"""Profile media fields observed in the Android profile timeline response."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .people import _boolean, _count, _object, _string


@dataclass(frozen=True, slots=True)
class Media:
    """One profile media item; all provider fields remain available in raw."""

    id: str
    media_type: int | None = None
    code: str | None = None
    product_type: str | None = None
    taken_at: int | None = None
    caption: str | None = None
    like_count: int | None = None
    comment_count: int | None = None
    has_liked: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Media":
        payload = _object(value)
        identifier = payload.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier
            or any(not part.isascii() or not part.isdigit() for part in identifier.split("_"))
        ):
            raise ValueError("Media requires a numeric or compound numeric ID.")
        caption = payload.get("caption")
        text = None
        if caption is not None:
            text = _string(_object(caption), "text")
        return cls(
            id=identifier,
            media_type=_count(payload, "media_type"),
            code=_string(payload, "code"),
            product_type=_string(payload, "product_type"),
            taken_at=_count(payload, "taken_at"),
            caption=text,
            like_count=_count(payload, "like_count"),
            comment_count=_count(payload, "comment_count"),
            has_liked=_boolean(payload, "has_liked"),
            raw=deepcopy(payload),
        )
