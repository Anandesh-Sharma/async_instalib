"""Parameters established by recorded account-owned relationship list requests."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CloseFriendsListOptions:
    """Requested member-page size; continuation has not yet been established."""

    page_size: int = 40

    def __post_init__(self) -> None:
        if type(self.page_size) is not int or self.page_size < 1:
            raise ValueError("page_size must be a positive integer.")


@dataclass(frozen=True, slots=True)
class MutedListOptions:
    """Rendering context for the recorded muted-accounts Bloks screen."""

    styles_id: str = "instagram"
    theme: str = "three_neutral_gray"
    design_system: str = "XMDS"

    def __post_init__(self) -> None:
        for value in (self.styles_id, self.theme, self.design_system):
            if (
                not isinstance(value, str)
                or not value
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(
                    "Muted-list context must be nonempty text without control characters."
                )


@dataclass(frozen=True, slots=True)
class FollowRequestsListOptions:
    """Entry context for the recorded incoming follow-requests category."""

    entry_point: str = "self_follower_follow_request_row"
    expand_top_category: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.entry_point, str)
            or not self.entry_point
            or any(ord(character) < 32 or ord(character) == 127 for character in self.entry_point)
        ):
            raise ValueError("Follow-request entry point must be nonempty text without controls.")
        if type(self.expand_top_category) is not bool:
            raise ValueError("expand_top_category must be a boolean.")
