"""Per-session admission control, independent of HTTP serialization."""

import asyncio
import time

from ..config import RequestPolicy
from ..exceptions import AccountPaused, ErrorInfo, RateLimited, RequestBudgetExceeded
from ..session import Session


class RequestGuard:
    """Serialize admissions and account for every attempted network dispatch."""

    def __init__(self, session: Session, policy: RequestPolicy) -> None:
        self.session = session
        self.policy = policy
        self._lock = asyncio.Lock()

    def check(self) -> None:
        if self.session.paused_reason:
            raise AccountPaused("Account requests are paused; resolve the issue before resuming.")
        until = self.session.rate_limit_until
        if until is not None and until > time.time():
            raise RateLimited(ErrorInfo(error_type="local_cooldown"), until - time.time())

    async def admit(self) -> None:
        async with self._lock:
            self.check()
            if self.session.last_request_at is not None:
                delay = self.session.last_request_at + self.policy.min_interval - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            self.check()
            now = time.time()
            self.session.request_times = [
                t for t in self.session.request_times if t > now - self.policy.window_seconds
            ]
            if len(self.session.request_times) >= self.policy.max_requests:
                raise RequestBudgetExceeded(
                    "request", self.session.request_times[0] + self.policy.window_seconds - now
                )
            self.session.request_times.append(now)
            self.session.last_request_at = now

    def admit_login(self) -> None:
        self.check()
        now = time.time()
        self.session.login_attempt_times = [
            t
            for t in self.session.login_attempt_times
            if t > now - self.policy.login_window_seconds
        ]
        if len(self.session.login_attempt_times) >= self.policy.max_login_attempts:
            raise RequestBudgetExceeded(
                "login",
                self.session.login_attempt_times[0] + self.policy.login_window_seconds - now,
            )
        self.session.login_attempt_times.append(now)
