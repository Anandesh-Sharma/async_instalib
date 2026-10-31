"""Shared pieces of the recorded direct-message requests."""

import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal

from ..exceptions import LoginRequired, ProtocolError
from ..models.direct import Message
from ..transport import Transport
from ..transport.graphql import GraphQLRequest


@dataclass(frozen=True, slots=True)
class Operation:
    """A recorded GraphQL operation: its name, document ID, result field, and route."""

    name: str
    document: str
    root: str
    route: Literal["query", "graphql_www"] = "graphql_www"


def new_otid() -> str:
    """An offline threading ID as the app makes it: milliseconds << 22 plus random bits."""
    return str((time.time_ns() // 1_000_000) << 22 | secrets.randbits(22))


def numeric(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError(f"{name} must be a numeric ID string.")
    return value


def thread_id(value: str) -> str:
    return numeric(value, "thread_id")


def require_session(transport: Transport) -> str:
    session = transport.session
    if not session.authenticated or not session.user_id:
        raise LoginRequired("Load your own authenticated session before using direct messages.")
    return session.user_id


async def form(transport: Transport, endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST a recorded direct form and require ``status: ok``."""
    require_session(transport)
    result = await transport.request(
        "POST", endpoint, {**fields, "_uuid": transport.session.device.id}
    )
    if result.get("status") != "ok":
        raise ProtocolError("Direct request did not confirm successful status.")
    return result


async def mutation(transport: Transport, operation: Operation, variables: dict[str, Any]) -> Any:
    """Send one recorded GraphQL operation and return the value at its root field."""
    require_session(transport)
    result = await transport.graphql(
        GraphQLRequest(
            operation.name,
            variables,
            client_doc_id=operation.document,
            route=operation.route,
            legacy_root_field=operation.root,
        )
    )
    data = result.get("data")
    if not isinstance(data, dict) or operation.root not in data:
        raise ProtocolError(f"{operation.name} response did not contain its result.")
    return data[operation.root]


def acknowledged(result: dict[str, Any], kind: str) -> Message:
    """Turn a broadcast ``item_ack`` into the sent Message (for later edit, unsend, react)."""
    payload = result.get("payload")
    if result.get("action") != "item_ack" or not isinstance(payload, dict):
        raise ProtocolError("Instagram did not acknowledge the message.")
    try:
        return Message.from_dict(
            {
                "item_id": payload["item_id"],
                "client_context": payload.get("client_context"),
                "timestamp": payload.get("timestamp"),
                "item_type": kind,
                "is_sent_by_viewer": True,
            },
            thread_id(str(payload["thread_id"])),
        )
    except (KeyError, ValueError):
        raise ProtocolError("Message acknowledgement lacked its item or thread.") from None
