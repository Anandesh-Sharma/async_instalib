"""Typed direct-message threads and messages from the recorded inbox responses."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .people import PersonSummary, _boolean, _identifier, _object, _string


def _seconds(value: object) -> float | None:
    """Inbox timestamps are microseconds; return seconds."""
    if type(value) is int and value > 0:
        return value / 1_000_000
    if isinstance(value, str) and value.isdigit():
        return int(value) / 1_000_000
    return None


@dataclass(frozen=True, slots=True)
class Message:
    """One thread item. ``type`` is Instagram's ``item_type`` (``text``, ``link``,
    ``media``, ``voice_media``, ``animated_media``, ``like``, ``clip``, and others);
    content beyond ``text`` is in ``raw``.
    """

    id: str
    thread_id: str
    user_id: str | None = None
    type: str = ""
    text: str | None = None
    sent_at: float | None = None
    is_sent_by_viewer: bool | None = None
    client_context: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any], thread_id: str) -> "Message":
        payload = _object(value)
        identifier = payload.get("item_id")
        if not isinstance(identifier, str) or not identifier.isdigit():
            raise ValueError("Message requires a numeric item_id.")
        user = payload.get("user_id")
        context = payload.get("client_context")
        return cls(
            id=identifier,
            thread_id=thread_id,
            user_id=_identifier(user) if user is not None else None,
            type=_string(payload, "item_type") or "",
            text=_string(payload, "text"),
            sent_at=_seconds(payload.get("timestamp")),
            is_sent_by_viewer=_boolean(payload, "is_sent_by_viewer"),
            client_context=str(context) if context is not None else None,
            raw=deepcopy(payload),
        )


@dataclass(frozen=True, slots=True)
class Thread:
    """One conversation with its newest messages, newest first."""

    id: str
    title: str | None = None
    users: tuple[PersonSummary, ...] = ()
    messages: tuple[Message, ...] = ()
    last_activity_at: float | None = None
    is_group: bool | None = None
    muted: bool | None = None
    pending: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Thread":
        payload = _object(value)
        identifier = payload.get("thread_id")
        if not isinstance(identifier, str) or not identifier.isdigit():
            raise ValueError("Thread requires a numeric thread_id.")
        users = payload.get("users") or []
        items = payload.get("items") or []
        if not isinstance(users, list) or not isinstance(items, list):
            raise ValueError("Thread users and items must be lists.")
        return cls(
            id=identifier,
            title=_string(payload, "thread_title"),
            users=tuple(PersonSummary.from_dict(_object(user)) for user in users),
            messages=tuple(Message.from_dict(_object(item), identifier) for item in items),
            last_activity_at=_seconds(payload.get("last_activity_at")),
            is_group=_boolean(payload, "is_group"),
            muted=_boolean(payload, "muted"),
            pending=_boolean(payload, "pending"),
            raw=deepcopy(payload),
        )
