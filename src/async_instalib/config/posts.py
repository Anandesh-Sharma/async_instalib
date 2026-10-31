"""Options for publishing and editing your own posts, reels, stories, and highlights.

Every field maps to a value recorded from the Android app; there are no free-form
provider parameters, because an unrecorded field could publish something unintended.
"""

from dataclasses import dataclass

from ..models.posts import Place
from ..models.uploads import Photo, Video


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"{name} must be text without control characters.")


def _flags(*values: object) -> None:
    if any(type(value) is not bool for value in values):
        raise ValueError("Post switches must be booleans.")


def _place(value: object) -> None:
    if value is not None and not isinstance(value, Place):
        raise ValueError("location must be a Place from posts.places.search().")


def _user_id(value: object) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not int(value):
        raise ValueError("User IDs must be positive numeric ASCII strings.")
    return str(int(value))


@dataclass(frozen=True, slots=True)
class UserTag:
    """Tag one person on a photo; x and y are fractions of the width and height."""

    user_id: str
    x: float = 0.5
    y: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _user_id(self.user_id))
        for value in (self.x, self.y):
            if type(value) not in {int, float} or not 0 <= value <= 1:
                raise ValueError("Tag positions must be numbers from 0 to 1.")


def _tags(value: object) -> tuple[UserTag, ...]:
    if not isinstance(value, (tuple, list)) or any(not isinstance(t, UserTag) for t in value):
        raise ValueError("user_tags must be a sequence of UserTag.")
    if len({tag.user_id for tag in value}) != len(value):
        raise ValueError("Tag each person at most once per item.")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class CarouselItem:
    """One carousel item. Photos carry their own tags and alt text, as the app sends them;
    videos need a first-frame ``cover`` because the app uploads one for each.
    """

    media: Photo | Video
    user_tags: tuple[UserTag, ...] = ()
    alt_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.media, (Photo, Video)):
            raise ValueError("Carousel items must be a Photo or Video.")
        object.__setattr__(self, "user_tags", _tags(self.user_tags))
        if self.alt_text is not None:
            _text(self.alt_text, "alt_text")
        if isinstance(self.media, Video) and (self.user_tags or self.alt_text is not None):
            raise ValueError("Tags and alt text were recorded for carousel photos only.")
        if isinstance(self.media, Video) and self.media.cover is None:
            raise ValueError("Carousel videos need a first-frame cover: Video(..., cover=Photo).")


@dataclass(frozen=True, slots=True)
class PostOptions:
    """A feed photo post or carousel.

    ``user_tags`` and ``alt_text`` apply to a single photo. In a carousel, set them per
    photo with ``CarouselItem``; the app sends them on each item, not on the album.
    """

    caption: str = ""
    location: Place | None = None
    user_tags: tuple[UserTag, ...] = ()
    alt_text: str | None = None
    comments_disabled: bool = False
    hide_like_count: bool = False
    hide_share_count: bool = False

    def __post_init__(self) -> None:
        _text(self.caption, "caption")
        _place(self.location)
        object.__setattr__(self, "user_tags", _tags(self.user_tags))
        if self.alt_text is not None:
            _text(self.alt_text, "alt_text")
        _flags(self.comments_disabled, self.hide_like_count, self.hide_share_count)


@dataclass(frozen=True, slots=True)
class ReelOptions:
    """A reel; ``share_to_feed`` also shows it in the profile's main grid.

    Reels tag people without a position, so tags are plain user IDs.
    """

    caption: str = ""
    location: Place | None = None
    tagged_user_ids: tuple[str, ...] = ()
    comments_disabled: bool = False
    hide_like_count: bool = False
    hide_share_count: bool = False
    share_to_feed: bool = True

    def __post_init__(self) -> None:
        _text(self.caption, "caption")
        _place(self.location)
        if not isinstance(self.tagged_user_ids, (tuple, list)):
            raise ValueError("tagged_user_ids must be a sequence of user IDs.")
        ids = tuple(_user_id(item) for item in self.tagged_user_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("Tag each person at most once.")
        object.__setattr__(self, "tagged_user_ids", ids)
        _flags(
            self.comments_disabled, self.hide_like_count, self.hide_share_count, self.share_to_feed
        )


@dataclass(frozen=True, slots=True)
class StoryOptions:
    """A story; ``close_friends`` limits it to your Close Friends list."""

    caption: str | None = None
    close_friends: bool = False

    def __post_init__(self) -> None:
        if self.caption is not None:
            _text(self.caption, "caption")
        _flags(self.close_friends)


@dataclass(frozen=True, slots=True)
class EditOptions:
    """New post details. The caption always replaces the current caption.

    Unset ``alt_text`` and ``location`` leave those details unchanged. ``user_tags``
    adds people to a photo; removing existing tags has not been recorded.
    """

    caption: str
    alt_text: str | None = None
    location: Place | None = None
    user_tags: tuple[UserTag, ...] = ()

    def __post_init__(self) -> None:
        _text(self.caption, "caption")
        if self.alt_text is not None:
            _text(self.alt_text, "alt_text")
        _place(self.location)
        object.__setattr__(self, "user_tags", _tags(self.user_tags))


@dataclass(frozen=True, slots=True)
class HighlightChanges:
    """Changes to one highlight: a new title, and stories to add or remove."""

    title: str | None = None
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.title is not None:
            _text(self.title, "title")
            if not self.title.strip():
                raise ValueError("Highlight titles cannot be empty.")
        for name in ("add", "remove"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)) or any(not isinstance(i, str) for i in value):
                raise ValueError(f"{name} must be a sequence of story media IDs.")
            object.__setattr__(self, name, tuple(value))
