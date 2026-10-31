"""Synthetic decoding and cursor isolation; no Instagram traffic."""

from dataclasses import replace
from typing import Any

import pytest

from async_instalib.config.people import PeopleListOptions, SearchOptions
from async_instalib.models.pagination import Continuation, Page, PageContext
from async_instalib.models.people import PersonProfile, PersonSummary, Relationship


def test_partial_person_does_not_claim_missing_flags_are_false() -> None:
    person = PersonSummary.from_dict({"pk": 42, "username": "synthetic"})
    assert person.id == "42"
    assert person.full_name is None
    assert person.is_private is None
    assert person.is_verified is None
    assert person.relationship is None
    assert PersonSummary.from_dict({"id": "42", "username": "synthetic", "is_private": False})


@pytest.mark.parametrize("identifier", [None, True, False, 0, -1, 1.5, "", "-1", "1.0", "١"])
def test_person_rejects_invalid_ids(identifier: Any) -> None:
    with pytest.raises(ValueError):
        PersonSummary.from_dict({"pk": identifier, "username": "synthetic"})


@pytest.mark.parametrize(
    "fields",
    [
        {"id": "43"},
        {"username": " "},
        {"username": 7},
        {"is_private": "false"},
        {"is_verified": 0},
        {"full_name": []},
        {"profile_pic_url": {}},
        {"friendship_status": []},
    ],
)
def test_person_rejects_malformed_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PersonSummary.from_dict({"pk": 42, "username": "synthetic", **fields})


def test_relationship_distinguishes_pending_follow_and_unknown() -> None:
    person = PersonSummary.from_dict(
        {
            "pk": 42,
            "username": "synthetic",
            "friendship_status": {"following": False, "outgoing_request": True},
        }
    )
    assert person.relationship is not None
    assert person.relationship.user_id == "42"
    assert person.relationship.following is False
    assert person.relationship.outgoing_request is True
    assert person.relationship.blocking is None
    with pytest.raises(ValueError):
        Relationship.from_dict({"following": "false"})


def test_profile_preserves_optional_counts_and_raw_without_aliasing() -> None:
    payload: dict[str, Any] = {
        "pk": "42",
        "username": "synthetic",
        "follower_count": 0,
        "biography": "",
        "extra": {"unmodeled": "secret-marker"},
    }
    person = PersonProfile.from_dict(payload)
    assert person.follower_count == 0
    assert person.following_count is None
    assert person.biography == ""
    assert "secret-marker" not in repr(person)
    payload["extra"]["unmodeled"] = "changed"
    assert person.raw["extra"]["unmodeled"] == "secret-marker"


@pytest.mark.parametrize("value", [-1, True, 2.5, "12"])
def test_profile_rejects_malformed_counts(value: Any) -> None:
    with pytest.raises(ValueError):
        PersonProfile.from_dict({"id": "42", "username": "synthetic", "media_count": value})


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "following"},
        {"account_id": "99"},
        {"target": "43"},
        {"query": "other"},
    ],
)
def test_continuation_rejects_cross_context_reuse(changes: dict[str, str]) -> None:
    context = PageContext("followers", "11", target="42", query="synthetic")
    cursor = Continuation("opaque-secret", context, rank_token="rank-secret")
    cursor.validate_for(context)
    with pytest.raises(ValueError):
        cursor.validate_for(replace(context, **changes))
    assert "opaque-secret" not in repr(cursor)
    assert "rank-secret" not in repr(cursor)


def test_page_preserves_order_and_only_uses_explicit_continuation() -> None:
    cursor = Continuation("next", PageContext("search", "11", query="test"))
    page = Page(("b", "a", "b"), cursor, raw={"debug": "secret-marker"})
    assert page.items == ("b", "a", "b")
    assert page.has_more
    assert page.total_count is None
    assert not Page(()).has_more
    assert "secret-marker" not in repr(page)
    with pytest.raises(ValueError):
        Page((), total_count=True)


@pytest.mark.parametrize("limit", [True, False, 0, -2, 1.5, "20"])
def test_options_require_positive_integer_limit(limit: Any) -> None:
    with pytest.raises(ValueError):
        SearchOptions(limit=limit)


def test_options_reject_unbound_cursor_and_preserve_unicode_query() -> None:
    query = "किरण & a+b?/%"
    options = PeopleListOptions(query=query)
    assert options.query == query
    with pytest.raises(ValueError):
        SearchOptions(cursor="unbound")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PeopleListOptions(cursor="unbound")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PeopleListOptions(query=42)  # type: ignore[arg-type]
