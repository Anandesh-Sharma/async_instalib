"""Typed metadata for explicit relationship changes."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal


def _parameters(value: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("parameters must be a mapping of string fields.")
    protected = {
        "user_id",
        "user_ids",
        "target_user_id",
        "target_user_ids",
        "target_posts_author_id",
        "target_reel_author_id",
        "device_id",
        "authorization",
        "cookie",
        "access_token",
        "auth_token",
        "sessionid",
        "csrftoken",
        "password",
        "username",
        "signed_body",
        "nav_chain",
        "container_module",
        "radio_type",
        "action",
        "preference",
        "is_auto_block_enabled",
        "surface",
        "add",
        "remove",
        "block_on_empty_thread_creation",
    }
    result = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", key)
            or key.lower() in protected
            or key.lower().startswith("target_")
            or key.lower().endswith(("_id", "_ids", "_token"))
            or not isinstance(item, str)
            or any(ord(char) < 32 or ord(char) == 127 for char in item)
        ):
            raise ValueError(
                "Extra action parameters cannot replace target, identity, or owned fields."
            )
        result[key] = item
    return MappingProxyType(result)


def _text(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Action metadata must be nonempty text without control characters.")


@dataclass(frozen=True, slots=True)
class ActionOptions:
    """Captured relationship metadata plus explicit provider parameter overrides."""

    container_module: str = "profile"
    radio_type: str = "wifi-none"
    parameters: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _text(self.container_module)
        _text(self.radio_type)
        object.__setattr__(self, "parameters", _parameters(self.parameters))


@dataclass(frozen=True, slots=True)
class MuteOptions:
    """Mute one recorded content category per call."""

    content: Literal["posts", "stories", "notes", "activity", "map_notes", "map_location"] = "posts"
    container_module: str = "media_mute_sheet"
    parameters: Mapping[str, str] = field(default_factory=dict, repr=False)
    fbid_v2: str | None = None
    map_entry_point: str = "PROFILE"

    def __post_init__(self) -> None:
        if self.content not in {
            "posts",
            "stories",
            "notes",
            "activity",
            "map_notes",
            "map_location",
        }:
            raise ValueError("Unsupported mute content category.")
        _text(self.container_module)
        _text(self.map_entry_point)
        if self.fbid_v2 is not None and (
            not isinstance(self.fbid_v2, str)
            or not self.fbid_v2.isascii()
            or not self.fbid_v2.isdigit()
            or int(self.fbid_v2) <= 0
        ):
            raise ValueError("fbid_v2 must be the positive numeric ID from the target profile.")
        if self.content == "map_location" and self.fbid_v2 is None:
            raise ValueError("Map location muting requires the target profile's fbid_v2.")
        parameters = _parameters(self.parameters)
        if {"entry_point", "fbid_v2"}.intersection(parameters):
            raise ValueError("Configure map context through fbid_v2 and map_entry_point.")
        object.__setattr__(self, "parameters", parameters)


@dataclass(frozen=True, slots=True)
class NotificationOptions:
    """Per-person notification selection; only recorded combinations are accepted by operations."""

    content: Literal["posts", "stories", "reels", "live"]
    mode: Literal["all", "relevant", "off"]
    parameters: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.content not in {"posts", "stories", "reels", "live"}:
            raise ValueError("Notification content must be posts, stories, reels, or live.")
        if self.mode not in {"all", "relevant", "off"}:
            raise ValueError("Notification mode must be all, relevant, or off.")
        object.__setattr__(self, "parameters", _parameters(self.parameters))


@dataclass(frozen=True, slots=True)
class BlockOptions:
    """Captured blocking scope; auto_block enables the app's associated-account option.

    App blocking with auto_block=True was recorded. Both boolean values were
    accepted in controlled live SDK checks, followed by successful unblocking.
    """

    auto_block: bool = True
    surface: str = "profile"
    container_module: str = "profile"
    parameters: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if type(self.auto_block) is not bool:
            raise ValueError("auto_block must be a boolean.")
        _text(self.surface)
        _text(self.container_module)
        object.__setattr__(self, "parameters", _parameters(self.parameters))


@dataclass(frozen=True, slots=True)
class CloseFriendsOptions:
    """Entry context for changing one close-friends member without replacing the list."""

    module: str = "audience_selection"
    source: str = "settings"
    parameters: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _text(self.module)
        _text(self.source)
        parameters = _parameters(self.parameters)
        if {"module", "source"}.intersection(parameters):
            raise ValueError("Configure close-friends context through module and source.")
        object.__setattr__(self, "parameters", parameters)


@dataclass(frozen=True, slots=True)
class FavoritesOptions:
    """Context for changing one feed favorite without replacing other favorites."""

    module: str = "following_sheet"
    block_on_empty_thread_creation: bool = False
    parameters: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _text(self.module)
        if type(self.block_on_empty_thread_creation) is not bool:
            raise ValueError("block_on_empty_thread_creation must be a boolean.")
        parameters = _parameters(self.parameters)
        if "module" in parameters:
            raise ValueError("Configure favorite entry context through module.")
        object.__setattr__(self, "parameters", parameters)
