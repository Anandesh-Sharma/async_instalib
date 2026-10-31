"""Deliver events to your own HTTP endpoints, signed so the receiver can trust them.

Each delivery is a JSON POST. With a secret, ``X-Instalib-Signature`` is
``sha256=<hex HMAC of "<X-Instalib-Timestamp>.<body>">``. Webhook retries go to your
endpoint only; they never repeat an Instagram request.
"""

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .events import Event


class DeliveryError(Exception):
    """A webhook did not accept an event after every attempt."""


@dataclass(frozen=True, slots=True, repr=False)
class Webhook:
    """An endpoint, an optional signing secret, and the event types it wants (all if empty)."""

    url: str
    secret: str | None = field(default=None, repr=False)
    events: frozenset[str] = frozenset()
    timeout: float = 10.0
    attempts: int = 3

    def __post_init__(self) -> None:
        parts = urlsplit(self.url) if isinstance(self.url, str) else None
        if parts is None or parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("Webhook url must be an http or https URL.")
        if self.secret is not None and (not isinstance(self.secret, str) or not self.secret):
            raise ValueError("Webhook secret must be nonempty text when supplied.")
        object.__setattr__(self, "events", frozenset(self.events))
        if not 0 < self.timeout <= 120 or type(self.attempts) is not int or self.attempts < 1:
            raise ValueError("Use a timeout up to 120 seconds and at least one attempt.")

    def accepts(self, event: Event) -> bool:
        return not self.events or event.type in self.events

    def __repr__(self) -> str:
        return f"Webhook(url={self.url!r}, events={sorted(self.events)})"


class WebhookSender:
    """Posts events with a separate HTTP client; Instagram credentials are never sent."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(transport=transport, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, webhook: Webhook, event: Event) -> None:
        """Deliver once, retrying network failures, 429, and 5xx with a short backoff."""
        body = json.dumps(event.to_dict(), separators=(",", ":")).encode()
        for attempt in range(webhook.attempts):
            if attempt:
                await asyncio.sleep(2 ** (attempt - 1))
            try:
                response = await self._http.post(
                    webhook.url,
                    content=body,
                    headers=self._headers(webhook, event, body),
                    timeout=webhook.timeout,
                )
            except httpx.HTTPError:
                continue
            if 200 <= response.status_code < 300:
                return
            if response.status_code != 429 and response.status_code < 500:
                break
        raise DeliveryError(f"{webhook!r} did not accept event {event.id}.")

    @staticmethod
    def _headers(webhook: Webhook, event: Event, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "async_instalib-triggers",
            "X-Instalib-Event": event.type,
            "X-Instalib-Delivery": event.id,
            "X-Instalib-Timestamp": timestamp,
        }
        if webhook.secret is not None:
            signed = timestamp.encode() + b"." + body
            digest = hmac.new(webhook.secret.encode(), signed, hashlib.sha256).hexdigest()
            headers["X-Instalib-Signature"] = "sha256=" + digest
        return headers
