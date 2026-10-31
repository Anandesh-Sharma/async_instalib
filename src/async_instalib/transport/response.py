"""HTTP ownership, dynamic headers, response state, and provider error translation."""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..exceptions import (
    AccountRestricted,
    APIError,
    BadCredentials,
    ChallengeRequired,
    ErrorInfo,
    LoginFlowUnsupported,
    LoginRequired,
    ProtocolError,
    RateLimited,
    TwoFactorRequired,
)
from .graphql import GraphQLDecoder, JSONStreamDecoder


@dataclass(frozen=True, slots=True, repr=False)
class RequestData:
    """A form or raw body plus per-exchange headers (CAA attestation, media uploads)."""

    form: Mapping[str, str]
    headers: Mapping[str, str]
    content: bytes | None = None


@dataclass(slots=True, repr=False)
class APIResponse:
    """Internal exchange result, never implicitly printed with account data."""

    status_code: int
    headers: httpx.Headers
    endpoint: str
    content: bytes = field(repr=False)
    _graphql: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _stream: tuple[dict[str, Any], ...] | None = field(default=None, init=False, repr=False)

    def json_stream(self, max_bytes: int) -> tuple[dict[str, Any], ...]:
        """Decode bounded concatenated JSON once, without discarding later records."""
        if self._stream is None:
            self._stream = JSONStreamDecoder(max_bytes).decode(self.content)
        return self._stream

    def graphql(self, max_bytes: int) -> dict[str, Any]:
        """Decode the bounded stream once; retain the full merged GraphQL envelope."""
        if self._graphql is None:
            self._graphql = GraphQLDecoder(max_bytes).decode(self.content)
        return self._graphql

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.content)
        except (ValueError, UnicodeError):
            if self.status_code >= 400:
                return {}
            raise ProtocolError("Expected a JSON object from Instagram.") from None
        if not isinstance(value, dict):
            raise ProtocolError("Expected a JSON object from Instagram.")
        return value

    def raise_for_error(self, data: dict[str, Any]) -> None:
        category = str(data.get("error_type") or data.get("message") or "unknown")
        if (
            200 <= self.status_code < 300
            and data.get("status") != "fail"
            and not data.get("two_factor_required")
            and not data.get("challenge")
            and category
            not in {
                "challenge_required",
                "checkpoint_required",
                "login_required",
                "feedback_required",
                "sentry_block",
                "rate_limit_error",
            }
        ):
            return
        recognized = {
            "bad_password",
            "invalid_user",
            "login_required",
            "challenge_required",
            "checkpoint_required",
            "two_factor_required",
            "needs_upgrade",
            "feedback_required",
            "sentry_block",
            "rate_limit_error",
            "invalid_code",
            "invalid_parameters",
        }
        info = ErrorInfo(
            self.status_code, category if category in recognized else "unknown", self.endpoint
        )
        if self.status_code == 429 or category == "rate_limit_error":
            raise RateLimited(info, self._retry_after())
        if category in {"challenge_required", "checkpoint_required"} or data.get("challenge"):
            challenge = data.get("challenge") or {}
            url = challenge.get("url") if isinstance(challenge, dict) else None
            if (
                not isinstance(url, str)
                or urlsplit(url).scheme != "https"
                or not (
                    (urlsplit(url).hostname or "").endswith(".instagram.com")
                    or urlsplit(url).hostname == "instagram.com"
                )
            ):
                url = None
            raise ChallengeRequired(info, url)
        if data.get("two_factor_required") or category == "two_factor_required":
            raise TwoFactorRequired("Instagram requires a two-factor verification code.", info)
        if category == "needs_upgrade":
            raise LoginFlowUnsupported("Instagram requires a newer authentication flow.", info)
        if category in {"bad_password", "invalid_user"}:
            raise BadCredentials("Instagram rejected the username or password.", info)
        if category == "login_required" or self.status_code == 401:
            raise LoginRequired("Instagram no longer accepts this session.", info)
        if category in {"feedback_required", "sentry_block"}:
            raise AccountRestricted("Instagram restricted this operation.", info)
        raise APIError("Instagram rejected the request.", info)

    def _retry_after(self) -> float | None:
        value = self.headers.get("retry-after")
        if not value:
            return None
        try:
            seconds = float(value)
            return max(0.0, seconds) if math.isfinite(seconds) else None
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                # Malformed provider hints must not obscure the rate-limit error.
                return None
