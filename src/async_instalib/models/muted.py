"""Mute categories established by machine-bound screen controls."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MutedPerson:
    """A verified numeric person identity; absent category controls remain unknown.

    No username is inferred from the localized or positional display labels.
    """

    id: str
    username: str | None = None
    posts: bool | None = None
    stories: bool | None = None
    notes: bool | None = None
    activity: bool | None = None
    map_notes: bool | None = None
    map_location: bool | None = None
    quick_snap: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
