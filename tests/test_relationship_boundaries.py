"""Identity and acknowledgement boundaries across relationship operations."""

import httpx
import pytest

from async_instalib import APIError, ClientOptions, ProtocolError, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import RequestBudgetExceeded
from async_instalib.models.people import Relationship
from async_instalib.resources.relationships import Relationships
from async_instalib.transport import Transport


def make_transport(handler, max_requests=60):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0, max_requests=max_requests)),
        httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("identifier", ["43", True, "invalid"])
def test_relationship_rejects_explicit_response_identity_conflicting_with_context(identifier):
    with pytest.raises(ValueError):
        Relationship.from_dict({"user_id": identifier, "following": True}, "42")


def test_relationship_keeps_matching_identity_and_unknown_context_truthful():
    assert Relationship.from_dict({"user_id": 42, "following": False}, "042").user_id == "42"
    assert Relationship.from_dict({"user_id": "42"}).user_id == "42"
    assert Relationship.from_dict({"following": False}).user_id is None


async def test_conflicting_response_identity_never_becomes_requested_person_state():
    transport = make_transport(
        lambda _: httpx.Response(200, json={"status": "ok", "user_id": "43", "following": True})
    )
    try:
        with pytest.raises(ProtocolError):
            await Relationships(transport).get("42")
    finally:
        await transport.aclose()


@pytest.mark.parametrize("method", ["follow", "add_close_friend"])
async def test_explicit_rest_error_cannot_be_treated_as_success_or_retried(method):
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "error": {"message": "private-marker"},
                "friendship_status": {"following": True},
            },
        )

    transport = make_transport(server)
    try:
        with pytest.raises(APIError) as error:
            await getattr(Relationships(transport), method)("42")
        assert "private-marker" not in str(error.value)
        assert len(calls) == 1
    finally:
        await transport.aclose()


async def test_lists_and_mutations_share_admission_budget_without_hidden_clients():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "ok", "friendship_status": {"following": True}})

    transport = make_transport(server, max_requests=1)
    try:
        relationships = Relationships(transport)
        await relationships.follow("42")
        with pytest.raises(RequestBudgetExceeded):
            await relationships.lists.blocked()
        assert len(calls) == 1
    finally:
        await transport.aclose()
