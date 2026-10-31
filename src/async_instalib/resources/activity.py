"""Read the activity feed with the app's recorded request, without marking it seen."""

from urllib.parse import urlencode

from ..exceptions import LoginRequired, ProtocolError
from ..models.activity import Notification
from ..models.pagination import Page
from ..transport import Transport

# The app splits one response into these sections; all are notifications.
_SECTIONS = ("new_stories", "priority_stories", "old_stories", "friend_request_stories")


class Activity:
    """Notifications for the signed-in account: comments, mentions, likes, follows."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def notifications(self) -> Page[Notification]:
        """Read the newest page of notifications, newest first, without marking them seen.

        Sections are merged and duplicates (the same row in two sections) appear once.
        Older pages are not read; poll often enough that new rows are on the first page.
        """
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Load your own authenticated session before reading activity.")
        query = {
            "could_truncate_feed": "true",
            "should_skip_su": "true",
            "mark_as_seen": "false",
            "timezone_offset": str(session.profile.timezone_offset),
            "push_disabled": "true",
        }
        result = await self._transport.request("GET", f"news/inbox/?{urlencode(query)}")
        rows: dict[str, Notification] = {}
        try:
            for section in _SECTIONS:
                for item in result.get(section) or []:
                    notification = Notification.from_dict(item)
                    rows.setdefault(notification.id, notification)
        except ValueError:
            raise ProtocolError("Activity response contained an invalid notification.") from None
        ordered = sorted(rows.values(), key=lambda row: row.occurred_at or 0.0, reverse=True)
        return Page(tuple(ordered), raw=result)
