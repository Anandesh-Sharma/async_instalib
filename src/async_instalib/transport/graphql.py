"""Recorded GraphQL form envelopes and bounded Instagram incremental JSON decoding."""

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from ..exceptions import ProtocolError


class GraphQLRoute(Enum):
    """Explicit same-origin routes; ordinary endpoint strings cannot select root paths."""

    QUERY = "/graphql/query"
    WWW = "/graphql_www"


@dataclass(frozen=True, slots=True, repr=False)
class GraphQLRequest:
    """One recorded operation, with exactly one document ID and JSON variables."""

    friendly_name: str
    variables: Mapping[str, Any] = field(repr=False)
    client_doc_id: str | None = None
    doc_id: str | None = None
    route: Literal["query", "graphql_www"] = "query"
    locale: str = "user"
    purpose: Literal["fetch"] | None = None
    legacy_root_field: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.friendly_name, str)
            or len(self.friendly_name) > 256
            or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9.-]*", self.friendly_name)
        ):
            raise ValueError("GraphQL friendly_name must be an operation identifier.")
        if (self.client_doc_id is None) == (self.doc_id is None):
            raise ValueError("Provide exactly one of client_doc_id and doc_id.")
        identifier = self.client_doc_id if self.client_doc_id is not None else self.doc_id
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]+", identifier):
            raise ValueError("GraphQL document IDs must be nonempty digit strings.")
        if self.route not in {"query", "graphql_www"}:
            raise ValueError("Unsupported GraphQL route.")
        if not isinstance(self.variables, Mapping):
            raise ValueError("GraphQL variables must be a mapping.")
        if not isinstance(self.locale, str) or not self.locale or "\n" in self.locale:
            raise ValueError("GraphQL locale must be a nonempty string.")
        if self.purpose not in {None, "fetch"}:
            raise ValueError("Unsupported GraphQL purpose.")
        if self.legacy_root_field is not None and (
            self.client_doc_id is None
            or not isinstance(self.legacy_root_field, str)
            or len(self.legacy_root_field) > 256
            or not re.fullmatch(r"[_A-Za-z][_A-Za-z0-9]*", self.legacy_root_field)
        ):
            raise ValueError(
                "Legacy GraphQL metadata requires a root field and client document ID."
            )

    @property
    def endpoint(self) -> GraphQLRoute:
        return GraphQLRoute.QUERY if self.route == "query" else GraphQLRoute.WWW

    def form(self) -> dict[str, str]:
        """Serialize without permitting NaN or changing the captured envelope defaults."""
        try:
            variables = json.dumps(dict(self.variables), separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            raise ValueError("GraphQL variables must contain finite JSON values.") from None
        form = {
            "method": "post",
            "format": "json",
            "server_timestamps": "true",
            "locale": self.locale,
            "fb_api_req_friendly_name": self.friendly_name,
            "variables": variables,
        }
        if self.client_doc_id is not None:
            form["client_doc_id"] = self.client_doc_id
        elif self.doc_id is not None:
            form["doc_id"] = self.doc_id
        if self.purpose is not None:
            form["purpose"] = self.purpose
        return form

    def headers(self) -> dict[str, str]:
        """Build operation-local metadata; the legacy bundle is explicitly selected.

        These Pando headers were required by the recorded activity mutations. They
        are generated from this request rather than persisted on the account client.
        """
        headers = {"X-FB-Friendly-Name": self.friendly_name}
        if self.legacy_root_field is not None:
            headers.update(
                {
                    "X-Client-Doc-ID": self.client_doc_id or "",
                    "X-Root-Field-Name": self.legacy_root_field,
                    "X-IG-Validate-Null-In-Legacy-Dict": "true",
                    "X-IG-Validate-Null-In-Legacy-Eager-Dict": "true",
                    "X-GraphQL-Client-Library": "pando",
                }
            )
        return headers


class JSONStreamDecoder:
    """Bound concatenated JSON objects or batches, shared by REST and GraphQL streams."""

    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_documents: int = 256) -> None:
        self.max_bytes = max_bytes
        self.max_documents = max_documents

    def decode(self, content: bytes) -> tuple[dict[str, Any], ...]:
        if len(content) > self.max_bytes:
            raise ProtocolError("JSON stream exceeded the configured byte limit.")
        try:
            text = content.decode("utf-8")
            decoder = json.JSONDecoder(parse_constant=self._reject_constant)
            offset = 0
            documents: list[dict[str, Any]] = []
            while offset < len(text):
                while offset < len(text) and text[offset].isspace():
                    offset += 1
                if offset == len(text):
                    break
                value, offset = decoder.raw_decode(text, offset)
                parts = value if isinstance(value, list) else [value]
                if not parts or any(not isinstance(part, dict) for part in parts):
                    raise ValueError
                documents.extend(parts)
                if len(documents) > self.max_documents:
                    raise ValueError
                self._check_depth(value)
        except (ValueError, UnicodeError, RecursionError):
            raise ProtocolError("Malformed or excessively complex JSON stream.") from None
        if not documents:
            raise ProtocolError("Expected a nonempty JSON stream.")
        return tuple(documents)

    @staticmethod
    def _reject_constant(value: str) -> Any:
        raise ValueError("Non-finite JSON constant")

    @staticmethod
    def _check_depth(value: Any, initial_depth: int = 0) -> None:
        pending = [(value, initial_depth)]
        while pending:
            item, depth = pending.pop()
            if depth > 64:
                raise ValueError
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)


class GraphQLDecoder:
    """Merge concatenated JSON documents, retaining metadata and every provider error.

    Returns a full envelope: ``data`` is the merged root dictionary, root metadata is
    unchanged, and ``incremental`` contains original deferred patches. Errors from all
    documents are collected in ``errors``. Unsupported or unresolved patches fail closed.
    Error envelopes retain unmerged data so malformed partial data cannot hide an account
    challenge; transport rejects every error envelope before exposing results.
    """

    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_documents: int = 256) -> None:
        self._decoder = JSONStreamDecoder(max_bytes, max_documents)

    def decode(self, content: bytes) -> dict[str, Any]:
        documents = list(self._decoder.decode(content))
        if not documents or "path" in documents[0]:
            raise ProtocolError("GraphQL response has no initial envelope.")
        root = documents[0]
        if "incremental" in root:
            raise ProtocolError("Unsupported GraphQL incremental envelope.")
        errors: list[dict[str, Any]] = []
        for document in documents:
            current = document.get("errors", [])
            if not isinstance(current, list) or any(not isinstance(e, dict) for e in current):
                raise ProtocolError("Malformed GraphQL error envelope.")
            errors.extend(current)
        if errors:
            root["errors"] = errors
            root["incremental"] = documents[1:]
            return root
        data = root.get("data")
        if not isinstance(data, dict):
            if (
                root.get("status") == "fail"
                or root.get("challenge")
                or root.get("error_type")
                or root.get("message")
            ):
                root["incremental"] = documents[1:]
                return root
            raise ProtocolError("GraphQL response has no root data object.")
        for patch in documents[1:]:
            self._apply_patch(data, patch)
        if len(documents) > 1:
            root["incremental"] = documents[1:]
        return root

    @classmethod
    def _apply_patch(cls, root: dict[str, Any], patch: dict[str, Any]) -> None:
        path = patch.get("path")
        value = patch.get("data")
        if (
            not isinstance(path, list)
            or not path
            or len(path) > 64
            or any(type(key) not in {str, int} for key in path)
            or not isinstance(value, dict)
        ):
            raise ProtocolError("Unsupported GraphQL patch shape.")
        try:
            JSONStreamDecoder._check_depth(value, len(path))
        except ValueError:
            raise ProtocolError("GraphQL patch exceeds the merged nesting limit.") from None
        parent: Any = root
        for key in path[:-1]:
            if isinstance(parent, dict) and isinstance(key, str) and key in parent:
                parent = parent[key]
            elif isinstance(parent, list) and type(key) is int and 0 <= key < len(parent):
                parent = parent[key]
            else:
                raise ProtocolError("GraphQL patch refers to an unresolved path.")
        last = path[-1]
        if isinstance(parent, list) and type(last) is int and last == len(parent):
            parent.append(copy.deepcopy(value))
        elif isinstance(parent, dict) and isinstance(last, str) and last in parent:
            if not isinstance(parent[last], dict):
                raise ProtocolError("GraphQL patch target is not an object.")
            cls._merge(parent[last], value)
        elif isinstance(parent, list) and type(last) is int and 0 <= last < len(parent):
            if not isinstance(parent[last], dict):
                raise ProtocolError("GraphQL patch target is not an object.")
            cls._merge(parent[last], value)
        else:
            raise ProtocolError("GraphQL patch refers to an unresolved path.")

    @classmethod
    def _merge(cls, target: dict[str, Any], value: dict[str, Any]) -> None:
        for key, item in value.items():
            if isinstance(target.get(key), dict) and isinstance(item, dict):
                cls._merge(target[key], item)
            else:
                target[key] = copy.deepcopy(item)


def error_payloads(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate explicit GraphQL error categories for the existing HTTP error policy."""
    failures = []
    if (
        envelope.get("status") == "fail"
        or envelope.get("challenge")
        or ("data" not in envelope and (envelope.get("error_type") or envelope.get("message")))
    ):
        failures.append({**envelope, "status": "fail"})
    for error in envelope.get("errors", []):
        extensions = error.get("extensions")
        extensions = extensions if isinstance(extensions, dict) else {}
        details = extensions.get("error_data")
        details = details if isinstance(details, dict) else {}
        category = (
            details.get("error_type")
            or extensions.get("error_type")
            or error.get("error_type")
            or extensions.get("type")
            or error.get("message")
            or "unknown"
        )
        failure = {"status": "fail", "error_type": category}
        challenge = (
            details.get("challenge") or extensions.get("challenge") or error.get("challenge")
        )
        if challenge:
            failure["challenge"] = challenge
        failures.append(failure)
    return failures
