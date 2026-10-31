"""Explicit login transitions; no implicit retries, prompts, or challenge bypasses."""

import asyncio
import json
from enum import StrEnum
from typing import Any
from uuid import uuid4

from ..exceptions import (
    AccountRestricted,
    ChallengeRequired,
    ErrorInfo,
    LoginFlowUnsupported,
    ProtocolError,
    TwoFactorRequired,
)
from ..models import User
from ..session import TwoFactorState
from ..transport import APIResponse, Transport
from .caa import CAALogin
from .password import PasswordKey


class TwoFactorMethod(StrEnum):
    """Methods supported by the classic mobile two-factor endpoint."""

    SMS = "1"
    TOTP = "3"


class Auth:
    """Authenticate one client and retain only the resulting session or continuation."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._lock = asyncio.Lock()

    async def login(self, username: str, password: str) -> User:
        """Use the captured CAA flow with fresh server context and one credential submission."""
        if not username.strip() or not password:
            raise ValueError("Username and password must not be empty.")
        async with self._lock:
            if self._transport.session.authenticated:
                raise ValueError("This client already has a session; use account.me().")
            if self._transport.session.pending_two_factor:
                raise ValueError("Finish the pending two-factor login or use a new Session.")
            self._transport.guard.admit_login()
            await self._transport.checkpoint()
            try:
                response = await CAALogin(self._transport).login(username.strip(), password)
                result = response.json()
                response.raise_for_error(result)
            except LoginFlowUnsupported:
                if self._transport.options.policy.pause_on_unsupported_login:
                    await self._transport.pause("unsupported_login")
                raise
            except ChallengeRequired:
                if self._transport.options.policy.pause_on_challenge:
                    await self._transport.pause("challenge_required")
                raise
            except AccountRestricted:
                if self._transport.options.policy.pause_on_restriction:
                    await self._transport.pause("account_restricted")
                raise
            user = self._finish_login(response, result)
            await self._transport.checkpoint()
            return user

    async def login_legacy(self, username: str, password: str) -> User:
        """Attempt encrypted password login once. Raise actionable auth/verification errors."""
        if not username.strip() or not password:
            raise ValueError("Username and password must not be empty.")
        async with self._lock:
            session = self._transport.session
            if session.authenticated:
                raise ValueError(
                    "This client already has a session; use account.me() or a new Session."
                )
            if session.pending_two_factor:
                raise ValueError("Finish the pending two-factor login or use a new Session.")
            self._transport.guard.admit_login()
            await self._transport.checkpoint()
            password_key = await self._password_key()
            device = session.device
            body = {
                "username": username.strip(),
                "enc_password": password_key.encrypt(password),
                "guid": device.id,
                "device_id": device.android_id,
                "phone_id": device.phone_id,
                "adid": device.advertising_id,
                "jazoest": "2" + str(sum(ord(character) for character in device.phone_id)),
                "country_codes": json.dumps(
                    [{"country_code": str(session.profile.country_code), "source": ["default"]}],
                    separators=(",", ":"),
                ),
                "google_tokens": "[]",
                "login_attempt_count": "0",
            }
            csrf = session.cookie_value("csrftoken")
            if csrf:
                body["_csrftoken"] = csrf
            response = await self._transport.send("POST", "accounts/login/", self._signed(body))
            result = response.json()
            if result.get("two_factor_required"):
                info = result.get("two_factor_info") or {}
                identifier = info.get("two_factor_identifier") if isinstance(info, dict) else None
                if not isinstance(identifier, str) or not identifier:
                    raise LoginFlowUnsupported(
                        "Instagram requires a two-factor flow without a supported continuation."
                    )
                session.pending_two_factor = TwoFactorState(username.strip(), identifier)
                raise TwoFactorRequired(
                    "Call auth.verify_two_factor() with your current code.",
                    ErrorInfo(response.status_code, "two_factor_required", response.endpoint),
                )
            response.raise_for_error(result)
            return self._finish_login(response, result)

    async def verify_two_factor(
        self, code: str, method: TwoFactorMethod = TwoFactorMethod.TOTP
    ) -> User:
        """Finish a pending login. An invalid code preserves the continuation for correction."""
        if not code.strip().isdigit() or len(code.strip()) != 6:
            raise ValueError("Enter a six-digit verification code as a string.")
        async with self._lock:
            session = self._transport.session
            pending = session.pending_two_factor
            if pending is None:
                raise ValueError("No two-factor login is pending.")
            body = {
                "username": pending.username,
                "verification_code": code.strip(),
                "two_factor_identifier": pending.identifier,
                "verification_method": method.value,
                "phone_id": session.device.phone_id,
                "guid": session.device.id,
                "device_id": session.device.android_id,
                "trust_this_device": str(int(self._transport.auth_options.trust_this_device)),
                "waterfall_id": str(uuid4()),
            }
            csrf = session.cookie_value("csrftoken")
            if csrf:
                body["_csrftoken"] = csrf
            response = await self._transport.send(
                "POST", "accounts/two_factor_login/", self._signed(body)
            )
            result = response.json()
            response.raise_for_error(result)
            return self._finish_login(response, result)

    async def _password_key(self) -> PasswordKey:
        response = await self._transport.send("GET", "qe/sync/")
        # Observed bootstrap response: 405 + both key headers, with an empty body.
        # This exception is confined to this endpoint; it never makes 405 a general success.
        if response.status_code not in {200, 405}:
            response.raise_for_error(response.json())
        key_id = response.headers.get("ig-set-password-encryption-key-id")
        key = response.headers.get("ig-set-password-encryption-pub-key")
        if not key_id or not key:
            raise ProtocolError("Instagram did not supply password encryption keys.")
        try:
            return PasswordKey(int(key_id), key)
        except ValueError:
            raise ProtocolError("Instagram supplied an invalid password-key identifier.") from None

    def _finish_login(self, response: APIResponse, result: dict[str, Any]) -> User:
        value = result.get("logged_in_user")
        if not isinstance(value, dict):
            raise ProtocolError("Login response did not include an authenticated user.")
        try:
            user = User.from_dict(value)
        except ValueError:
            raise ProtocolError("Login response contained an invalid user.") from None
        session = self._transport.session
        authorization = response.headers.get("ig-set-authorization")
        if authorization and not authorization.startswith("Bearer "):
            raise ProtocolError("Login returned an unsupported authorization token.")
        if not authorization and not session.cookie_value("sessionid"):
            raise ProtocolError("Login did not issue a bearer token or session cookie.")
        session.authorization = authorization
        session.user_id = user.id
        session.pending_two_factor = None
        return user

    @staticmethod
    def _signed(data: dict[str, str]) -> dict[str, str]:
        # This marker is part of the established mobile form format, not an invented HMAC.
        return {"signed_body": "SIGNATURE." + json.dumps(data, separators=(",", ":"))}
