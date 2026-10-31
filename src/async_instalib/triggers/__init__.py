"""Triggers: deliver new account events to handlers and webhooks."""

from .events import (
    COMMENT,  # Someone commented on your post.
    FOLLOW,  # Someone started following you.
    LIKE,  # Someone liked your post.
    MENTION,  # Someone mentioned you in a comment or caption.
    MESSAGE,  # Someone sent you a direct message.
    MESSAGE_REACTION,  # Someone reacted to a direct message.
    MESSAGE_REQUEST,  # Someone sent you a message request.
    NOTIFICATION,  # Any other activity-feed row; Instagram's own type is in data["name"].
    TAG,  # Someone tagged you in a post or reel.
    Actor,  # The person who caused an event.
    Event,  # One event shape for every source.
)
from .runner import (
    TriggerOptions,  # Sources, poll interval, state file, and first-poll behavior.
    Triggers,  # Poll sources and deliver new events.
)
from .sources import (
    ActivitySource,  # Comments, mentions, likes, and follows from the activity feed.
    DirectSource,  # Direct messages, reactions, and message requests.
    Source,  # The protocol a new event source implements.
    TaggedSource,  # Posts and reels you are tagged in.
)
from .state import TriggerState  # Seen event IDs per source, saved privately.
from .webhooks import (
    DeliveryError,  # A webhook refused an event after every attempt.
    Webhook,  # An endpoint, signing secret, and event filter.
    WebhookSender,  # Signs and posts events with its own HTTP client.
)

__all__ = [
    "COMMENT",
    "FOLLOW",
    "LIKE",
    "MENTION",
    "MESSAGE",
    "MESSAGE_REACTION",
    "MESSAGE_REQUEST",
    "NOTIFICATION",
    "TAG",
    "Actor",
    "ActivitySource",
    "DeliveryError",
    "DirectSource",
    "Event",
    "Source",
    "TaggedSource",
    "TriggerOptions",
    "TriggerState",
    "Triggers",
    "Webhook",
    "WebhookSender",
]
