"""Networking and composed client configuration."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .auth import AuthOptions
from .policy import RequestPolicy
from .validation import validate_header_value


@dataclass(frozen=True, slots=True)
class ClientOptions:
    """Bounded networking settings; environment proxies are never used implicitly."""

    base_url: str = "https://i.instagram.com/api/v1/"
    timeout: float = 20.0
    connect_timeout: float = 5.0
    max_connections: int = 1
    read_timeout: float | None = None
    write_timeout: float | None = None
    pool_timeout: float | None = None
    max_keepalive_connections: int | None = None
    keepalive_expiry: float = 5.0
    http2: bool = True
    max_response_bytes: int = 16 * 1024 * 1024
    policy: RequestPolicy = field(default_factory=RequestPolicy)
    auth: AuthOptions = field(default_factory=AuthOptions)
    proxy: str | None = field(default=None, repr=False)
    ca_bundle: Path | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for timeout in (self.timeout, self.connect_timeout):
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Timeouts must be finite positive numbers.")
        if type(self.max_connections) is not int or self.max_connections < 1:
            raise ValueError("max_connections must be a positive integer.")
        for phase_timeout in (self.read_timeout, self.write_timeout, self.pool_timeout):
            if phase_timeout is not None and (
                not math.isfinite(phase_timeout) or phase_timeout <= 0
            ):
                raise ValueError("Per-phase timeouts must be finite and positive when supplied.")
        if self.max_keepalive_connections is not None and (
            type(self.max_keepalive_connections) is not int
            or not 0 <= self.max_keepalive_connections <= self.max_connections
        ):
            raise ValueError("Keepalive connections must be between zero and max_connections.")
        if not math.isfinite(self.keepalive_expiry) or self.keepalive_expiry < 0:
            raise ValueError("keepalive_expiry must be finite and nonnegative.")
        if type(self.max_response_bytes) is not int or self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer.")
        protected = {
            "authorization",
            "cookie",
            "host",
            "connection",
            "content-length",
            "content-type",
            "accept-encoding",
            "transfer-encoding",
            "proxy-authorization",
            "proxy-connection",
            "te",
            "trailer",
            "upgrade",
            "user-agent",
            "accept-language",
            "x-mid",
            "ig-intended-user-id",
            "x-ig-app-id",
            "x-ig-capabilities",
            "x-bloks-version-id",
            "x-ig-app-locale",
            "x-ig-device-locale",
            "x-ig-mapped-locale",
            "x-ig-timezone-offset",
            "x-ig-device-id",
            "x-ig-android-id",
            "x-ig-family-device-id",
            "x-ig-www-claim",
            "x-ig-attest-params",
            "x-meta-usdid",
            "x-fb-friendly-name",
            "x-client-doc-id",
            "x-root-field-name",
            "x-ig-validate-null-in-legacy-dict",
            "x-ig-validate-null-in-legacy-eager-dict",
            "x-graphql-client-library",
        }
        normalized: dict[str, str] = {}
        for name, value in self.extra_headers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ValueError("Extra header names must be valid HTTP tokens.")
            key = name.lower()
            if key in protected or key.startswith(("ig-u-", "ig-set-", "x-pigeon-")):
                raise ValueError(
                    "This header is managed by the client; "
                    "use typed configuration or request metadata."
                )
            if key in normalized:
                raise ValueError("Duplicate extra header names are not allowed.")
            validate_header_value(value)
            normalized[key] = value
        object.__setattr__(self, "extra_headers", MappingProxyType(normalized))
