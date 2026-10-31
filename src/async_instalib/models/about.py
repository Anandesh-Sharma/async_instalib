"""Named About-account state decoded without interpreting localized UI labels."""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..config.metadata import AboutDecodeOptions
from .people import _identifier, _object

_COUNTRY = "IG_ABOUT_THIS_ACCOUNT:about_this_account_country"
_COUNTRY_VISIBILITY = "IG_ABOUT_THIS_ACCOUNT:about_this_account_country_visibility"


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite constants are not valid About bundle values.")


def _bundle(value: object, limits: AboutDecodeOptions) -> dict[str, Any]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limits.max_bundle_bytes:
        raise ValueError("About bundle is missing or exceeds the byte limit.")
    try:
        result = json.loads(value, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise ValueError("About bundle is not valid finite JSON.") from None
    pending = [(result, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > limits.max_depth or count > limits.max_nodes:
            raise ValueError("About bundle exceeds the structural decoding limits.")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return _object(result)


@dataclass(frozen=True, slots=True)
class AboutState:
    """One explicitly named Bloks state entry, retaining its provider key and value."""

    key: str
    value: Any = field(repr=False)
    type: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AboutAccount:
    """Machine-keyed About fields and the decoded, inert provider bundle.

    country_display is the provider's display string, not an ISO country code.
    Joined dates and username history are not inferred from localized screen text.
    """

    user_id: str
    country_display: str | None = None
    country_visible: bool | None = None
    states: tuple[AboutState, ...] = ()
    bundle: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(
        cls, value: dict[str, Any], user_id: str, options: AboutDecodeOptions | None = None
    ) -> "AboutAccount":
        if options is not None and not isinstance(options, AboutDecodeOptions):
            raise ValueError("options must be AboutDecodeOptions or None.")
        payload = _object(value)
        identifier = _identifier(user_id)
        try:
            screen = payload["data"]["bloks_app"]["screen_content"]
            tree = _bundle(
                screen["component"]["bundle"]["bloks_bundle_tree"], options or AboutDecodeOptions()
            )
            entries = tree["layout"]["bloks_payload"]["data"]
        except (KeyError, TypeError):
            raise ValueError("About response is missing its structured state bundle.") from None
        if not isinstance(entries, list):
            raise ValueError("About state collection must be a list.")
        states: list[AboutState] = []
        named: dict[str, Any] = {}
        for entry in entries:
            row = _object(entry)
            data = _object(row.get("data"))
            key = data.get("key")
            if key is None:
                continue
            if not isinstance(key, str) or not key or "initial" not in data:
                raise ValueError("Named About state requires a key and initial value.")
            state_type = row.get("type")
            if state_type is not None and not isinstance(state_type, str):
                raise ValueError("About state type must be a string or null.")
            initial = data["initial"]
            if key in named and named[key] != initial:
                raise ValueError("About response has conflicting named state values.")
            named[key] = deepcopy(initial)
            states.append(AboutState(key, deepcopy(initial), state_type, deepcopy(row)))
        country = named.get(_COUNTRY)
        visible = named.get(_COUNTRY_VISIBILITY)
        if country is not None and not isinstance(country, str):
            raise ValueError("About country state must be a string or null.")
        if visible is not None and type(visible) is not bool:
            raise ValueError("About country visibility must be a boolean or null.")
        return cls(identifier, country, visible, tuple(states), tree, deepcopy(payload))
