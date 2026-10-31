"""Act on one direct message: react, edit, unsend, mark seen, pin."""

from ..exceptions import ProtocolError
from ..models.direct import Message
from ..transport import Transport
from .direct_common import Operation, acknowledged, form, mutation, new_otid

_EDIT = Operation(
    "IGDirectEditMessageMutation", "230942130613574727333027324333", "direct_edit_message"
)
_SEEN = Operation(
    "IGDirectItemSeenMutation", "421890028716509129940137474254", "xig_direct_item_seen"
)


def _message(value: Message) -> Message:
    if not isinstance(value, Message) or value.client_context is None:
        raise ValueError("Pass a Message from the inbox or a send; it carries its context.")
    return value


class MessageActions:
    """``instagram.direct.messages``: each method takes the Message it changes."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def react(self, message: Message, emoji: str = "❤") -> None:
        """React with one emoji; reacting again with another emoji replaces it."""
        await self._reaction(message, emoji, "created")

    async def unreact(self, message: Message, emoji: str = "❤") -> None:
        """Remove your reaction."""
        await self._reaction(message, emoji, "deleted")

    async def edit(self, message: Message, text: str) -> None:
        """Change the text of a text message you sent (Instagram allows it for 15 minutes)."""
        target = _message(message)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be nonempty.")
        variables = {
            "offline_threading_id": target.client_context,
            "ig_thread_igid": target.thread_id,
            "target_item_id": target.id,
            "body": {"sensitive_string_value": text},
        }
        if await mutation(self._transport, _EDIT, variables) is not True:
            raise ProtocolError("Instagram did not accept the edit.")

    async def unsend(self, message: Message) -> None:
        """Remove a message you sent, for everyone in the thread."""
        target = _message(message)
        await form(
            self._transport,
            f"direct_v2/threads/{target.thread_id}/items/{target.id}/delete/",
            {
                "send_attribution": "inbox",
                "original_message_client_context": target.client_context or "",
            },
        )

    async def mark_seen(self, message: Message) -> None:
        """Mark a thread as read up to this message; the sender sees "Seen"."""
        target = _message(message)
        variables = {
            "metadata": {
                "sampled": False,
                "offline_threading_id": new_otid(),
                "ig_thread_igid": target.thread_id,
            },
            "data": {
                "item_id": target.id,
                "disappearing_messages_otid": None,
                "disappearing_messages_item_id": None,
            },
        }
        if await mutation(self._transport, _SEEN, variables) is not True:
            raise ProtocolError("Instagram did not confirm the seen state.")

    async def pin(self, message: Message) -> None:
        """Pin a message to the top of its thread."""
        await self._pin(message, "pin")

    async def unpin(self, message: Message) -> None:
        await self._pin(message, "unpin")

    async def _pin(self, message: Message, action: str) -> None:
        target = _message(message)
        await form(
            self._transport,
            f"direct_v2/threads/{target.thread_id}/{action}_message/",
            {"client_context": target.client_context or "", f"{action}_message_item_id": target.id},
        )

    async def _reaction(self, message: Message, emoji: str, status: str) -> None:
        target = _message(message)
        if not isinstance(emoji, str) or not emoji or len(emoji) > 8:
            raise ValueError("emoji must be one emoji character sequence.")
        otid = new_otid()
        fields = {
            "action": "send_item",
            "item_type": "reaction",
            "reaction_type": "like",
            "reaction_status": status,
            "reaction_action_source": "emoji_tray",
            "node_type": "item",
            "emoji": emoji,
            "item_id": target.id,
            "original_message_client_context": target.client_context or "",
            "thread_ids": f"[{target.thread_id}]",
            "send_attribution": "inbox",
            "client_context": otid,
            "mutation_token": otid,
            "offline_threading_id": otid,
            "device_id": self._transport.session.device.android_id,
        }
        if status == "created":
            fields["super_react_type"] = "none"
        result = await form(self._transport, "direct_v2/threads/broadcast/reaction/", fields)
        acknowledged(result, "reaction")
