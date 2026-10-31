"""Request options for bounded person searches and lists."""

from dataclasses import dataclass

from ..models.pagination import Continuation


def _validate_page(limit: int, cursor: Continuation | None) -> None:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer.")
    if cursor is not None and not isinstance(cursor, Continuation):
        raise ValueError("cursor must be a Continuation returned by a previous page.")


@dataclass(frozen=True, slots=True)
class SearchOptions:
    """Requested page size and continuation; server page-size caps may still apply."""

    limit: int = 20
    cursor: Continuation | None = None

    def __post_init__(self) -> None:
        _validate_page(self.limit, self.cursor)


@dataclass(frozen=True, slots=True)
class PeopleListOptions:
    """One provider-sized list page; filters require an observed request contract."""

    query: str | None = None
    cursor: Continuation | None = None

    def __post_init__(self) -> None:
        if self.cursor is not None and not isinstance(self.cursor, Continuation):
            raise ValueError("cursor must be a Continuation returned by a previous page.")
        if self.query is not None and not isinstance(self.query, str):
            raise ValueError("query must be a string or None.")


@dataclass(frozen=True, slots=True)
class MutualFollowersOptions:
    """Requested mutual-preview count and recorded profile entry context."""

    limit: int = 12
    module: str = "profile_social_context"

    def __post_init__(self) -> None:
        _validate_page(self.limit, None)
        if (
            not isinstance(self.module, str)
            or not self.module
            or any(ord(character) < 32 or ord(character) == 127 for character in self.module)
        ):
            raise ValueError("Mutual-follower module must be nonempty text without controls.")
