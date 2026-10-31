import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from async_instalib import ClientOptions, RequestPolicy, Session
from async_instalib.exceptions import (
    AccountPaused,
    AccountRestricted,
    APIError,
    ChallengeRequired,
    LoginRequired,
    ProtocolError,
    RateLimited,
)
from async_instalib.transport import Transport
from async_instalib.transport.graphql import GraphQLDecoder, GraphQLRequest


def stream(*documents):
    return "\n".join(json.dumps(document) for document in documents).encode()


def operation(**kwargs):
    return GraphQLRequest("SyntheticSearchQuery", {"query": "café"}, client_doc_id="123", **kwargs)


def transport(server, **kwargs):
    return Transport(
        Session(authorization="Bearer synthetic", user_id="42"),
        ClientOptions(policy=RequestPolicy(min_interval=0), **kwargs),
        httpx.MockTransport(server),
    )


def test_streamed_results_and_deferred_objects_preserve_metadata():
    initial = {
        "data": {"search": {"results": [{"user": {"id": "1", "username": "synthetic"}}]}},
        "extensions": {"trace": "synthetic"},
    }
    appended = {
        "path": ["search", "results", 1],
        "label": "results",
        "data": {"user": {"id": "2"}},
        "extensions": {"stream": True},
    }
    deferred = {"path": ["search", "results", 1, "user"], "data": {"username": "second"}}
    nested = {
        "path": ["search", "results", 0],
        "data": {"user": {"friendship_status": {"following": False}}},
    }
    envelope = GraphQLDecoder().decode(stream(initial, [appended], deferred, nested))
    users = [result["user"] for result in envelope["data"]["search"]["results"]]
    assert users == [
        {"id": "1", "username": "synthetic", "friendship_status": {"following": False}},
        {"id": "2", "username": "second"},
    ]
    assert envelope["extensions"] == initial["extensions"]
    assert envelope["incremental"] == [appended, deferred, nested]


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"[]",
        b"null",
        b"{}",
        b'{"data":{}} trailing',
        b'{"data":{"x":NaN}}',
        b'{"data":{},"errors":{}}',
        stream({"path": ["search"], "data": {}}),
        stream({"data": {"results": []}}, {"path": ["results", 99999999], "data": {}}),
        stream({"data": {"results": []}}, {"path": ["results", -1], "data": {}}),
        stream({"data": {"results": [{}]}}, {"path": ["results", True], "data": {}}),
        stream({"data": {}}, {"path": ["missing", "child"], "data": {}}),
        stream({"data": {"x": None}}, {"path": ["x"], "data": {}}),
        stream({"data": {}}, {"data": {"other": "root"}}),
        b'{"data":' + b"[" * 70 + b"0" + b"]" * 70 + b"}",
    ],
)
def test_malformed_or_unresolved_streams_fail_closed(content):
    with pytest.raises(ProtocolError):
        GraphQLDecoder().decode(content)


def test_decoder_limits_documents_and_bytes():
    body = stream({"data": {"x": {}}}, {"path": ["x"], "data": {}})
    with pytest.raises(ProtocolError):
        GraphQLDecoder(max_documents=1).decode(body)
    with pytest.raises(ProtocolError):
        GraphQLDecoder(max_bytes=len(body) - 1).decode(body)


@pytest.mark.parametrize(
    "route,path", [("query", "/graphql/query"), ("graphql_www", "/graphql_www")]
)
@pytest.mark.parametrize(
    "base_url", ["https://i.instagram.com/api/v1/", "http://127.0.0.1:8765/api/v1/"]
)
async def test_recorded_form_uses_same_origin_root_route(route, path, base_url):
    requests = []

    def server(request):
        requests.append(request)
        return httpx.Response(200, json={"data": {"search": {"results": []}}})

    client = transport(server, base_url=base_url)
    try:
        result = await client.graphql(operation(route=route))
        assert result["data"]["search"]["results"] == []
        request = requests[0]
        assert request.url == httpx.URL(base_url).copy_with(path=path)
        assert request.headers["authorization"] == "Bearer synthetic"
        assert request.headers["x-fb-friendly-name"] == "SyntheticSearchQuery"
        form = parse_qs(request.content.decode())
        assert form["locale"] == ["user"]
        assert form["client_doc_id"] == ["123"]
        assert form["method"] == ["post"]
        assert form["format"] == ["json"]
        assert form["server_timestamps"] == ["true"]
        assert json.loads(form["variables"][0]) == {"query": "café"}
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "endpoint", ["https://evil.invalid/graphql", "/graphql/query", "../graphql/query"]
)
async def test_ordinary_endpoint_strings_cannot_escape_api_path(endpoint):
    client = transport(lambda _: pytest.fail("Request must not be sent"))
    try:
        with pytest.raises(ValueError):
            await client.send("POST", endpoint)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "category,exception,pause",
    [
        ("challenge_required", ChallengeRequired, "challenge_required"),
        ("checkpoint_required", ChallengeRequired, "challenge_required"),
        ("feedback_required", AccountRestricted, "account_restricted"),
        ("sentry_block", AccountRestricted, "account_restricted"),
    ],
)
async def test_http200_graphql_errors_pause_before_next_request(category, exception, pause):
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(
            200,
            content=stream(
                {"data": {"search": {}}},
                {"path": ["search"], "errors": [{"extensions": {"error_type": category}}]},
            ),
        )

    client = transport(server)
    try:
        with pytest.raises(exception):
            await client.graphql(operation())
        assert client.session.paused_reason == pause
        with pytest.raises(AccountPaused):
            await client.graphql(operation())
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_all_errors_observed_and_provider_messages_redacted():
    def server(request):
        return httpx.Response(
            200,
            json={
                "data": None,
                "errors": [
                    {"message": "synthetic-private-provider-value"},
                    {"extensions": {"error_type": "feedback_required"}},
                ],
            },
        )

    client = transport(server)
    try:
        with pytest.raises(APIError) as error:
            await client.graphql(operation())
        assert "synthetic-private-provider-value" not in str(error.value)
        assert client.session.paused_reason == "account_restricted"
    finally:
        await client.aclose()


async def test_graphql_login_error_clears_session():
    client = transport(
        lambda _: httpx.Response(200, json={"errors": [{"message": "login_required"}]})
    )
    try:
        with pytest.raises(LoginRequired):
            await client.graphql(operation())
        assert not client.session.authenticated
    finally:
        await client.aclose()


async def test_graphql_rate_limit_persists_cooldown_without_retry():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"retry-after": "120"},
            json={"errors": [{"extensions": {"error_type": "rate_limit_error"}}]},
        )

    client = transport(server)
    try:
        with pytest.raises(RateLimited):
            await client.graphql(operation())
        assert client.session.rate_limit_until > time.time() + 100
        with pytest.raises(RateLimited):
            await client.graphql(operation())
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_graphql_transport_size_limit():
    client = transport(
        lambda _: httpx.Response(200, json={"data": {"x": "a" * 100}}), max_response_bytes=50
    )
    try:
        with pytest.raises(ProtocolError, match="byte limit"):
            await client.graphql(operation())
    finally:
        await client.aclose()


def test_request_validation_and_alternate_document_id():
    request = GraphQLRequest("SyntheticQuery", {}, doc_id="456")
    assert request.form()["doc_id"] == "456"
    assert "client_doc_id" not in request.form()
    with pytest.raises(ValueError):
        operation(doc_id="456")
    with pytest.raises(ValueError):
        operation(route="https://evil.invalid")
    with pytest.raises(ValueError):
        GraphQLRequest("SyntheticQuery", {"bad": float("nan")}, doc_id="456").form()


async def test_rest_stream_preserves_every_record():
    records = ({"user": {"id": "42"}, "status": "ok"}, {"user": {"username": "synthetic"}})
    client = transport(lambda _: httpx.Response(200, content=stream(*records)))
    try:
        assert await client.request_stream("GET", "users/42/info_stream/") == records
    finally:
        await client.aclose()


async def test_late_rest_stream_error_pauses_concurrent_request():
    import asyncio

    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(
            200,
            content=stream({"user": {"id": "42"}}, {"message": "feedback_required"}),
        )

    client = transport(server)
    try:
        results = await asyncio.gather(
            client.request_stream("GET", "users/42/info_stream/"),
            client.request_stream("GET", "users/42/info_stream/"),
            return_exceptions=True,
        )
        assert isinstance(results[0], AccountRestricted)
        assert isinstance(results[1], AccountPaused)
        assert len(calls) == 1
    finally:
        await client.aclose()


async def test_rest_stream_rejects_external_endpoint():
    client = transport(lambda _: pytest.fail("Request must not be sent"))
    try:
        with pytest.raises(ValueError):
            await client.request_stream("GET", "https://evil.invalid/info_stream/")
    finally:
        await client.aclose()


async def test_rest_stream_late_login_error_clears_session():
    client = transport(
        lambda _: httpx.Response(
            200,
            content=stream({"user": {"id": "42"}}, {"message": "login_required"}),
        )
    )
    try:
        with pytest.raises(LoginRequired):
            await client.request_stream("GET", "users/42/info_stream/")
        assert not client.session.authenticated
    finally:
        await client.aclose()


async def test_unresolved_partial_data_cannot_mask_graphql_account_challenge():
    client = transport(
        lambda _: httpx.Response(
            200,
            content=stream(
                {"data": {"search": {}}},
                {
                    "path": ["unresolved", "partial", "data"],
                    "data": {"user": {"id": "42"}},
                    "errors": [{"extensions": {"error_type": "challenge_required"}}],
                },
            ),
        )
    )
    try:
        with pytest.raises(ChallengeRequired):
            await client.graphql(operation())
        assert client.session.paused_reason == "challenge_required"
        with pytest.raises(AccountPaused):
            await client.graphql(operation())
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["graphql", "rest"])
async def test_non_json_401_clears_session_for_streams(kind):
    client = transport(lambda _: httpx.Response(401, content=b"Unauthorized"))
    try:
        with pytest.raises(LoginRequired):
            if kind == "graphql":
                await client.graphql(operation())
            else:
                await client.request_stream("GET", "users/42/info_stream/")
        assert not client.session.authenticated
    finally:
        await client.aclose()
