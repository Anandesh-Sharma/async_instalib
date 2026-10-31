"""Read the small data subset needed from Bloks; never execute server expressions."""

import json
from collections.abc import Iterator
from typing import Any

from ..exceptions import LoginFlowUnsupported, ProtocolError


def strings(value: Any) -> Iterator[str]:
    """Walk JSON values without treating object keys as executable instructions."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))


def quoted_strings(expression: str) -> Iterator[str]:
    decoder, position = json.JSONDecoder(), 0
    while position < len(expression):
        if expression[position] != '"':
            position += 1
            continue
        try:
            value, position = decoder.raw_decode(expression, position)
        except ValueError:
            position += 1
            continue
        if isinstance(value, str):
            yield value


def expression_at(text: str, position: int) -> Any:
    """Parse one parenthesized value with bounded depth; operators remain inert strings."""
    stack: list[list[Any]] = []
    decoder = json.JSONDecoder()
    value: Any
    while position < len(text):
        character = text[position]
        if character.isspace() or character == ",":
            position += 1
        elif character == "(":
            stack.append([])
            if len(stack) > 128:
                raise ProtocolError("Bloks expression nesting exceeds the supported limit.")
            position += 1
        elif character == ")":
            if not stack:
                break
            value = stack.pop()
            if not stack:
                return value
            stack[-1].append(value)
            position += 1
        elif character == '"':
            value, position = decoder.raw_decode(text, position)
            if not stack:
                break
            stack[-1].append(value)
        else:
            end = position
            while end < len(text) and not text[end].isspace() and text[end] not in "(),":
                end += 1
            atom = text[position:end]
            try:
                value = json.loads(atom)
            except ValueError:
                value = atom
            if not stack:
                break
            stack[-1].append(value)
            position = end
    raise ProtocolError("Incomplete Bloks data expression.")


def server_parameters(payload: dict[str, Any], action: str) -> dict[str, Any]:
    """Read the literal server-parameter map immediately following the named action."""
    marker = json.dumps(action)
    for text in strings(payload):
        position = text.find(marker)
        if position < 0:
            continue
        start = text.find("(", position + len(marker))
        if start < 0:
            continue
        try:
            node = expression_at(text, start)
        except (ValueError, ProtocolError):
            continue
        if not isinstance(node, list) or len(node) != 3:
            continue
        keys, values = node[1:]
        if (
            not isinstance(keys, list)
            or not isinstance(values, list)
            or len(keys) != len(values)
            or not keys
            or keys[0] != values[0]
            or not all(isinstance(k, str) for k in keys[1:])
            or any(isinstance(v, (list, dict)) for v in values[1:])
        ):
            continue
        return dict(zip(keys[1:], values[1:], strict=True))
    raise LoginFlowUnsupported("Instagram's login screen did not expose supported action data.")


def account_access_context(payload: dict[str, Any]) -> str:
    for item in payload.get("layout", {}).get("bloks_payload", {}).get("data", []):
        data = item.get("data", {}) if isinstance(item, dict) else {}
        if not isinstance(data, dict) or data.get("key") != "CAA_ACCOUNT_ACCESS_CONTEXT:aac":
            continue
        initial = data.get("initial")
        if isinstance(initial, str) and initial:
            return initial
        expression = data.get("initial_lispy")
        if isinstance(expression, str):
            value = next(quoted_strings(expression), None)
            if value:
                return value
    raise LoginFlowUnsupported("Instagram did not issue an account access context.")


def login_result(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("layout", {}).get("bloks_payload", {}).get("action", "")
    if not isinstance(action, str):
        raise ProtocolError("Instagram's login response has no supported action data.")
    for value in quoted_strings(action):
        if "login_response" not in value:
            continue
        try:
            parsed = json.loads(value)
            result = json.loads(parsed["login_response"])
            headers = json.loads(parsed.get("headers") or "{}")
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(result, dict) and isinstance(headers, dict):
            return {"result": result, "headers": headers}
    raise LoginFlowUnsupported(
        "Instagram returned a verification or login continuation this client does not support. "
        "Complete verification in the app; no automatic password retry was made."
    )
