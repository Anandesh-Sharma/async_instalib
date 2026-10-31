"""Options for reading comments and for the account's recorded comment settings."""

from dataclasses import dataclass
from typing import Literal

from ..models.pagination import Continuation

CommentSort = Literal["default", "recent", "meta_verified"]

# Who can comment: your followers, followers you follow back, or nobody.
CommentAudience = Literal["followers", "mutual_followers", "off"]


@dataclass(frozen=True, slots=True)
class CommentListOptions:
    """One page of a post's comments in the app's sort order.

    ``default`` is the app's "For you" order. Pass a returned ``continuation`` as
    ``cursor`` to read the next page with the same sort.
    """

    sort: CommentSort = "default"
    cursor: Continuation | None = None

    def __post_init__(self) -> None:
        if self.sort not in {"default", "recent", "meta_verified"}:
            raise ValueError("sort must be default, recent, or meta_verified.")
        if self.cursor is not None and not isinstance(self.cursor, Continuation):
            raise ValueError("cursor must be a Continuation returned by a previous page.")
