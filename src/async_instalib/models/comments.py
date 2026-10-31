"""Typed comments from the recorded comment list, reply, and write responses."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .people import PersonSummary, _boolean, _count, _identifier, _object, _string


def _optional_id(value: dict[str, Any], name: str) -> str | None:
    return _identifier(value[name]) if value.get(name) is not None else None


@dataclass(frozen=True, slots=True)
class Comment:
    """One comment or reply; unknown fields stay ``None`` and all fields remain in raw.

    ``text`` is empty for GIF and photo comments. ``parent_id`` is set on replies.
    """

    id: str
    text: str = ""
    user: PersonSummary | None = None
    media_id: str | None = None
    parent_id: str | None = None
    created_at: int | None = None
    like_count: int | None = None
    reply_count: int | None = None
    has_liked: bool | None = None
    is_edited: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Comment":
        payload = _object(value)
        user = payload.get("user")
        return cls(
            id=_identifier(payload.get("pk")),
            text=_string(payload, "text") or "",
            user=PersonSummary.from_dict(_object(user)) if user is not None else None,
            media_id=_optional_id(payload, "media_id"),
            parent_id=_optional_id(payload, "parent_comment_id"),
            created_at=_count(payload, "created_at"),
            like_count=_count(payload, "comment_like_count"),
            reply_count=_count(payload, "child_comment_count"),
            has_liked=_boolean(payload, "has_liked_comment"),
            is_edited=_boolean(payload, "is_edited"),
            raw=deepcopy(payload),
        )
