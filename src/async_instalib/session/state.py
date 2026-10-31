"""Device identity and private session persistence, independent of network I/O."""

import json
import os
import secrets
import tempfile
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from http.cookiejar import Cookie
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import ClientProfile

STATE_HEADERS = frozenset(
    {
        "ig-u-rur",
        "ig-u-shbid",
        "ig-u-shbts",
        "ig-u-ig-direct-region-hint",
        "x-ig-www-claim",
    }
)


@dataclass(frozen=True, slots=True)
class Device:
    """Persistent client-generated identifiers; save them with the session."""

    id: str = field(default_factory=lambda: str(uuid4()))
    android_id: str = field(default_factory=lambda: "android-" + secrets.token_hex(8))
    family_id: str = field(default_factory=lambda: str(uuid4()))
    phone_id: str = field(default_factory=lambda: str(uuid4()))
    advertising_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(slots=True, repr=False)
class SessionCookie:
    """Preserve domain/path/expiry instead of flattening distinct cookies by name."""

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = True
    expires: int | None = None
    host_only: bool = False

    def to_cookie(self) -> Cookie:
        return Cookie(
            version=0,
            name=self.name,
            value=self.value,
            port=None,
            port_specified=False,
            domain=self.domain,
            domain_specified=not self.host_only,
            domain_initial_dot=self.domain.startswith("."),
            path=self.path,
            path_specified=True,
            secure=self.secure,
            expires=self.expires,
            discard=self.expires is None,
            comment=None,
            comment_url=None,
            rest={},
        )


@dataclass(slots=True, repr=False)
class TwoFactorState:
    """A pending login continuation; never includes the account password."""

    username: str
    identifier: str


@dataclass(slots=True, repr=False)
class Session:
    """One account's private state. A Session belongs to one active client at a time."""

    profile: ClientProfile = field(default_factory=ClientProfile)
    device: Device = field(default_factory=Device)
    authorization: str | None = None
    user_id: str | None = None
    mid: str | None = None
    state_headers: dict[str, str] = field(default_factory=dict)
    cookies: list[SessionCookie] = field(default_factory=list)
    pending_two_factor: TwoFactorState | None = None
    rate_limit_until: float | None = None
    request_times: list[float] = field(default_factory=list, repr=False)
    login_attempt_times: list[float] = field(default_factory=list, repr=False)
    last_request_at: float | None = None
    paused_reason: str | None = None

    def __repr__(self) -> str:
        return f"Session(authenticated={self.authenticated})"

    @property
    def authenticated(self) -> bool:
        return bool(self.user_id and (self.authorization or self.cookie_value("sessionid")))

    def cookie_value(self, name: str) -> str | None:
        return next(
            (
                c.value
                for c in reversed(self.cookies)
                if c.name == name
                and (c.expires is None or c.expires > time.time())
                and c.domain.lstrip(".") in {"instagram.com", "i.instagram.com"}
            ),
            None,
        )

    def clear_authentication(self) -> None:
        self.authorization = None
        self.user_id = None
        self.state_headers.clear()
        self.pending_two_factor = None
        self.cookies = [c for c in self.cookies if c.name not in {"sessionid", "ds_user_id"}]

    def save(self, path: str | Path) -> None:
        """Atomically store credentials with mode 0600; keep this file outside Git."""
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"schema_version": 1, **asdict(self)}
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, delete=False
            ) as stream:
                temporary = stream.name
                os.fchmod(stream.fileno(), 0o600)
                json.dump(payload, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        """Restore credentials and device identity; this does not verify server validity."""
        try:
            data: dict[str, Any] = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid session file; expected an object.")
            if data.pop("schema_version") != 1:
                raise ValueError("Unsupported session schema version.")
            data["profile"] = ClientProfile(**data["profile"])
            data["device"] = Device(**data["device"])
            data["cookies"] = [SessionCookie(**cookie) for cookie in data["cookies"]]
            if data.get("pending_two_factor"):
                data["pending_two_factor"] = TwoFactorState(**data["pending_two_factor"])
            session = cls(**data)
            if any(k not in STATE_HEADERS for k in session.state_headers):
                raise ValueError("Unexpected session state header.")
            return session
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Invalid session file; credentials were not loaded.") from None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "Session":
        """Import your own captured Instagram request; copy only recognized state fields."""
        values = {key.lower(): value for key, value in headers.items()}
        defaults = ClientProfile()
        profile = ClientProfile(
            user_agent=values.get("user-agent", defaults.user_agent),
            app_id=values.get("x-ig-app-id", defaults.app_id),
            bloks_version=values.get("x-bloks-version-id", defaults.bloks_version),
            capabilities=values.get("x-ig-capabilities", defaults.capabilities),
            locale=values.get("x-ig-app-locale", defaults.locale),
            timezone_offset=int(values.get("x-ig-timezone-offset", "0")),
        )
        device = Device()
        device = Device(
            id=values.get("x-ig-device-id", device.id),
            android_id=values.get("x-ig-android-id", device.android_id),
            family_id=values.get("x-ig-family-device-id", device.family_id),
            phone_id=device.phone_id,
            advertising_id=device.advertising_id,
        )
        session = cls(
            profile=profile,
            device=device,
            authorization=values.get("authorization"),
            user_id=values.get("ig-u-ds-user-id") or values.get("ig-intended-user-id"),
            mid=values.get("x-mid"),
            state_headers={k: values[k] for k in STATE_HEADERS if values.get(k)},
        )
        parsed: SimpleCookie = SimpleCookie()
        parsed.load(values.get("cookie", ""))
        session.cookies = [SessionCookie(k, v.value, ".instagram.com") for k, v in parsed.items()]
        if not session.user_id or session.user_id == "0":
            session.user_id = session.cookie_value("ds_user_id")
        return session
