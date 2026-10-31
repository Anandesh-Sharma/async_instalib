"""Read-only operations for the authenticated account."""

from ..exceptions import LoginRequired, ProtocolError
from ..models import User
from ..transport import Transport


class Account:
    """Account operations share the client's connection pool and session state."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def me(self, *, edit: bool = True) -> User:
        """Validate the session with Instagram and return the currently authenticated account."""
        session = self._transport.session
        if not session.authenticated:
            raise LoginRequired("Log in or load your own saved session before reading the account.")
        result = await self._transport.request(
            "GET", f"accounts/current_user/?edit={str(edit).lower()}"
        )
        value = result.get("user")
        if not isinstance(value, dict):
            raise ProtocolError("Current-account response did not contain a user.")
        try:
            user = User.from_dict(value)
        except ValueError:
            raise ProtocolError("Current-account response contained an invalid user.") from None
        if user.id != session.user_id:
            raise ProtocolError("Instagram's account identity does not match the loaded session.")
        return user
