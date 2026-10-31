"""Event sources: each reads one Instagram feed and turns its rows into Events.

A source is any object with a ``name`` and ``async poll(instagram) -> list[Event]``.
Add one for a new feed without changing delivery.
"""

from typing import TYPE_CHECKING, Protocol

from ..config.direct import InboxOptions
from ..models.activity import Notification
from ..models.direct import Message, Thread
from .events import (
    COMMENT,
    FOLLOW,
    LIKE,
    MENTION,
    MESSAGE,
    MESSAGE_REACTION,
    MESSAGE_REQUEST,
    NOTIFICATION,
    TAG,
    Actor,
    Event,
)

if TYPE_CHECKING:
    from ..client import Instagram

# Recorded notif_name values; any other row is delivered as a plain "notification".
_ACTIVITY_TYPES = {
    "comment": COMMENT,
    "mentioned_comment": MENTION,
    "post_like": LIKE,
    "user_followed": FOLLOW,
}


class Source(Protocol):
    """Reads one feed of the signed-in account; one or two requests per poll."""

    name: str

    async def poll(self, instagram: "Instagram") -> list[Event]: ...


class ActivitySource:
    """Comments, mentions, likes, follows, and other rows of the activity feed."""

    name = "activity"

    async def poll(self, instagram: "Instagram") -> list[Event]:
        page = await instagram.activity.notifications()
        account = instagram.session.user_id or ""
        return [self._event(row, account) for row in page.items]

    def _event(self, row: Notification, account: str) -> Event:
        return Event(
            id=f"{self.name}:{row.id}",
            type=_ACTIVITY_TYPES.get(row.name, NOTIFICATION),
            source=self.name,
            account_id=account,
            occurred_at=row.occurred_at,
            actor=Actor(row.actor_id, row.actor_username) if row.actor_id else None,
            media_id=row.media_ids[0] if row.media_ids else None,
            comment_id=row.comment_id,
            text=row.text,
            data={"name": row.name, **row.raw},
        )


class TaggedSource:
    """Posts and reels in which someone tagged the signed-in account."""

    name = "tagged"

    async def poll(self, instagram: "Instagram") -> list[Event]:
        account = instagram.session.user_id or ""
        page = await instagram.people.content.tagged(account)
        events = []
        for media in page.items:
            user = media.raw.get("user")
            owner = user if isinstance(user, dict) else {}
            owner_id = owner.get("pk") or owner.get("id")
            username = owner.get("username")
            events.append(
                Event(
                    id=f"{self.name}:{media.id}",
                    type=TAG,
                    source=self.name,
                    account_id=account,
                    occurred_at=float(media.taken_at) if media.taken_at is not None else None,
                    actor=(
                        Actor(str(owner_id), username if isinstance(username, str) else None)
                        if owner_id
                        else None
                    ),
                    media_id=media.id,
                    text=media.caption or "",
                    data=media.raw,
                )
            )
        return events


class DirectSource:
    """New direct messages, reactions to messages, and message requests.

    Each poll reads the inbox (newest ``thread_limit`` threads, ``message_limit``
    messages each) and message requests: two requests. Messages beyond those limits
    between two polls are not seen, so raise the limits for busy inboxes.
    """

    name = "direct"

    def __init__(self, options: InboxOptions | None = None) -> None:
        self.options = InboxOptions(message_limit=10) if options is None else options

    async def poll(self, instagram: "Instagram") -> list[Event]:
        account = instagram.session.user_id or ""
        events: list[Event] = []
        for thread in (await instagram.direct.inbox(self.options)).items:
            for message in thread.messages:
                if not message.is_sent_by_viewer and message.type != "action_log":
                    events.append(self._message(thread, message, account))
                events.extend(self._reactions(thread, message, account))
        for thread in (await instagram.direct.requests()).items:
            newest = next((m for m in thread.messages if not m.is_sent_by_viewer), None)
            if newest is not None:
                request = self._message(thread, newest, account)
                events.append(
                    Event(
                        id=f"{self.name}_request:{thread.id}:{newest.id}",
                        type=MESSAGE_REQUEST,
                        source=self.name,
                        account_id=account,
                        occurred_at=newest.sent_at,
                        actor=request.actor,
                        text=newest.text or "",
                        data=request.data,
                    )
                )
        return events

    def _message(self, thread: Thread, message: Message, account: str) -> Event:
        return Event(
            id=f"{self.name}:{message.id}",
            type=MESSAGE,
            source=self.name,
            account_id=account,
            occurred_at=message.sent_at,
            actor=self._actor(thread, message.user_id),
            text=message.text or "",
            data={"thread_id": thread.id, "message_type": message.type, "message": message.raw},
        )

    def _reactions(self, thread: Thread, message: Message, account: str) -> list[Event]:
        reactions = message.raw.get("reactions")
        rows = reactions.get("emojis") if isinstance(reactions, dict) else None
        events = []
        for row in rows if isinstance(rows, list) else []:
            sender = str(row.get("sender_id")) if isinstance(row, dict) else ""
            if not sender.isdigit() or sender == account:
                continue
            stamp, emoji = row.get("timestamp"), str(row.get("emoji") or "")
            # Changing a reaction keeps its client context, so the emoji is part of the ID,
            # written as code points because IDs travel in an HTTP header.
            code = "-".join(f"{ord(character):x}" for character in emoji)
            reaction = f"{message.id}:{sender}:{row.get('client_context')}:{code}"
            events.append(
                Event(
                    id=f"{self.name}_reaction:{reaction}",
                    type=MESSAGE_REACTION,
                    source=self.name,
                    account_id=account,
                    occurred_at=stamp / 1_000_000 if type(stamp) is int else None,
                    actor=self._actor(thread, sender),
                    text=emoji,
                    data={"thread_id": thread.id, "message_id": message.id, "reaction": row},
                )
            )
        return events

    @staticmethod
    def _actor(thread: Thread, user_id: str | None) -> Actor | None:
        if user_id is None:
            return None
        username = next((u.username for u in thread.users if u.id == user_id), None)
        return Actor(user_id, username)
