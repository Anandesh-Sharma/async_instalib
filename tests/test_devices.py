"""Real-phone user agents: the format the app writes, the default, and saved sessions."""

import pytest

from async_instalib import (
    GALAXY_S24,
    GALAXY_S25,
    GALAXY_S25_ULTRA,
    INFINIX_X6880,
    PIXEL_9_PRO,
    AndroidDevice,
    ClientProfile,
    Session,
)
from async_instalib.resources.posts import _ANDROID

PUBLISHED = {
    # Device fields of published Instagram app user agents (whatmyuseragent.com).
    GALAXY_S25: "Android (36/16; 480dpi; 1080x2340; samsung; SM-S931B; pa1q; qcom; ",
    GALAXY_S25_ULTRA: "Android (36/16; 600dpi; 1440x3120; samsung; SM-S938B; pa3q; qcom; ",
    GALAXY_S24: "Android (34/14; 480dpi; 1080x2340; samsung; SM-S921U; e1q; qcom; ",
    PIXEL_9_PRO: "Android (37/17; 480dpi; 1280x2856; Google/google; Pixel 9 Pro; caiman; caiman; ",
    INFINIX_X6880: (
        "Android (35/15; 440dpi; 1080x2436; INFINIX/Infinix; Infinix X6880; Infinix-X6880; mt6789; "
    ),
}


@pytest.mark.parametrize("device", list(PUBLISHED))
def test_presets_write_the_published_fields_with_the_recorded_build(device):
    agent = device.user_agent("de_DE")
    assert agent == f"Instagram 452.1.0.53.64 {PUBLISHED[device]}de_DE; 1100805085)"
    ClientProfile(user_agent=agent).with_locale("en_IN", 19800)  # one locale field


def test_default_profile_is_a_galaxy_s25_and_posts_read_its_fields():
    assert ClientProfile().user_agent == GALAXY_S25.user_agent()
    assert "sdk_gphone" not in ClientProfile().user_agent
    found = _ANDROID.search(ClientProfile().user_agent).groups()
    assert found == ("36", "16", "samsung", "SM-S931B")
    pixel = _ANDROID.search(ClientProfile.for_device(PIXEL_9_PRO).user_agent).groups()
    assert pixel == ("37", "17", "Google", "Pixel 9 Pro")


def test_saved_sessions_keep_their_device(tmp_path):
    path = tmp_path / "account.session.json"
    Session(profile=ClientProfile.for_device(GALAXY_S24)).save(path)
    assert "SM-S921U" in Session.load(path).profile.user_agent


@pytest.mark.parametrize(
    "fields",
    [
        ("36", 480, "1080x2340", "samsung", "SM-S931B", "pa1q", "qcom"),
        ("36/16", 48, "1080x2340", "samsung", "SM-S931B", "pa1q", "qcom"),
        ("36/16", 480, "big", "samsung", "SM-S931B", "pa1q", "qcom"),
        ("36/16", 480, "1080x2340", "samsung", "SM-S931B; x", "pa1q", "qcom"),
        ("36/16", 480, "1080x2340", "samsung", "SM-S931B)", "pa1q", "qcom"),
    ],
)
def test_devices_reject_fields_that_would_break_the_user_agent(fields):
    with pytest.raises(ValueError):
        AndroidDevice(*fields)
