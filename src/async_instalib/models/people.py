"""Typed person fields with explicit unknown values for partial responses."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


def _identifier(value: object) -> str:
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0:
        return str(int(value))
    raise ValueError("Person ID must be a positive ASCII integer or numeric string.")


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Person response must be a JSON object with string keys.")
    return value


def _string(value: dict[str, Any], name: str) -> str | None:
    result = value.get(name)
    if result is not None and not isinstance(result, str):
        raise ValueError(f"Person field {name} must be a string or null.")
    return result


def _boolean(value: dict[str, Any], name: str) -> bool | None:
    result = value.get(name)
    if result is not None and type(result) is not bool:
        raise ValueError(f"Person field {name} must be a boolean or null.")
    return result


def _count(value: dict[str, Any], name: str) -> int | None:
    result = value.get(name)
    if result is not None and (type(result) is not int or result < 0):
        raise ValueError(f"Person field {name} must be a nonnegative integer or null.")
    return result


@dataclass(frozen=True, slots=True)
class Relationship:
    """Account-relative relationship state; omitted flags remain unknown."""

    user_id: str | None = None
    following: bool | None = None
    followed_by: bool | None = None
    outgoing_request: bool | None = None
    incoming_request: bool | None = None
    blocking: bool | None = None
    is_restricted: bool | None = None
    muting: bool | None = None
    is_muting_reel: bool | None = None
    is_bestie: bool | None = None
    is_private: bool | None = None
    is_feed_favorite: bool | None = None
    is_muting_notes: bool | None = None
    is_muting_media_notes: bool | None = None
    is_muting_media_reposts: bool | None = None
    is_muting_friend_map: bool | None = None
    is_muting_friend_map_location: bool | None = None
    is_muting_quick_snap: bool | None = None
    is_blocking_reel: bool | None = None
    subscribed: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any], user_id: str | None = None) -> "Relationship":
        payload = _object(value)
        identifier = _identifier(user_id) if user_id is not None else None
        if payload.get("user_id") is not None:
            returned_identifier = _identifier(payload["user_id"])
            if identifier is not None and identifier != returned_identifier:
                raise ValueError("Relationship response identifies a different person.")
            identifier = returned_identifier
        return cls(
            user_id=identifier,
            following=_boolean(payload, "following"),
            followed_by=_boolean(payload, "followed_by"),
            outgoing_request=_boolean(payload, "outgoing_request"),
            incoming_request=_boolean(payload, "incoming_request"),
            blocking=_boolean(payload, "blocking"),
            is_restricted=_boolean(payload, "is_restricted"),
            muting=_boolean(payload, "muting"),
            is_muting_reel=_boolean(payload, "is_muting_reel"),
            is_bestie=_boolean(payload, "is_bestie"),
            is_private=_boolean(payload, "is_private"),
            is_feed_favorite=_boolean(payload, "is_feed_favorite"),
            is_muting_notes=_boolean(payload, "is_muting_notes"),
            is_muting_media_notes=_boolean(payload, "is_muting_media_notes"),
            is_muting_media_reposts=_boolean(payload, "is_muting_media_reposts"),
            is_muting_friend_map=_boolean(payload, "is_muting_friend_map"),
            is_muting_friend_map_location=_boolean(payload, "is_muting_friend_map_location"),
            is_muting_quick_snap=_boolean(payload, "is_muting_quick_snap"),
            is_blocking_reel=_boolean(payload, "is_blocking_reel"),
            subscribed=_boolean(payload, "subscribed"),
            raw=deepcopy(payload),
        )


def _summary_fields(value: dict[str, Any]) -> dict[str, Any]:
    payload = _object(value)
    identifiers = [_identifier(payload[key]) for key in ("pk", "id") if key in payload]
    if not identifiers or len(set(identifiers)) != 1:
        raise ValueError("Person response requires one consistent numeric account ID.")
    username = _string(payload, "username")
    if not username or not username.strip():
        raise ValueError("Person response requires a nonempty username.")
    relationship = payload.get("friendship_status")
    return {
        "id": identifiers[0],
        "username": username,
        "fbid_v2": _identifier(payload["fbid_v2"]) if payload.get("fbid_v2") is not None else None,
        "full_name": _string(payload, "full_name"),
        "profile_pic_url": _string(payload, "profile_pic_url"),
        "is_private": _boolean(payload, "is_private"),
        "is_verified": _boolean(payload, "is_verified"),
        "relationship": (
            Relationship.from_dict(_object(relationship), identifiers[0])
            if relationship is not None
            else None
        ),
        "raw": deepcopy(payload),
    }


@dataclass(frozen=True, slots=True)
class PersonSummary:
    """Partial identity returned by search and account lists."""

    id: str
    username: str
    full_name: str | None = None
    profile_pic_url: str | None = None
    is_private: bool | None = None
    is_verified: bool | None = None
    relationship: Relationship | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    fbid_v2: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PersonSummary":
        return cls(**_summary_fields(value))


@dataclass(frozen=True, slots=True)
class PersonProfile(PersonSummary):
    """Profile details, preserving absent or inaccessible fields as unknown."""

    biography: str | None = None
    external_url: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    media_count: int | None = None
    is_business: bool | None = None
    category: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PersonProfile":
        fields = _summary_fields(value)
        return cls(
            **fields,
            biography=_string(value, "biography"),
            external_url=_string(value, "external_url"),
            follower_count=_count(value, "follower_count"),
            following_count=_count(value, "following_count"),
            media_count=_count(value, "media_count"),
            is_business=_boolean(value, "is_business"),
            category=_string(value, "category"),
        )
