"""Triggers: activity parsing, sources, baseline, delivery, signing, retries, and state."""

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl

import httpx
import pytest

from async_instalib import ClientOptions, Media, Notification, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import RateLimited
from async_instalib.models.pagination import Page
from async_instalib.resources.activity import Activity
from async_instalib.transport import Transport
from async_instalib.triggers import (
    ActivitySource,
    DeliveryError,
    Event,
    TaggedSource,
    TriggerOptions,
    Triggers,
    Webhook,
    WebhookSender,
)
from async_instalib.triggers import runner as runner_module
from async_instalib.triggers import webhooks as webhooks_module

ROW = {
    "pk": "abc==",
    "notif_name": "comment",
    "args": {
        "rich_text": "{friend|000000|1|user?id=42|none} commented: nice",
        "profile_id": 42,
        "profile_name": "friend",
        "media": [{"id": "5_11"}],
        "comment_id": 900,
        "timestamp": 1700000000.5,
    },
}


def event(identifier, kind="comment", at=1.0):
    return Event(identifier, kind, "fake", "11", occurred_at=at, text=identifier)


class FakeSource:
    """Returns whatever events the test puts in ``events``."""

    def __init__(self, name="fake"):
        self.name = name
        self.events = []

    async def poll(self, instagram):
        return list(self.events)


def test_notification_reads_rich_text_actor_media_and_comment():
    row = Notification.from_dict(ROW)
    assert row.text == "friend commented: nice"
    assert (row.actor_id, row.actor_username) == ("42", "friend")
    assert row.media_ids == ("5_11",) and row.comment_id == "900"
    assert row.occurred_at == 1700000000.5
    with pytest.raises(ValueError):
        Notification.from_dict({"args": {}})


async def test_activity_reads_without_marking_seen_and_merges_sections():
    def server(request):
        query = dict(parse_qsl(request.url.query.decode()))
        assert request.url.path == "/api/v1/news/inbox/"
        assert query["mark_as_seen"] == "false"
        older = {**ROW, "pk": "old", "args": {**ROW["args"], "timestamp": 1.0}}
        return httpx.Response(
            200,
            json={
                "priority_stories": [ROW],
                "new_stories": [ROW],
                "old_stories": [older],
                "status": "ok",
            },
        )

    transport = Transport(
        Session(authorization="Bearer synthetic", user_id="11"),
        ClientOptions(policy=RequestPolicy(min_interval=0)),
        httpx.MockTransport(server),
    )
    try:
        page = await Activity(transport).notifications()
        assert [row.id for row in page.items] == ["abc==", "old"]
    finally:
        await transport.aclose()


async def test_sources_map_rows_to_typed_events():
    rows = [
        Notification.from_dict(ROW),
        Notification.from_dict({**ROW, "pk": "m", "notif_name": "mentioned_comment"}),
        Notification.from_dict({"pk": "x", "notif_name": "suspicious_login", "args": {}}),
    ]
    instagram = SimpleNamespace(
        session=SimpleNamespace(user_id="11"),
        activity=SimpleNamespace(notifications=lambda: _page(rows)),
        people=SimpleNamespace(
            content=SimpleNamespace(
                tagged=lambda user_id: _page(
                    [
                        Media.from_dict(
                            {
                                "id": "7_42",
                                "taken_at": 5,
                                "caption": {"text": "hi @me"},
                                "user": {"pk": 42, "username": "friend"},
                            }
                        )
                    ]
                )
            )
        ),
    )
    activity = await ActivitySource().poll(instagram)
    assert [e.type for e in activity] == ["comment", "mention", "notification"]
    assert activity[0].id == "activity:abc==" and activity[0].actor.username == "friend"
    assert activity[2].data["name"] == "suspicious_login"
    (tag,) = await TaggedSource().poll(instagram)
    assert (tag.type, tag.id, tag.actor.id, tag.text) == ("tag", "tagged:7_42", "42", "hi @me")


async def _page(items):
    return Page(tuple(items))


async def test_first_poll_is_a_baseline_then_only_new_events_are_delivered(tmp_path):
    source = FakeSource()
    source.events = [event("a", at=2.0)]
    options = TriggerOptions(sources=(source,), state_file=tmp_path / "state.json")
    received = []
    async with Triggers(SimpleNamespace(), options) as triggers:

        @triggers.on()
        async def everything(item):
            received.append(item.id)

        assert await triggers.poll() == [] and received == []
        source.events = [event("b", at=3.0), event("a", at=2.0), event("c", at=1.0)]
        assert [e.id for e in await triggers.poll()] == ["c", "b"]
        assert received == ["c", "b"]
        assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600

    async with Triggers(SimpleNamespace(), options) as restarted:
        assert await restarted.poll() == []


async def test_handlers_filter_by_type_and_failures_do_not_stop_delivery():
    source = FakeSource()
    options = TriggerOptions(sources=(source,), deliver_existing=True)
    seen, errors = [], []
    async with Triggers(SimpleNamespace(), options) as triggers:

        @triggers.on("tag")
        async def tags(item):
            seen.append(("tag", item.id))

        @triggers.on("comment")
        async def broken(item):
            raise RuntimeError("receiver down")

        @triggers.on("comment")
        async def comments(item):
            seen.append(("comment", item.id))

        @triggers.on_error
        async def failed(item, error):
            errors.append((item.id, str(error)))

        source.events = [event("t", "tag"), event("c", "comment")]
        await triggers.poll()
    assert sorted(seen) == [("comment", "c"), ("tag", "t")]
    assert errors == [("c", "receiver down")]


async def test_webhooks_are_signed_and_retried(monkeypatch):
    async def no_wait(seconds):
        return None

    monkeypatch.setattr(webhooks_module.asyncio, "sleep", no_wait)
    replies = [httpx.Response(500), httpx.Response(204)]
    requests = []

    def endpoint(request):
        requests.append(request)
        return replies.pop(0)

    sender = WebhookSender(httpx.MockTransport(endpoint))
    try:
        hook = Webhook("https://example.test/hook", secret="s3cret", events={"comment"})
        await sender.send(hook, event("c1"))
        assert len(requests) == 2
        request = requests[-1]
        signed = request.headers["X-Instalib-Timestamp"].encode() + b"." + request.content
        expected = hmac.new(b"s3cret", signed, hashlib.sha256).hexdigest()
        assert request.headers["X-Instalib-Signature"] == "sha256=" + expected
        assert request.headers["X-Instalib-Delivery"] == "c1"
        assert json.loads(request.content)["text"] == "c1"
        assert "Authorization" not in request.headers
        assert hook.accepts(event("x", "comment")) and not hook.accepts(event("x", "tag"))
        replies[:] = [httpx.Response(400)]
        with pytest.raises(DeliveryError):
            await sender.send(hook, event("c2"))
        assert len(requests) == 3
        assert "s3cret" not in repr(hook)
    finally:
        await sender.aclose()


async def test_run_waits_out_rate_limits(monkeypatch):
    calls, sleeps = [], []

    class LimitedSource(FakeSource):
        async def poll(self, instagram):
            calls.append(1)
            if len(calls) == 1:
                raise RateLimited(None, 120.0)
            raise asyncio.CancelledError

    async def record(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(runner_module.asyncio, "sleep", record)
    triggers = Triggers(SimpleNamespace(), TriggerOptions(sources=(LimitedSource(),)))
    try:
        with pytest.raises(asyncio.CancelledError):
            await triggers.run()
        assert sleeps == [120.0]
    finally:
        await triggers.aclose()


def test_options_and_webhooks_reject_unsafe_values():
    with pytest.raises(ValueError, match="30"):
        TriggerOptions(sources=(FakeSource(),), interval=5)
    with pytest.raises(ValueError, match="distinct"):
        TriggerOptions(sources=(FakeSource(), FakeSource()))
    with pytest.raises(ValueError):
        Webhook("ftp://example.test/hook")
    with pytest.raises(ValueError):
        Event("", "comment", "fake", "11")
