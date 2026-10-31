"""Config package exports."""

from .auth import AuthOptions  # Configure captured login metadata and explicit overrides.
from .options import ClientOptions  # Expose the ClientOptions contract.
from .policy import RequestPolicy  # Configure pacing, budgets, cooldowns, and account pauses.
from .profile import ClientProfile  # Expose the ClientProfile contract.

__all__ = ["ClientOptions", "ClientProfile", "AuthOptions", "RequestPolicy"]
