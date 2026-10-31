"""Actionable errors that do not include credentials or raw provider responses."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Non-secret diagnostic context for a failed HTTP exchange."""

    status_code: int | None = None
    error_type: str | None = None
    endpoint: str | None = None


class InstagramError(Exception):
    """Base error for all expected SDK failures."""

    def __init__(self, message: str, info: ErrorInfo | None = None) -> None:
        super().__init__(message)
        self.info = info or ErrorInfo()


class NetworkError(InstagramError):
    """The exchange failed; a request may have reached the server and is not retried."""


class ProtocolError(InstagramError):
    """Instagram returned a response outside the supported protocol."""


class LoginRequired(InstagramError):
    """There is no usable authenticated session, or Instagram rejected it."""


class BadCredentials(InstagramError):
    """Instagram rejected the supplied credentials."""


class LoginFlowUnsupported(InstagramError):
    """Instagram requires a login flow this client has not established."""


class TwoFactorRequired(InstagramError):
    """Continue through client.auth.verify_two_factor; never retry the password blindly."""


class ChallengeRequired(InstagramError):
    """Instagram requires account-owner verification outside normal password login."""

    def __init__(self, info: ErrorInfo, challenge_url: str | None = None) -> None:
        super().__init__("Complete Instagram's account verification before continuing.", info)
        self.challenge_url = challenge_url


class RateLimited(InstagramError):
    """The caller must wait; the library never retries authentication automatically."""

    def __init__(self, info: ErrorInfo, retry_after: float | None = None) -> None:
        super().__init__("Instagram rate-limited the request; wait before trying again.", info)
        self.retry_after = retry_after


class AccountRestricted(InstagramError):
    """Instagram has restricted the operation or account."""


class APIError(InstagramError):
    """An unclassified HTTP or application-level failure."""


class PersonNotFound(InstagramError):
    """Instagram returned HTTP 404 for the requested username's accessible profile."""


class AccountPaused(InstagramError):
    """A prior account failure requires explicit local recovery before more requests."""


class RequestBudgetExceeded(InstagramError):
    """A local request or login budget was exhausted; no request was sent."""

    def __init__(self, budget: str, retry_after: float) -> None:
        super().__init__(f"The local {budget} budget is exhausted; no request was sent.")
        self.budget = budget
        self.retry_after = retry_after


class SessionInUse(InstagramError):
    """Another client owns this session file; create one client per account."""
