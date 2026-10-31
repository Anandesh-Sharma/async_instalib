"""Read literal muted-account bindings from captured Bloks layouts without executing them."""

import json
import math
from copy import deepcopy
from typing import Any

from ..auth.bloks import expression_at
from ..config.muted import MutedDecodeOptions
from ..exceptions import ProtocolError
from ..models.muted import MutedPerson
from ..models.pagination import Page
from ..models.people import _identifier

# Full machine action names from Android 452.1.0.53.64, not localized labels.
_ACTIONS = {
    "com.bloks.www.com.igcarbon.profile.mute_profile_bottom_sheet.mute_posts": "posts",
    "com.bloks.www.com.igcarbon.profile.mute_profile_bottom_sheet.mute_stories": "stories",
    "com.bloks.www.com.igcarbon.profile.mute_profile_bottom_sheet.mute_notes": "notes",
    "com.igcarbon.profile.mute_profile_bottom_sheet.mute_reposts": "activity",
    "com.igcarbon.profile.mute_profile_bottom_sheet.mute_friend_map_user": "map_notes",
    "com.igcarbon.profile.mute_profile_bottom_sheet.mute_friend_map_location_user": "map_location",
    "com.igcarbon.profile.mute_profile_bottom_sheet.mute_quick_snap_user": "quick_snap",
}


def _nodes(value: Any) -> list[Any]:
    pending = [value]
    result = []
    while pending:
        item = pending.pop()
        result.append(item)
        if isinstance(item, dict):
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON constant")


class _Budget:
    """Bound both the outer tree and data decoded from nested string literals."""

    def __init__(self, options: MutedDecodeOptions) -> None:
        self.options = options
        self.nodes = 0
        self.bytes = 0
        self.embedded = 0

    def inspect(self, value: Any) -> None:
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            self.nodes += 1
            if self.nodes > self.options.max_nodes or depth > self.options.max_depth:
                raise ValueError("Muted metadata exceeds its structural limits.")
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("Muted metadata keys must be strings.")
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                self.bytes += len(item.encode("utf-8"))
                if self.bytes > self.options.max_decoded_bytes:
                    raise ValueError("Muted metadata exceeds its decoded byte limit.")
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Muted metadata contains a non-finite value.")
            elif item is not None and type(item) not in {bool, int, float}:
                raise ValueError("Muted metadata contains an unsupported value.")

    def json(self, value: str) -> Any:
        self.inspect(value)
        result = json.loads(value, parse_constant=_reject_constant)
        self.inspect(result)
        return result

    def expression(self, value: str) -> list[Any]:
        # Inspect only syntax and literal data. Operators are never called or evaluated.
        text = value.strip()
        if not text.startswith("(") or not text.endswith(")"):
            raise ValueError("Expected a captured parenthesized action expression.")
        self.inspect(text)
        depth, offset = 0, 0
        decoder = json.JSONDecoder()
        while offset < len(text):
            if text[offset] == '"':
                _, offset = decoder.raw_decode(text, offset)
                continue
            if text[offset] == "(":
                depth += 1
                if depth > self.options.max_depth:
                    raise ValueError("Muted expression exceeds its depth limit.")
            elif text[offset] == ")":
                depth -= 1
                if depth < 0 or (depth == 0 and offset != len(text) - 1):
                    raise ValueError("Muted expression has trailing or unmatched syntax.")
            offset += 1
        if depth:
            raise ValueError("Muted expression is incomplete.")
        result = expression_at(text, 0)
        self.inspect(result)
        if not isinstance(result, list):
            raise ValueError("Muted expression must be a literal syntax tree.")
        return result


class MutedAccountsDecoder:
    """Bind literal navigation identities to machine switch states in known layouts.

    UI labels are ignored. Neither action handlers nor visibility expressions execute.
    Unrecognized rows fail explicitly rather than being silently reported as empty.
    """

    def __init__(self, options: MutedDecodeOptions | None = None) -> None:
        if options is not None and not isinstance(options, MutedDecodeOptions):
            raise ValueError("options must be MutedDecodeOptions or None.")
        self.options = options or MutedDecodeOptions()

    def decode(self, response: dict[str, Any]) -> Page[MutedPerson]:
        budget = _Budget(self.options)
        try:
            decoded = response["decoded_payload"]
            budget.inspect(decoded)
            payload = decoded["layout"]["bloks_payload"]
            tree = payload["tree"]
            attribution = budget.json(payload["error_attribution"]["logging_id"])
            callsite = budget.json(attribution["callsite"])
            if callsite.get("feature") != "MutedUsersPage":
                raise ValueError("Response is not the recorded muted-accounts screen.")
            profiles, sheets = self._entries(tree, budget)
            people: dict[str, MutedPerson] = {}
            for sheet in sheets:
                person = self._person(sheet, budget)
                if person.id in people:
                    raise ValueError("Muted metadata repeats an account row.")
                people[person.id] = person
            if set(people) != profiles:
                raise ValueError(
                    "Muted controls and profile navigation identify different accounts."
                )
            if not people and not self._empty_layout(tree):
                raise ValueError("Unrecognized empty muted-account layout.")
            return Page(tuple(people.values()), raw=response)
        except (KeyError, TypeError, ValueError, RecursionError, AttributeError):
            raise ProtocolError(
                "Muted screen does not expose supported, unambiguous account bindings."
            ) from None

    def _entries(
        self, tree: dict[str, Any], budget: _Budget
    ) -> tuple[set[str], list[dict[str, Any]]]:
        profiles: set[str] = set()
        sheets: list[dict[str, Any]] = []
        for item in _nodes(tree):
            if not isinstance(item, str):
                continue
            if "PushProfileScreen" in item:
                ast = budget.expression(item)
                found = []
                for node in _nodes(ast):
                    if (
                        isinstance(node, list)
                        and len(node) == 3
                        and node[0] == "gjz"
                        and node[2] == "PushProfileScreen"
                    ):
                        found.append(_identifier(node[1]))
                if len(found) != 1:
                    raise ValueError("Muted profile handler has no single literal identity.")
                profiles.update(found)
            if "mute_profile_bottom_sheet" not in item:
                continue
            ast = budget.expression(item)
            # The recorded sheet opener contains one JSON literal inside dow(...).
            literals = [
                node[1]
                for node in _nodes(ast)
                if isinstance(node, list)
                and len(node) == 2
                and node[0] == "dow"
                and isinstance(node[1], str)
            ]
            if len(literals) != 1:
                raise ValueError("Muted row does not contain one embedded sheet.")
            envelope = budget.json(literals[0])
            embedded = envelope.get("payload")
            if isinstance(embedded, str) and "layout" not in envelope:
                sheet = budget.json(embedded)
            elif "payload" not in envelope and isinstance(envelope.get("layout"), dict):
                # Both the app wrapper and the SDK's direct embedded envelope were recorded.
                sheet = envelope
            else:
                raise ValueError("Muted sheet has an unsupported or ambiguous embedded envelope.")
            budget.embedded += 1
            if budget.embedded > self.options.max_embedded_payloads:
                raise ValueError("Too many muted-account embedded sheets.")
            if not isinstance(sheet, dict):
                raise ValueError("Muted sheet must be a JSON object.")
            sheets.append(sheet)
        return profiles, sheets

    def _person(self, sheet: dict[str, Any], budget: _Budget) -> MutedPerson:
        tree = sheet["layout"]["bloks_payload"]["tree"]
        categories: dict[str, bool] = {}
        identities: set[str] = set()
        for control in _nodes(tree):
            if not isinstance(control, dict) or not isinstance(control.get("#"), str):
                continue
            handler = control["#"]
            if "mute_profile_bottom_sheet" not in handler:
                continue
            if control.get("$") not in {"on", "off"}:
                # Only a switch's literal state is evidence of its current value.
                continue
            ast = budget.expression(handler)
            actions = [
                node
                for node in _nodes(ast)
                if isinstance(node, list)
                and len(node) > 1
                and node[0] in {"dmh", "dmj"}
                and isinstance(node[1], str)
                and node[1] in _ACTIONS
            ]
            if len(actions) != 2 or actions[0][1] != actions[1][1]:
                raise ValueError("Muted switch does not bind one known category.")
            bindings = [self._binding(action) for action in actions]
            if bindings[0][0] != bindings[1][0] or {value for _, value in bindings} != {
                True,
                False,
            }:
                raise ValueError("Muted switch branches disagree about identity or action state.")
            category = _ACTIONS[actions[0][1]]
            if category in categories:
                raise ValueError("Muted sheet repeats a category control.")
            categories[category] = control["$"] == "on"
            identities.add(bindings[0][0])
        if len(identities) != 1 or not categories:
            raise ValueError("Muted sheet lacks a supported identity/category binding.")
        return MutedPerson(
            id=next(iter(identities)), username=None, **categories, raw=deepcopy(sheet)
        )

    @staticmethod
    def _binding(action: list[Any]) -> tuple[str, bool]:
        mappings = [
            node
            for node in _nodes(action)
            if isinstance(node, list)
            and len(node) == 3
            and node[0] == "f4i"
            and node[1] == ["dkc", "user_id", "is_muted"]
        ]
        if len(mappings) != 1:
            raise ValueError("Muted action lacks literal user/state parameters.")
        values = mappings[0][2]
        if not isinstance(values, list) or len(values) != 3 or values[0] != "dkc":
            raise ValueError("Muted action parameter shape changed.")
        identifier, state = values[1:]
        if action[0] == "dmh":
            if not isinstance(identifier, list) or len(identifier) != 2 or identifier[0] != "eud":
                raise ValueError("Muted action user ID is not a numeric literal.")
            if (
                not isinstance(state, list)
                or len(state) != 2
                or state[0] != "dqp"
                or type(state[1]) is not bool
            ):
                raise ValueError("Muted action state is not a boolean literal.")
            return _identifier(identifier[1]), state[1]
        if not isinstance(identifier, str) or state not in {"True", "False"}:
            raise ValueError("Muted action does not contain its recorded literal strings.")
        return _identifier(identifier), state == "True"

    @staticmethod
    def _empty_layout(tree: dict[str, Any]) -> bool:
        # Exact observed empty structure, with localized text deliberately ignored.
        try:
            root = tree["㐈"][" "]
            body = root[0]["㚞"]["#"]["㐈"][" "]
            message = body[0]["㐈"][" "]
            return len(root) == len(body) == len(message) == 1 and set(message[0]) == {"㐗"}
        except (KeyError, TypeError, IndexError):
            return False
