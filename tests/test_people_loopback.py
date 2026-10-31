"""Real loopback HTTP proof for recorded request forms and streamed account errors."""

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

from async_instalib import ClientOptions, Instagram, RequestPolicy, Session
from async_instalib.config.people import SearchOptions
from async_instalib.exceptions import AccountPaused, AccountRestricted, ChallengeRequired
from async_instalib.transport import Transport


def stream(*documents):
    return "\n".join(json.dumps(value, ensure_ascii=False) for value in documents).encode()


@asynccontextmanager
async def serve(responses):
    received = []

    async def handler(reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
            lines = raw.decode("ascii").split("\r\n")
            method, target, _ = lines[0].split(" ")
            headers = {
                name.lower(): value
                for name, value in (line.split(": ", 1) for line in lines[1:] if line)
            }
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            response_index = len(received)
            received.append((method, target, headers, body))
            content = responses[response_index]
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Connection: close\r\n"
                b"Set-Cookie: synthetic_cookie=value; Path=/; HttpOnly\r\n"
                b"ig-set-authorization: Bearer rotated-synthetic\r\n\r\n"
            )
            # Split both JSON documents and multibyte characters across wire chunks.
            for start in range(0, len(content), 11):
                chunk = content[start : start + 11]
                writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                await writer.drain()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/api/v1/", received
    finally:
        server.close()
        await server.wait_closed()


def options(base_url):
    return ClientOptions(base_url=base_url, policy=RequestPolicy(min_interval=0))


def session():
    return Session(authorization="Bearer initial-synthetic", user_id="42")


async def test_people_search_and_streamed_suggestions_over_real_http():
    query = "किरण & café+/?"
    search = stream(
        {"users": [{"pk": "123", "username": "synthetic"}], "has_more": False, "status": "ok"}
    )
    root_name = "xdt_fbsearch__typeahead_stream"
    suggestions = stream(
        {"data": {root_name: {"results": [{"__typename": "XDTTypeaheadSearchKeyword"}]}}},
        [
            {
                "path": [root_name, "results", 1],
                "data": {"__typename": "XDTTypeaheadSearchUser", "user": {"id": "456"}},
            }
        ],
        {"path": [root_name, "results", 1, "user"], "data": {"username": "café_synthetic"}},
    )
    async with serve([search, suggestions]) as (base_url, requests):
        async with Instagram(session(), options(base_url)) as client:
            found = await client.people.search(query, SearchOptions(limit=7))
            suggested = await client.people.suggest(query)
        assert [(person.id, person.username) for person in found.items] == [("123", "synthetic")]
        assert [(person.id, person.username) for person in suggested.items] == [
            ("456", "café_synthetic")
        ]
        assert len(requests) == 2
        method, target, headers, body = requests[0]
        assert method == "GET"
        assert urlsplit(target).path == "/api/v1/fbsearch/account_serp/"
        assert parse_qs(urlsplit(target).query)["query"] == [query]
        assert parse_qs(urlsplit(target).query)["count"] == ["7"]
        assert not body
        assert headers["authorization"] == "Bearer initial-synthetic"
        method, target, headers, body = requests[1]
        assert method == "POST"
        assert target == "/graphql/query"
        assert headers["content-type"] == "application/x-www-form-urlencoded"
        assert headers["authorization"] == "Bearer rotated-synthetic"
        assert "synthetic_cookie=value" in headers["cookie"]
        form = parse_qs(body.decode())
        assert form["fb_api_req_friendly_name"] == ["TypeaheadSearchQuery"]
        assert form["client_doc_id"] == ["702998554512640115307956645"]
        assert json.loads(form["variables"][0])["request_data"]["query"] == query


async def test_late_rest_stream_restriction_persists_and_stops_queued_http(tmp_path):
    response = stream(
        {"user": {"pk": "123", "username": "synthetic"}, "status": "ok"},
        {"status": "fail", "message": "feedback_required"},
    )
    async with serve([response]) as (base_url, requests):
        transport = Transport(session(), options(base_url))
        state_file = tmp_path / "account.json"
        transport.session_file = state_file
        try:
            outcomes = await asyncio.gather(
                transport.request_stream("GET", "users/123/info_stream/"),
                transport.request_stream("GET", "users/456/info_stream/"),
                return_exceptions=True,
            )
            assert isinstance(outcomes[0], AccountRestricted)
            assert isinstance(outcomes[1], AccountPaused)
            assert Session.load(state_file).paused_reason == "account_restricted"
            assert len(requests) == 1
        finally:
            await transport.aclose()


async def test_graphql_http200_challenge_stops_next_people_operation():
    response = stream(
        {"data": {"xdt_fbsearch__typeahead_stream": {"results": []}}},
        {"errors": [{"extensions": {"error_type": "challenge_required"}}]},
    )
    async with serve([response]) as (base_url, requests):
        async with Instagram(session(), options(base_url)) as client:
            with pytest.raises(ChallengeRequired):
                await client.people.suggest("synthetic")
            with pytest.raises(AccountPaused):
                await client.people.search("synthetic")
        assert len(requests) == 1
