"""Typed notifications from the recorded activity feed (the app's heart tab)."""

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .people import _identifier, _object

# "{name|color|bold|user?id=1|none} commented: hi" -> "name commented: hi"
_RICH_ENTITY = re.compile(r"\{([^|{}]*)\|[^{}]*\}")


def plain_text(rich_text: str) -> str:
    """Drop the app's inline link markup, keeping the visible words."""
    return _RICH_ENTITY.sub(r"\1", rich_text)


@dataclass(frozen=True, slots=True)
class Notification:
    """One activity-feed row; ``name`` is Instagram's own type, such as ``comment``."""

    id: str
    name: str
    text: str = ""
    actor_id: str | None = None
    actor_username: str | None = None
    media_ids: tuple[str, ...] = ()
    comment_id: str | None = None
    occurred_at: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Notification":
        payload = _object(value)
        identifier, name = payload.get("pk"), payload.get("notif_name")
        if not isinstance(identifier, str) or not identifier or not isinstance(name, str):
            raise ValueError("Notification requires its pk and notif_name.")
        args = _object(payload.get("args") or {})
        media = args.get("media") or []
        if not isinstance(media, list):
            raise ValueError("Notification media must be a list.")
        media_ids = tuple(str(_object(item).get("id")) for item in media)
        if any(not part.replace("_", "").isdigit() for part in media_ids):
            raise ValueError("Notification media IDs must be numeric.")
        timestamp = args.get("timestamp")
        username = args.get("profile_name")
        return cls(
            id=identifier,
            name=name,
            text=plain_text(args.get("rich_text") or args.get("text") or ""),
            actor_id=_identifier(args["profile_id"]) if args.get("profile_id") else None,
            actor_username=username if isinstance(username, str) else None,
            media_ids=media_ids,
            comment_id=_identifier(args["comment_id"]) if args.get("comment_id") else None,
            occurred_at=float(timestamp) if isinstance(timestamp, (int, float)) else None,
            raw=deepcopy(payload),
        )
