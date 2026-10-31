"""Real Android phones as Instagram's app describes them in its user agent.

Each preset copies the device fields of a published Instagram app user agent
(whatmyuseragent.com, builds 447 to 451, collected 2026-10-31). The app version and
build number always come from the recorded 452.1.0.53.64 APK, because the GraphQL
document IDs belong to that build.
"""

import re
from dataclasses import dataclass

APP_VERSION = "452.1.0.53.64"
# The build number the recorded APK reports; other variants of 452.1.0.53.64 use others.
APP_BUILD = "1100805085"


@dataclass(frozen=True, slots=True)
class AndroidDevice:
    """One phone's fields in the Android user agent, written as the app writes them."""

    android: str  # API level / release, such as "36/16"
    dpi: int
    resolution: str  # width x height in pixels, such as "1080x2340"
    manufacturer: str  # "samsung", or "Google/google" when the brand differs in case
    model: str
    device: str  # the hardware code name
    cpu: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"\d{2}/\d{1,2}", self.android):
            raise ValueError("android must be API level / release, such as 36/16.")
        if type(self.dpi) is not int or not 100 <= self.dpi <= 1000:
            raise ValueError("dpi must be a whole number from 100 to 1000.")
        if not re.fullmatch(r"\d{3,4}x\d{3,4}", self.resolution):
            raise ValueError("resolution must look like 1080x2340.")
        for value in (self.manufacturer, self.model, self.device, self.cpu):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._/-]*", value):
                raise ValueError("Device names must be plain ASCII without ; or ( ).")

    def user_agent(self, locale: str = "en_US") -> str:
        """The Instagram 452.1.0.53.64 user agent on this phone."""
        return (
            f"Instagram {APP_VERSION} Android ({self.android}; {self.dpi}dpi; "
            f"{self.resolution}; {self.manufacturer}; {self.model}; {self.device}; "
            f"{self.cpu}; {locale}; {APP_BUILD})"
        )


# The default: the most-seen recent string, from several Galaxy S25 owners on 448.0.0.52.84.
GALAXY_S25 = AndroidDevice("36/16", 480, "1080x2340", "samsung", "SM-S931B", "pa1q", "qcom")
GALAXY_S25_ULTRA = AndroidDevice("36/16", 600, "1440x3120", "samsung", "SM-S938B", "pa3q", "qcom")
GALAXY_S24 = AndroidDevice("34/14", 480, "1080x2340", "samsung", "SM-S921U", "e1q", "qcom")
PIXEL_9_PRO = AndroidDevice(
    "37/17", 480, "1280x2856", "Google/google", "Pixel 9 Pro", "caiman", "caiman"
)
INFINIX_X6880 = AndroidDevice(
    "35/15", 440, "1080x2436", "INFINIX/Infinix", "Infinix X6880", "Infinix-X6880", "mt6789"
)
