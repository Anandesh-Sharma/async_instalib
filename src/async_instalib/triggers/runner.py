"""Poll sources, find new events, and hand each one to handlers and webhooks."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

from ..exceptions import RateLimited, RequestBudgetExceeded
from .events import Event
from .sources import ActivitySource, DirectSource, Source, TaggedSource
from .state import TriggerState
from .webhooks import Webhook, WebhookSender

if TYPE_CHECKING:
    from ..client import Instagram

Handler = Callable[[Event], Awaitable[None]]
ErrorHandler = Callable[[Event, Exception], Awaitable[None]]
_log = logging.getLogger("async_instalib.triggers")


@dataclass(frozen=True, slots=True)
class TriggerOptions:
    """How often to poll, which sources, and where to remember delivered events.

    The first poll of a source only records what already exists; set
    ``deliver_existing`` to deliver those too. Each poll costs one request per source,
    so the interval is at least 30 seconds.
    """

    sources: tuple[Source, ...] = field(
        default_factory=lambda: (ActivitySource(), TaggedSource(), DirectSource())
    )
    interval: float = 60.0
    state_file: Path | None = None
    deliver_existing: bool = False

    def __post_init__(self) -> None:
        if not self.sources or len({source.name for source in self.sources}) != len(self.sources):
            raise ValueError("Use at least one source, each with a distinct name.")
        if type(self.interval) not in {int, float} or self.interval < 30:
            raise ValueError("interval must be at least 30 seconds.")
        if self.state_file is not None:
            object.__setattr__(self, "state_file", Path(self.state_file).expanduser())


class Triggers:
    """Deliver new account events to ``@triggers.on(...)`` handlers and webhooks.

    Delivery is at least once: a crash between delivery and saving state can repeat an
    event, so receivers should drop repeated ``Event.id`` values.
    """

    def __init__(self, instagram: "Instagram", options: TriggerOptions | None = None) -> None:
        self.instagram = instagram
        self.options = TriggerOptions() if options is None else options
        self.state = TriggerState(self.options.state_file)
        self._handlers: list[tuple[frozenset[str], Handler]] = []
        self._error_handlers: list[ErrorHandler] = []
        self._webhooks: list[Webhook] = []
        self._sender = WebhookSender()

    def on(self, *types: str) -> Callable[[Handler], Handler]:
        """Register an async handler for these event types, or for all when none are given."""

        def register(handler: Handler) -> Handler:
            self._handlers.append((frozenset(types), handler))
            return handler

        return register

    def on_error(self, handler: ErrorHandler) -> ErrorHandler:
        """Register an async callback for failed handlers and webhooks."""
        self._error_handlers.append(handler)
        return handler

    def add_webhook(self, webhook: Webhook) -> None:
        if not isinstance(webhook, Webhook):
            raise ValueError("add_webhook takes a Webhook.")
        self._webhooks.append(webhook)

    async def poll(self) -> list[Event]:
        """Poll every source once and deliver what is new; returns the delivered events."""
        delivered: list[Event] = []
        for source in self.options.sources:
            events = await source.poll(self.instagram)
            fresh = self.state.unseen(source.name, events)
            if self.state.started(source.name) or self.options.deliver_existing:
                for event in fresh:
                    await self._deliver(event)
                delivered.extend(fresh)
            self.state.remember(source.name, fresh)
            self.state.save()
        return delivered

    async def run(self) -> None:
        """Poll forever at the configured interval; cancel the task to stop.

        Rate limits and local budgets wait for their reported delay. Other account
        errors (a challenge or a rejected session) stop the loop by raising.
        """
        while True:
            delay = self.options.interval
            try:
                await self.poll()
            except (RateLimited, RequestBudgetExceeded) as error:
                delay = max(delay, error.retry_after or 0.0)
                _log.warning("Polling paused for %.0f seconds: %s", delay, error)
            await asyncio.sleep(delay)

    async def aclose(self) -> None:
        """Close the webhook HTTP client; the Instagram client stays open."""
        await self._sender.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _deliver(self, event: Event) -> None:
        for types, handler in self._handlers:
            if not types or event.type in types:
                await self._attempt(handler(event), event)
        for webhook in self._webhooks:
            if webhook.accepts(event):
                await self._attempt(self._sender.send(webhook, event), event)

    async def _attempt(self, delivery: Awaitable[None], event: Event) -> None:
        try:
            await delivery
        except Exception as error:  # A failing receiver must not stop other deliveries.
            _log.error("Delivery of %s failed: %s", event.id, error)
            for handler in self._error_handlers:
                await handler(event, error)
