"""Typed Instagram account response."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class User:
    """Stable account fields; additional provider fields remain available explicitly."""

    id: str
    username: str
    full_name: str = ""
    is_private: bool = False
    is_verified: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "User":
        identifier = value.get("pk") or value.get("id")
        username = value.get("username")
        if not identifier or not str(identifier).isdigit() or not isinstance(username, str):
            raise ValueError("User response lacks a numeric account ID or username.")
        return cls(
            id=str(identifier),
            username=username,
            full_name=str(value.get("full_name", "")),
            is_private=bool(value.get("is_private", False)),
            is_verified=bool(value.get("is_verified", False)),
            raw=dict(value),
        )
