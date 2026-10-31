"""Bounded result pages and continuations scoped to their originating request."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def _optional_text(value: object, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{name} must be a nonempty string when supplied.")


@dataclass(frozen=True, slots=True)
class PageContext:
    """Identity of a paginated operation; query comparison is exact."""

    operation: str
    account_id: str
    target: str | None = None
    query: str | None = None

    def __post_init__(self) -> None:
        for name in ("operation", "account_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string.")
        _optional_text(self.target, "target")
        if self.query is not None and not isinstance(self.query, str):
            raise ValueError("query must be a string or None.")


@dataclass(frozen=True, slots=True)
class Continuation:
    """Opaque provider cursor and request context; never an executable next URL."""

    value: str = field(repr=False)
    context: PageContext
    rank_token: str | None = field(default=None, repr=False)
    search_session_id: str | None = field(default=None, repr=False)
    serp_session_id: str | None = field(default=None, repr=False)
    page_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("Cursor value must be a nonempty string.")
        if not isinstance(self.context, PageContext):
            raise ValueError("Cursor requires a PageContext.")
        _optional_text(self.rank_token, "rank_token")
        _optional_text(self.search_session_id, "search_session_id")
        _optional_text(self.serp_session_id, "serp_session_id")
        if self.page_index is not None and (
            type(self.page_index) is not int or self.page_index < 1
        ):
            raise ValueError("page_index must be a positive integer when supplied.")

    def validate_for(self, context: PageContext) -> None:
        """Reject continuation reuse across queries, people, operations, or accounts."""
        if self.context != context:
            raise ValueError("Cursor belongs to a different operation, account, target, or query.")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One response page; reading it never fetches another page implicitly."""

    items: tuple[T, ...]
    continuation: Continuation | None = None
    total_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.items, (list, tuple)):
            raise ValueError("Page items must be a list or tuple.")
        if self.continuation is not None and not isinstance(self.continuation, Continuation):
            raise ValueError("Page continuation must be a Continuation or None.")
        if self.total_count is not None and (
            type(self.total_count) is not int or self.total_count < 0
        ):
            raise ValueError("Page total_count must be a nonnegative integer or None.")
        if not isinstance(self.raw, dict):
            raise ValueError("Page raw payload must be a dictionary.")
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "raw", deepcopy(self.raw))

    @property
    def has_more(self) -> bool:
        """Whether the response provided a usable continuation."""
        return self.continuation is not None
