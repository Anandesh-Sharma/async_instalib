"""Public app identity and locale settings."""

import re
from dataclasses import dataclass, replace

from .devices import GALAXY_S25, AndroidDevice
from .validation import validate_header_value


@dataclass(frozen=True, slots=True)
class ClientProfile:
    """Public app metadata observed in Android 452.1.0.53.64; never account credentials.

    The user agent describes a real phone (``GALAXY_S25`` by default). A saved session
    keeps the profile it was created with, so its device never changes between runs.
    """

    user_agent: str = GALAXY_S25.user_agent()
    app_id: str = "567067343352427"
    bloks_version: str = "1dad8c6870f627b89627678d48ae07b89573dd4beb9cce4e6c7b4c56e60bf78d"
    capabilities: str = "3brTv10="
    locale: str = "en_US"
    timezone_offset: int = 0
    country_code: int = 1

    def __post_init__(self) -> None:
        for name in ("user_agent", "app_id", "bloks_version", "capabilities", "locale"):
            validate_header_value(getattr(self, name))
        if not re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}", self.locale):
            raise ValueError("Use a locale such as en_US or en_IN.")
        if type(self.timezone_offset) is not int or not -43200 <= self.timezone_offset <= 50400:
            raise ValueError("Timezone offset must be seconds between -43200 and 50400.")
        if type(self.country_code) is not int or not 1 <= self.country_code <= 999:
            raise ValueError("Country calling code must be an integer between 1 and 999.")

    @classmethod
    def for_device(cls, device: AndroidDevice) -> "ClientProfile":
        """The default app identity on another real phone; set it before signing in."""
        return cls(user_agent=device.user_agent())

    def with_locale(self, locale: str, timezone_offset: int) -> "ClientProfile":
        """Keep the Android user-agent locale and locale headers aligned."""
        user_agent, count = re.subn(
            r"; [a-z]{2,3}_[A-Z]{2};", lambda _: f"; {locale};", self.user_agent
        )
        if count != 1:
            raise ValueError("Custom user agent lacks one recognizable Android locale field.")
        return replace(self, user_agent=user_agent, locale=locale, timezone_offset=timezone_offset)
