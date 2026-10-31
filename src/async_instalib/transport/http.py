"""HTTP ownership, dynamic headers, response state, and provider error translation."""

import asyncio
import copy
import json
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx

from ..config import ClientOptions
from ..exceptions import (
    ErrorInfo,
    LoginRequired,
    NetworkError,
    ProtocolError,
    RateLimited,
)
from ..session import STATE_HEADERS, Session, SessionCookie
from .graphql import GraphQLRequest, GraphQLRoute, error_payloads
from .guard import RequestGuard
from .response import APIResponse, RequestData
from .upload import DIRECT_UPLOAD_ORIGIN, MediaUpload


@dataclass(frozen=True, slots=True)
class _JSONStreamEndpoint:
    """Select REST stream decoding while retaining relative endpoint validation."""

    path: str


class Transport:
    """Own one pooled AsyncClient and update one account's state after each exchange."""

    def __init__(
        self,
        session: Session,
        options: ClientOptions,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        origin = urlsplit(options.base_url)
        production = origin.scheme == "https" and origin.netloc == "i.instagram.com"
        local_test = origin.scheme == "http" and origin.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        if not (production or local_test) or origin.username or origin.query or origin.fragment:
            raise ValueError(
                "API origin must be i.instagram.com over HTTPS or a loopback test server."
            )
        self.session = session
        self.options = options
        self.auth_options = copy.deepcopy(options.auth)
        self.guard = RequestGuard(session, options.policy)
        self.session_file: Path | None = None
        self._save_lock = asyncio.Lock()
        self._extra_headers = options.extra_headers
        self._pigeon_id = "UFS-" + str(uuid4()) + "-0"
        self._semaphore = asyncio.Semaphore(options.max_connections)
        verify = (
            ssl.create_default_context(cafile=str(options.ca_bundle)) if options.ca_bundle else True
        )
        self._http = httpx.AsyncClient(
            base_url=options.base_url.rstrip("/") + "/",
            http2=options.http2,
            trust_env=False,
            follow_redirects=False,
            verify=verify,
            proxy=options.proxy,
            transport=http_transport,
            timeout=httpx.Timeout(
                options.timeout,
                connect=options.connect_timeout,
                read=options.read_timeout or options.timeout,
                write=options.write_timeout or options.timeout,
                pool=options.pool_timeout or options.timeout,
            ),
            limits=httpx.Limits(
                max_connections=options.max_connections,
                max_keepalive_connections=(
                    options.max_keepalive_connections
                    if options.max_keepalive_connections is not None
                    else options.max_connections
                ),
                keepalive_expiry=options.keepalive_expiry,
            ),
        )
        for cookie in session.cookies:
            self._http.cookies.jar.set_cookie(cookie.to_cookie())

    async def aclose(self) -> None:
        await self._http.aclose()

    async def checkpoint(self) -> None:
        if self.session_file is not None:
            async with self._save_lock:
                snapshot = copy.deepcopy(self.session)
                await asyncio.to_thread(snapshot.save, self.session_file)

    async def pause(self, reason: str) -> None:
        self.session.paused_reason = reason
        await self.checkpoint()

    async def request(
        self, method: str, endpoint: str, data: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        return await self._checked(await self.send(method, endpoint, data))

    async def graphql(self, request: GraphQLRequest) -> dict[str, Any]:
        """Send one operation and return its merged envelope, including stream metadata.

        GraphQL errors raise the same typed errors as REST, even with HTTP 200. No
        partial data is returned on failure and no automatic retry is performed.
        """
        data = RequestData(
            request.form(),
            request.headers(),
        )
        response = await self.send("POST", request.endpoint, data)
        return response.graphql(self.options.max_response_bytes)

    async def upload(self, upload: MediaUpload) -> dict[str, Any]:
        """Send one media body once and return Instagram's acknowledgement.

        Video and audio uploads first read the stored offset, as the app does. A nonzero
        offset means the entity name is already in use, so the body is not sent. A failed
        or uncertain upload is never resent.
        """
        if upload.route.probes:
            probe = RequestData({}, upload.offset_headers())
            stored = await self._checked(await self.send("GET", upload, probe))
            if stored.get("offset") != 0:
                raise ProtocolError("Instagram reported existing data for a new video upload.")
        data = RequestData({}, upload.headers(), upload.content)
        return await self._checked(await self.send("POST", upload, data))

    async def _checked(self, response: APIResponse) -> dict[str, Any]:
        """Decode a JSON reply and raise its typed error; a rejected session is cleared."""
        payload = response.json()
        try:
            response.raise_for_error(payload)
        except LoginRequired:
            self.clear_authentication()
            await self.checkpoint()
            raise
        return payload

    async def request_stream(
        self, method: str, endpoint: str, data: Mapping[str, str] | None = None
    ) -> tuple[dict[str, Any], ...]:
        """Return bounded REST stream records after checking every record for errors."""
        response = await self.send(method, _JSONStreamEndpoint(endpoint), data)
        return response.json_stream(self.options.max_response_bytes)

    async def send(
        self,
        method: str,
        endpoint: str | GraphQLRoute | _JSONStreamEndpoint | MediaUpload,
        data: Mapping[str, str] | RequestData | None = None,
    ) -> APIResponse:
        if isinstance(endpoint, GraphQLRoute):
            # Root routes are enum members, never user-supplied absolute URLs.
            target: str | httpx.URL = self._http.base_url.copy_with(path=endpoint.value, query=None)
            endpoint_name = endpoint.value
        elif isinstance(endpoint, MediaUpload):
            # Direct media goes to Meta's fixed upload host; loopback tests keep their origin.
            if endpoint.route.direct and self._http.base_url.host == "i.instagram.com":
                target = httpx.URL(DIRECT_UPLOAD_ORIGIN + endpoint.path)
            else:
                target = self._http.base_url.copy_with(path=endpoint.path, query=None)
            endpoint_name = endpoint.route.value
        else:
            path = endpoint.path if isinstance(endpoint, _JSONStreamEndpoint) else endpoint
            self._validate_endpoint(path)
            target = path
            endpoint_name = path.split("?")[0]
        if self._http.is_closed:
            raise RuntimeError("Instagram client is closed; create a new client.")
        async with self._semaphore:
            await self.guard.admit()
            await self.checkpoint()
            self.guard.check()
            headers = self._headers()
            form = data.form if isinstance(data, RequestData) else data
            content = data.content if isinstance(data, RequestData) else None
            if isinstance(data, RequestData):
                headers.update(data.headers)
            try:
                async with self._http.stream(
                    method, target, headers=headers, data=form or None, content=content
                ) as res:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in res.aiter_bytes():
                        size += len(chunk)
                        if size > self.options.max_response_bytes:
                            raise ProtocolError("API response exceeded the configured byte limit.")
                        chunks.append(chunk)
                    result = APIResponse(
                        res.status_code, res.headers, endpoint_name, b"".join(chunks)
                    )
                    self.receive_state(res.headers)
                    if result.status_code == 429:
                        hint = result._retry_after()
                        delay = hint if hint is not None else self.options.policy.fallback_cooldown
                        self.session.rate_limit_until = max(
                            self.session.rate_limit_until or 0.0, time.time() + delay
                        )
                        await self.checkpoint()
                        raise RateLimited(
                            ErrorInfo(429, "rate_limit_error", result.endpoint), delay
                        )
                    await self._observe_account_state(result)
                    await self.checkpoint()
                    if isinstance(endpoint, GraphQLRoute) and not 300 <= result.status_code < 400:
                        await self._check_graphql(result)
                    elif isinstance(endpoint, _JSONStreamEndpoint):
                        if not 300 <= result.status_code < 400:
                            await self._check_stream(result)
            except httpx.HTTPError:
                # Transport exceptions may contain proxy credentials; preserve only safe context.
                raise NetworkError(
                    "Instagram's HTTP exchange failed; no automatic retry was made."
                ) from None
        if 300 <= result.status_code < 400:
            raise ProtocolError("Instagram returned a redirect; credentials were not forwarded.")
        return result

    async def _observe_account_state(self, response: APIResponse) -> None:
        try:
            payload = json.loads(response.content)
        except (ValueError, UnicodeError):
            return
        if not isinstance(payload, dict):
            return
        await self._observe_payload(payload)

    async def _observe_payload(self, payload: dict[str, Any]) -> None:
        category = payload.get("error_type") or payload.get("message")
        if not isinstance(category, str):
            category = ""
        if self.options.policy.pause_on_challenge and (
            category in {"challenge_required", "checkpoint_required"} or payload.get("challenge")
        ):
            await self.pause("challenge_required")
        elif self.options.policy.pause_on_restriction and category in {
            "feedback_required",
            "sentry_block",
        }:
            await self.pause("account_restricted")

    async def _check_graphql(self, response: APIResponse) -> None:
        try:
            envelope = response.graphql(self.options.max_response_bytes)
        except ProtocolError:
            if response.status_code >= 400:
                await self._check_payloads(response, [response.json()])
            raise
        failures = error_payloads(envelope)
        await self._check_payloads(response, [*failures, envelope])

    async def _check_stream(self, response: APIResponse) -> None:
        try:
            records = response.json_stream(self.options.max_response_bytes)
        except ProtocolError:
            if response.status_code >= 400:
                await self._check_payloads(response, [response.json()])
            raise
        await self._check_payloads(response, records)

    async def _check_payloads(
        self, response: APIResponse, payloads: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> None:
        # Observe every error before raising: a generic field error must not hide a pause.
        for failure in payloads:
            await self._observe_payload(failure)
            category = failure.get("error_type") or failure.get("message")
            if category == "login_required":
                self.clear_authentication()
            if category == "rate_limit_error":
                hint = response._retry_after()
                delay = hint if hint is not None else self.options.policy.fallback_cooldown
                self.session.rate_limit_until = max(
                    self.session.rate_limit_until or 0.0, time.time() + delay
                )
        await self.checkpoint()
        try:
            for failure in payloads:
                response.raise_for_error(failure)
        except LoginRequired:
            self.clear_authentication()
            await self.checkpoint()
            raise

    def clear_authentication(self) -> None:
        self.session.clear_authentication()
        self._http.cookies.clear()
        for cookie in self.session.cookies:
            self._http.cookies.jar.set_cookie(cookie.to_cookie())

    def _headers(self) -> dict[str, str]:
        session, profile, device = self.session, self.session.profile, self.session.device
        headers = {
            "User-Agent": profile.user_agent,
            "X-IG-App-ID": profile.app_id,
            "X-IG-Capabilities": profile.capabilities,
            "X-Bloks-Version-Id": profile.bloks_version,
            "Accept-Language": profile.locale.replace("_", "-"),
            "Accept-Encoding": "gzip, deflate, zstd",
            "X-IG-App-Locale": profile.locale,
            "X-IG-Device-Locale": profile.locale,
            "X-IG-Mapped-Locale": profile.locale,
            "X-IG-Timezone-Offset": str(profile.timezone_offset),
            "X-IG-Device-ID": device.id,
            "X-IG-Android-ID": device.android_id,
            "X-IG-Family-Device-ID": device.family_id,
            "X-Pigeon-Session-Id": self._pigeon_id,
            "X-Pigeon-Rawclienttime": f"{time.time():.3f}",
            "IG-INTENDED-USER-ID": session.user_id or "0",
        }
        headers.update(self._extra_headers)
        headers.update({k: v for k, v in session.state_headers.items() if k in STATE_HEADERS})
        if session.authorization:
            headers["Authorization"] = session.authorization
        if session.user_id:
            headers["IG-U-DS-USER-ID"] = session.user_id
        if session.mid:
            headers["X-MID"] = session.mid
        return headers

    def receive_state(self, headers: httpx.Headers) -> None:
        """Apply server state from HTTP headers or an embedded CAA login response."""
        session = self.session
        for name, value in headers.items():
            target = name.removeprefix("ig-set-")
            if target in STATE_HEADERS:
                session.state_headers[target] = value
        claim = headers.get("x-ig-set-www-claim") or headers.get("x-ig-set-www-claim-v2")
        if claim:
            session.state_headers["x-ig-www-claim"] = claim
        session.mid = headers.get("ig-set-x-mid") or session.mid
        if session.authenticated and headers.get("ig-set-authorization"):
            session.authorization = headers["ig-set-authorization"]
        session.cookies = [
            SessionCookie(
                c.name, c.value or "", c.domain, c.path, c.secure, c.expires, not c.domain_specified
            )
            for c in self._http.cookies.jar
        ]
        session.mid = session.mid or session.cookie_value("mid")

    @staticmethod
    def _validate_endpoint(endpoint: str) -> None:
        parsed = urlsplit(endpoint)
        decoded = unquote(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or endpoint.startswith("/")
            or ".." in decoded.split("/")
            or "\\" in decoded
            or decoded.startswith("/")
            or any(c in endpoint for c in "\r\n")
        ):
            raise ValueError("Endpoint must be a relative API path without traversal or an origin.")
