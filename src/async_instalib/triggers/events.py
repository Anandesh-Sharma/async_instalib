"""One event shape for every trigger source, so handlers and webhooks never change per source."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

# Event types the built-in sources emit. A new source adds its own type names;
# "notification" covers activity rows with no mapping yet.
COMMENT = "comment"
MENTION = "mention"
LIKE = "like"
FOLLOW = "follow"
TAG = "tag"
NOTIFICATION = "notification"
MESSAGE = "message"
MESSAGE_REACTION = "message_reaction"
MESSAGE_REQUEST = "message_request"


@dataclass(frozen=True, slots=True)
class Actor:
    """The person who caused an event, when Instagram names one."""

    id: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """Something that happened to the signed-in account.

    ``id`` is unique and stable (``<source>:<Instagram id>``), so receivers can drop
    repeats. ``data`` keeps the provider's original fields for anything not typed here.
    """

    id: str
    type: str
    source: str
    account_id: str
    occurred_at: float | None = None
    actor: Actor | None = None
    media_id: str | None = None
    comment_id: str | None = None
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("id", "type", "source", "account_id"):
            value = getattr(self, name)
            # IDs and types are sent as webhook headers, which must be printable ASCII.
            if not isinstance(value, str) or not value or not value.isascii():
                raise ValueError(f"Event {name} must be nonempty ASCII text.")
            if not value.isprintable():
                raise ValueError(f"Event {name} must be printable.")
        object.__setattr__(self, "data", deepcopy(self.data))

    def to_dict(self) -> dict[str, Any]:
        """The JSON body sent to webhooks."""
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "account_id": self.account_id,
            "occurred_at": self.occurred_at,
            "actor": (
                {"id": self.actor.id, "username": self.actor.username} if self.actor else None
            ),
            "media_id": self.media_id,
            "comment_id": self.comment_id,
            "text": self.text,
            "data": deepcopy(self.data),
        }
