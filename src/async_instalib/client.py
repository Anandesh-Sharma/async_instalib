"""Public async client and resource ownership."""

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Self

import httpx

from .auth import Auth
from .config import ClientOptions
from .resources import Account
from .resources.activity import Activity
from .resources.comments import Comments
from .resources.direct import Direct
from .resources.people import People
from .resources.posts import Posts
from .session import Session
from .session.lease import SessionLease
from .transport import Transport


class Instagram:
    """One account, one connection pool; use as an async context manager."""

    def __init__(
        self,
        session: Session | None = None,
        options: ClientOptions | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.session = session if session is not None else Session()
        self._transport = Transport(self.session, options or ClientOptions(), transport)
        self.auth = Auth(self._transport)
        self.account = Account(self._transport)
        self.activity = Activity(self._transport)
        self.people = People(self._transport)
        self.posts = Posts(self._transport)
        self.comments = Comments(self._transport)
        self.direct = Direct(self._transport)
        self._session_file: Path | None = None
        self._saved_on_close = False
        self._close_lock = asyncio.Lock()
        self._lease: SessionLease | None = None
        self._closed = False

    @classmethod
    def from_session_file(
        cls,
        path: str | Path,
        options: ClientOptions | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> Self:
        """Load or create a session; save updated state when the client closes.

        Missing files create a fresh identity, never an implicit password login.
        Invalid existing files raise instead of silently replacing credentials.
        Use one active client per file.
        """
        destination = Path(path).expanduser().resolve()
        lease = SessionLease(destination)
        try:
            session = Session.load(destination) if destination.exists() else Session()
            if not destination.exists():
                session.save(destination)
            client = cls(session, options, transport)
            client._session_file = destination
            client._transport.session_file = destination
            client._lease = lease
            return client
        except BaseException:
            lease.close()
            raise

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release connections and save a bound session file, without logging out."""
        async with self._close_lock:
            if self._closed:
                return
            try:
                await self._transport.aclose()
            finally:
                if self._session_file is not None and not self._saved_on_close:
                    try:
                        await self._transport.checkpoint()
                        self._saved_on_close = True
                    finally:
                        if self._lease is not None:
                            self._lease.close()
                        self._closed = True
                else:
                    self._closed = True

    async def resume_after_review(self) -> None:
        """Clear a local pause after you resolve verification in Instagram.

        This makes no network request and does not reset rate limits or budgets.
        It does not claim that Instagram has cleared the underlying restriction.
        """
        if self._closed:
            raise RuntimeError("Instagram client is closed; reopen it before resuming.")
        self.session.paused_reason = None
        await self._transport.checkpoint()
