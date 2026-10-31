import json

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.config.metadata import AboutDecodeOptions
from async_instalib.exceptions import ProtocolError
from async_instalib.models.about import AboutAccount
from async_instalib.resources.profiles import Profiles
from async_instalib.transport import Transport

COUNTRY = "IG_ABOUT_THIS_ACCOUNT:about_this_account_country"
VISIBLE = "IG_ABOUT_THIS_ACCOUNT:about_this_account_country_visibility"


def envelope(states=None):
    if states is None:
        states = [
            {"type": "state", "data": {"key": COUNTRY, "initial": "Example country"}},
            {"type": "state", "data": {"key": VISIBLE, "initial": True}},
        ]
    bundle = {
        "layout": {
            "bloks_payload": {
                "data": states,
                "tree": {"display": "Joined in 2001", "program": "(dangerous looking program)"},
            }
        }
    }
    return {
        "data": {
            "bloks_app": {
                "screen_content": {
                    "component": {"bundle": {"bloks_bundle_tree": json.dumps(bundle)}}
                }
            }
        }
    }


def test_about_uses_named_states_and_preserves_inert_bundle():
    payload = envelope()
    details = AboutAccount.from_dict(payload, "7")
    assert details.country_display == "Example country"
    assert details.country_visible is True
    assert len(details.states) == 2
    assert details.bundle["layout"]["bloks_payload"]["tree"]["display"] == "Joined in 2001"
    assert not hasattr(details, "joined_at")
    assert AboutAccount.from_dict(envelope([]), "7").country_visible is None
    payload["data"].clear()
    assert details.raw["data"]


@pytest.mark.parametrize(
    "states",
    [
        [{"data": {"key": COUNTRY, "initial": 1}}],
        [{"data": {"key": VISIBLE, "initial": "true"}}],
        [{"data": {"key": COUNTRY, "initial": "A"}}, {"data": {"key": COUNTRY, "initial": "B"}}],
    ],
)
def test_about_rejects_invalid_or_conflicting_machine_fields(states):
    with pytest.raises(ValueError):
        AboutAccount.from_dict(envelope(states), "7")


@pytest.mark.parametrize(
    "options",
    [
        AboutDecodeOptions(max_bundle_bytes=10),
        AboutDecodeOptions(max_nodes=3),
        AboutDecodeOptions(max_depth=2),
    ],
)
def test_about_decoding_is_bounded(options):
    with pytest.raises(ValueError):
        AboutAccount.from_dict(envelope(), "7", options)


async def test_about_details_preserves_raw_operation_and_does_not_execute_actions():
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, json=envelope())

    client = Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )
    try:
        profiles = Profiles(client)
        assert (await profiles.about_details("7")).country_visible is True
        assert len(requests) == 1
        assert isinstance(await profiles.about("7"), dict)
        with pytest.raises(ProtocolError):
            await profiles.about_details(
                "7", decode_options=AboutDecodeOptions(max_bundle_bytes=10)
            )
    finally:
        await client.aclose()
