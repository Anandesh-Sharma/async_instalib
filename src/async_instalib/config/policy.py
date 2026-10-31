"""Local operating budgets; these are not Instagram's published allowances."""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestPolicy:
    """Bound per-session work and require explicit recovery from account failures."""

    min_interval: float = 1.0
    max_requests: int = 60
    window_seconds: float = 60.0
    max_login_attempts: int = 3
    login_window_seconds: float = 900.0
    fallback_cooldown: float = 60.0
    pause_on_challenge: bool = True
    pause_on_restriction: bool = True
    pause_on_unsupported_login: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_interval) or self.min_interval < 0:
            raise ValueError("min_interval must be finite and nonnegative.")
        for value in (self.window_seconds, self.login_window_seconds, self.fallback_cooldown):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    "Policy windows and fallback cooldown must be finite and positive."
                )
        for value in (self.max_requests, self.max_login_attempts):
            if type(value) is not int or not 1 <= value <= 100000:
                raise ValueError("Request and login budgets must be integers from 1 to 100000.")
