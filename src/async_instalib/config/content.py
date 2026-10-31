"""Options for recorded profile and content requests."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..models.pagination import Continuation


@dataclass(frozen=True, slots=True)
class ProfileOptions:
    """Context metadata sent by the Android profile screen."""

    module: str = "serp_users"
    entry_point: str = "search_navigate_to_user"
    push_disabled: bool = True

    def __post_init__(self) -> None:
        for value in (self.module, self.entry_point):
            if not isinstance(value, str) or not value:
                raise ValueError("Profile context values must be nonempty strings.")
        if type(self.push_disabled) is not bool:
            raise ValueError("push_disabled must be a boolean.")


@dataclass(frozen=True, slots=True)
class ContentOptions:
    """One profile grid page and explicit overrides for recorded feature variables."""

    limit: int = 9
    cursor: Continuation | None = None
    variables: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("limit must be a positive integer.")
        if self.cursor is not None and not isinstance(self.cursor, Continuation):
            raise ValueError("cursor must be a Continuation returned by a previous page.")
        if not isinstance(self.variables, dict) or any(
            not isinstance(key, str) for key in self.variables
        ):
            raise ValueError("variables must be a dictionary with string keys.")
        if {
            "user_id",
            "count",
            "max_id",
            "profile_grid_items_cursor",
            "pinned_profile_grid_items_ids",
        } & self.variables.keys():
            raise ValueError(
                "Configure target, count, and continuation through their typed inputs."
            )
        try:
            json.dumps(self.variables, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise ValueError("Content variables must contain finite JSON values.") from None
        object.__setattr__(self, "variables", deepcopy(self.variables))


@dataclass(frozen=True, slots=True)
class MetadataOptions:
    """Explicit query overrides for story/highlight metadata, without automatic paging.

    Use provider parameter names, including supported_capabilities_new when needed.
    Cursor parameters are provider-native because highlight continuation has not been
    observed in the current capture; no automatic continuation contract is implied.
    """

    parameters: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.parameters, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.parameters.items()
        ):
            raise ValueError("Metadata parameters must contain string keys and values.")
        object.__setattr__(self, "parameters", dict(self.parameters))


@dataclass(frozen=True, slots=True)
class RelatedOptions:
    """Optional target username and context for profile recommendations."""

    username: str | None = None
    parameters: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.username is not None and (
            not isinstance(self.username, str)
            or not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", self.username)
        ):
            raise ValueError("username must be an Instagram username without @.")
        validated = MetadataOptions(self.parameters)
        if {"target_id", "target_username"} & validated.parameters.keys():
            raise ValueError("Configure the target through user_id and username.")
        object.__setattr__(self, "parameters", validated.parameters)


@dataclass(frozen=True, slots=True)
class ReelsOptions:
    """Recorded profile-reel filtering, paging, and explicit feature overrides."""

    limit: int | None = None
    cursor: Continuation | None = None
    sort_by_views: bool = False
    include_feed_video: bool = True
    chunk_size: int = 6
    variables: dict[str, Any] = field(default_factory=dict, repr=False)
    parameters: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.limit is not None and (type(self.limit) is not int or self.limit < 1):
            raise ValueError("limit must be a positive integer or None for the provider default.")
        if type(self.chunk_size) is not int or self.chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer.")
        if type(self.sort_by_views) is not bool or type(self.include_feed_video) is not bool:
            raise ValueError("Reel filtering flags must be booleans.")
        if self.cursor is not None and not isinstance(self.cursor, Continuation):
            raise ValueError("cursor must be a Continuation returned by a previous page.")
        reserved = {
            "target_user_id",
            "page_size",
            "max_id",
            "sort_by_views",
            "include_feed_video",
            "no_of_medias_in_each_chunk",
        }
        for mapping in (self.variables, self.parameters):
            if not isinstance(mapping, dict) or any(not isinstance(key, str) for key in mapping):
                raise ValueError("Reel overrides must be dictionaries with string keys.")
            try:
                json.dumps(mapping, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise ValueError("Reel overrides must contain finite JSON values.") from None
        if "data" in self.variables or reserved.intersection(self.parameters):
            raise ValueError(
                "Configure target, filtering, paging and chunk size through typed inputs."
            )
        object.__setattr__(self, "variables", deepcopy(self.variables))
        object.__setattr__(self, "parameters", deepcopy(self.parameters))


@dataclass(frozen=True, slots=True)
class TaggedOptions:
    """One tagged-media page and explicit query metadata overrides."""

    limit: int = 12
    cursor: Continuation | None = None
    parameters: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("limit must be a positive integer.")
        if self.cursor is not None and not isinstance(self.cursor, Continuation):
            raise ValueError("cursor must be a Continuation returned by a previous page.")
        validated = MetadataOptions(self.parameters)
        if {"count", "max_id"} & validated.parameters.keys():
            raise ValueError("Configure count and continuation through their typed inputs.")
        object.__setattr__(self, "parameters", validated.parameters)


@dataclass(frozen=True, slots=True)
class RepostsOptions:
    """A repost page; the captured operation does not expose a count parameter."""

    cursor: Continuation | None = None
    prepend_author_ids: tuple[str, ...] | None = None
    variables: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.cursor is not None and not isinstance(self.cursor, Continuation):
            raise ValueError("cursor must be a Continuation returned by a previous page.")
        if self.prepend_author_ids is not None:
            if not isinstance(self.prepend_author_ids, (list, tuple)) or any(
                not isinstance(item, str)
                or not item.isascii()
                or not item.isdigit()
                or int(item) < 1
                for item in self.prepend_author_ids
            ):
                raise ValueError("prepend_author_ids must contain positive numeric ID strings.")
            object.__setattr__(self, "prepend_author_ids", tuple(self.prepend_author_ids))
        if not isinstance(self.variables, dict) or any(
            not isinstance(key, str) for key in self.variables
        ):
            raise ValueError("Repost variables must be a dictionary with string keys.")
        if {"id", "max_id", "prepend_media_repost_author_ids"} & self.variables.keys():
            raise ValueError("Configure identity, continuation and authors through typed inputs.")
        try:
            json.dumps(self.variables, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Repost variables must contain finite JSON values.") from None
        object.__setattr__(self, "variables", deepcopy(self.variables))
