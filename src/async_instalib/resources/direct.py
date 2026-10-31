"""Read the direct inbox and manage threads with the requests recorded from the app."""

import json
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import uuid4

from ..config.direct import InboxOptions
from ..exceptions import ProtocolError
from ..models.direct import Thread
from ..models.pagination import Page
from ..transport import Transport
from .direct_common import (
    Operation,
    form,
    mutation,
    new_otid,
    numeric,
    require_session,
    thread_id,
)
from .direct_items import MessageActions
from .direct_send import DirectSender

_PENDING_INBOX = Operation(
    "IGDirectGetPendingInboxQuery",
    "40079534929542046757018231971",
    "ig_direct_get_inbox",
)
_MUTE = Operation("IGDirectMuteThread", "327424623113876385440784900803", "xig_direct_mute_thread")
_PIN = Operation(
    "IGPinThreadMutation", "14536838318695625646166582129", "xdt_pin_thread", route="query"
)
# The app's mute choices; "until I change it" was recorded only for calls.
_MUTE_SECONDS = {3600, 28800, 86400}


@dataclass(frozen=True, slots=True)
class Presence:
    """Whether a person is active now, and when they were last active."""

    user_id: str
    is_active: bool | None = None
    last_active_at: float | None = None


class Direct:
    """Direct messages of the signed-in account: inbox, requests, and thread settings.

    ``send`` sends every recorded message kind; ``messages`` acts on one message.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self.send = DirectSender(transport)
        self.messages = MessageActions(transport)

    async def inbox(self, options: InboxOptions | None = None) -> Page[Thread]:
        """Read the newest threads, each with its newest messages (pinned threads first)."""
        settings = InboxOptions() if options is None else options
        if not isinstance(settings, InboxOptions):
            raise ValueError("options must be InboxOptions.")
        require_session(self._transport)
        query = {
            "include_old_mrs": "false",
            "igd_request_log_tracking_id": str(uuid4()),
            "thread_message_limit": str(settings.message_limit),
            "limit": str(settings.thread_limit),
            "fetch_reason": "manual_refresh",
        }
        result = await self._transport.request("GET", f"direct_v2/inbox/?{urlencode(query)}")
        inbox = result.get("inbox")
        if not isinstance(inbox, dict) or not isinstance(inbox.get("threads"), list):
            raise ProtocolError("Inbox response did not contain threads.")
        rows = [*(inbox.get("pinned_threads") or []), *inbox["threads"]]
        return Page(self._threads(rows), raw=result)

    async def requests(self, include_older: bool = False) -> Page[Thread]:
        """Read message requests: threads from people waiting for your approval.

        ``include_older`` also returns requests older than 30 days, like the app's
        "See all".
        """
        if type(include_older) is not bool:
            raise ValueError("include_older must be a boolean.")
        variables = {
            "use_schematized_query": False,
            "folder": "PENDING",
            "client_input_params": {
                **dict.fromkeys(
                    (
                        "sort_order",
                        "inbox_mode",
                        "thread_message_limit",
                        "thread_age_limit_days",
                        "is_inbox_ia_enabled",
                        "custom_folder_id_str",
                        "eb_device_id",
                        "cursor_timestamp_seconds_str",
                        "cursor_thread_v2_id_str",
                        "selected_filter_json",
                        "ig_inbox_folder",
                        "unseen_visual_message_limit",
                        "selected_filter",
                        "cursor_relevancy_score",
                    )
                ),
                "igd_request_log_tracking_id": str(uuid4()),
                "fetch_reason": "unclassified_pending_inbox_snapshot",
                "thread_limit": 15,
                "include_old_mrs": include_older,
                "message_request_selected_filters": [],
            },
        }
        inbox = await mutation(self._transport, _PENDING_INBOX, variables)
        try:
            edges = inbox["threads"]["edges"]
            if not isinstance(edges, list):
                raise ValueError
            # Each node carries the inbox-shaped thread as a JSON string.
            rows = [json.loads(edge["node"]["full_json_thread_dict"]) for edge in edges]
        except (KeyError, TypeError, ValueError):
            raise ProtocolError("Message requests response did not contain threads.") from None
        return Page(self._threads(rows), raw=inbox)

    async def presence(self, user_ids: list[str]) -> dict[str, Presence]:
        """Read whether these people are active now."""
        ids = [int(numeric(user, "user_id")) for user in user_ids]
        if not ids:
            raise ValueError("Ask for at least one user ID.")
        result = await form(
            self._transport,
            "direct_v2/fetch_and_subscribe_presence/",
            {"subscriptions_off": "false", "request_data": json.dumps(ids)},
        )
        rows = result.get("user_presence")
        if not isinstance(rows, dict):
            raise ProtocolError("Presence response did not contain people.")
        found = {}
        for user, row in rows.items():
            row = row if isinstance(row, dict) else {}
            active, last = row.get("is_active"), row.get("last_activity_at_ms")
            found[str(user)] = Presence(
                str(user),
                active if type(active) is bool else None,
                last / 1000 if type(last) is int else None,
            )
        return found

    async def approve(self, thread: str) -> None:
        """Accept a message request; the thread moves to your inbox."""
        await form(self._transport, f"direct_v2/threads/{thread_id(thread)}/approve/", {})

    async def hide(self, thread: str) -> None:
        """Delete a chat from your inbox, or decline a message request (for you only)."""
        await form(
            self._transport,
            f"direct_v2/threads/{thread_id(thread)}/hide/",
            {"should_move_future_requests_to_spam": "false"},
        )

    async def pin(self, thread: str) -> None:
        """Pin a chat to the top of your inbox."""
        await self._pin(thread, True)

    async def unpin(self, thread: str) -> None:
        await self._pin(thread, False)

    async def mute(self, thread: str, seconds: int = 3600) -> None:
        """Mute message notifications for 1, 8, or 24 hours (3600, 28800, or 86400)."""
        if seconds not in _MUTE_SECONDS:
            raise ValueError("seconds must be 3600, 28800, or 86400, as the app offers.")
        await self._mute(thread, {"mute_seconds": seconds, "mute": True})

    async def unmute(self, thread: str) -> None:
        await self._mute(thread, {"mute": False})

    async def mute_calls(self, thread: str) -> None:
        """Mute calls from this chat until you unmute them."""
        await form(self._transport, f"direct_v2/threads/{thread_id(thread)}/mute_video_call/", {})

    async def unmute_calls(self, thread: str) -> None:
        await form(self._transport, f"direct_v2/threads/{thread_id(thread)}/unmute_video_call/", {})

    async def _mute(self, thread: str, change: dict[str, object]) -> None:
        variables = {"offline_threading_id": new_otid(), "ig_thread_igid": thread_id(thread)}
        if await mutation(self._transport, _MUTE, {**variables, **change}) is not True:
            raise ProtocolError("Instagram did not confirm the mute change.")

    async def _pin(self, thread: str, pin: bool) -> None:
        result = await mutation(self._transport, _PIN, {"thread_id": thread_id(thread), "pin": pin})
        response = result.get("json_response") if isinstance(result, dict) else None
        if not isinstance(response, str) or json.loads(response).get("status") != "ok":
            raise ProtocolError("Instagram did not confirm the pin change.")

    @staticmethod
    def _threads(rows: list[object]) -> tuple[Thread, ...]:
        threads: dict[str, Thread] = {}
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                thread = Thread.from_dict(row)
                threads.setdefault(thread.id, thread)
        except ValueError:
            raise ProtocolError("Inbox response contained an invalid thread.") from None
        return tuple(threads.values())
