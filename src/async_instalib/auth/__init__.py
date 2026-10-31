"""Auth package exports."""

from .service import (
    Auth,  # Expose the Auth contract.
    TwoFactorMethod,  # Expose the TwoFactorMethod contract.
)

__all__ = ["Auth", "TwoFactorMethod"]
