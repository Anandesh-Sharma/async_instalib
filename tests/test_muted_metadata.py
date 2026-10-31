import json

import pytest

from async_instalib.config.muted import MutedDecodeOptions
from async_instalib.exceptions import ProtocolError
from async_instalib.resources.muted_metadata import MutedAccountsDecoder

POSTS = "com.bloks.www.com.igcarbon.profile.mute_profile_bottom_sheet.mute_posts"
NOTES = "com.bloks.www.com.igcarbon.profile.mute_profile_bottom_sheet.mute_notes"
ACTIVITY = "com.igcarbon.profile.mute_profile_bottom_sheet.mute_reposts"


def expression(value):
    if isinstance(value, list):
        return "(" + ", ".join(expression(item) for item in value) + ")"
    return json.dumps(value)


def control(target, *, action=POSTS, state="on", family="dmh"):
    branches = []
    for value in [True, False]:
        identity = ["eud", int(target)] if family == "dmh" else str(target)
        muted = ["dqp", value] if family == "dmh" else str(value)
        mapping = ["f4i", ["dkc", "user_id", "is_muted"], ["dkc", identity, muted]]
        branches.append([family, action, mapping])
    return {"$": state, "#": expression(["e2b", *branches])}


def row(target, *, controls=None, navigation_target=None, direct=False):
    controls = [control(target)] if controls is None else controls
    sheet = {
        "layout": {"bloks_payload": {"tree": {"controls": controls, "display": "非本地化标签"}}}
    }
    literal = json.dumps(sheet if direct else {"payload": json.dumps(sheet)})
    navigation = expression(
        ["e2f", ["ety", 1], ["gjz", str(navigation_target or target), "PushProfileScreen"]]
    )
    opener = expression(["dok", ["dow", literal], ["e1v"]])
    return {"navigation": navigation, "sheet": opener}


def response(rows=None, *, tree=None, feature="MutedUsersPage"):
    layout = {
        "layout": {
            "bloks_payload": {
                "tree": tree if tree is not None else {"rows": rows or []},
                "error_attribution": {
                    "logging_id": json.dumps({"callsite": json.dumps({"feature": feature})})
                },
            }
        }
    }
    return {"status": "ok", "decoded_payload": layout}


def empty_tree():
    return {
        "㐈": {" ": [{"㚞": {"#": {"㐈": {" ": [{"㐈": {" ": [{"㐗": {")": "任意语言"}}]}}]}}}}]}
    }


def test_literal_switch_state_wins_over_true_first_handler_branch():
    payload = response(
        [row("7", controls=[control("7", state="off"), control("7", action=NOTES, state="on")])]
    )
    page = MutedAccountsDecoder().decode(payload)
    person = page.items[0]
    assert person.id == "7"
    assert person.posts is False
    assert person.notes is True
    assert person.stories is None
    assert person.username is None
    assert page.continuation is None and page.total_count is None
    payload["decoded_payload"]["layout"].clear()
    assert person.raw["layout"]


def test_both_recorded_literal_parameter_families_parse_without_execution():
    payload = response(
        [
            row("7", controls=[control("7", action=ACTIVITY, family="dmj", state="on")]),
            row("8", controls=[control("8", state="off")]),
        ]
    )
    people = MutedAccountsDecoder().decode(payload).items
    assert [(person.id, person.activity, person.posts) for person in people] == [
        ("7", True, None),
        ("8", None, False),
    ]


def test_only_observed_empty_structure_returns_empty_page():
    assert MutedAccountsDecoder().decode(response(tree=empty_tree())).items == ()
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response())
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response(tree=empty_tree(), feature="AnotherScreen"))


@pytest.mark.parametrize(
    "rows",
    [
        [row("7", navigation_target="8")],
        [row("7"), row("7")],
        [row("7", controls=[control("7"), control("8", action=NOTES)])],
        [row("7", controls=[control("7"), control("7")])],
        [row("7", controls=[control("7", state="unknown")])],
    ],
)
def test_ambiguous_identity_or_controls_fail_instead_of_inventing_people(rows):
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response(rows))


def test_nonliteral_identity_is_not_evaluated():
    switch = control("7")
    switch["#"] = switch["#"].replace('("eud", 7)', '("execute", "7")')
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response([row("7", controls=[switch])]))


def test_branch_boolean_or_identity_conflicts_fail_closed():
    switch = control("7")
    switch["#"] = switch["#"].replace('("dqp", false)', '("dqp", true)')
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response([row("7", controls=[switch])]))


def test_trailing_expression_is_rejected():
    switch = control("7")
    switch["#"] += ' ("another_action")'
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response([row("7", controls=[switch])]))


@pytest.mark.parametrize(
    "options",
    [
        MutedDecodeOptions(max_decoded_bytes=100),
        MutedDecodeOptions(max_nodes=10),
        MutedDecodeOptions(max_depth=3),
        MutedDecodeOptions(max_embedded_payloads=1),
    ],
)
def test_nested_payloads_and_program_literals_share_decoding_limits(options):
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder(options).decode(response([row("7"), row("8")]))


def test_options_are_checked_before_parsing():
    with pytest.raises(ValueError):
        MutedDecodeOptions(max_nodes=True)
    with pytest.raises(ValueError):
        MutedAccountsDecoder({})


@pytest.mark.parametrize("direct", [False, True])
def test_wrapped_and_direct_embedded_sheets_preserve_two_enabled_categories(direct):
    payload = response(
        [
            row(
                "7",
                controls=[control("7", state="on"), control("7", action=NOTES, state="on")],
                direct=direct,
            )
        ]
    )
    person = MutedAccountsDecoder().decode(payload).items[0]
    assert person.id == "7"
    assert person.posts is True
    assert person.notes is True


def test_ambiguous_embedded_envelope_is_rejected():
    item = row("7")
    # Replace the outer JSON literal with conflicting direct and wrapped shapes.
    literal = json.dumps({"layout": {}, "payload": json.dumps({"layout": {}})})
    item["sheet"] = expression(["dok", ["dow", literal], ["e1v"]])
    with pytest.raises(ProtocolError):
        MutedAccountsDecoder().decode(response([item]))
