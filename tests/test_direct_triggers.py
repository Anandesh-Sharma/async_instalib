"""DirectSource: messages, reactions, and requests become events with header-safe IDs."""

from types import SimpleNamespace

import pytest

from async_instalib import Thread
from async_instalib.models.pagination import Page
from async_instalib.triggers import DirectSource, Event, TriggerOptions

FRIEND = {"pk": "42", "username": "friend"}


def item(identifier, **fields):
    return {
        "item_id": identifier,
        "user_id": 42,
        "item_type": "text",
        "timestamp": 1793399000000000,
        "client_context": "100",
        **fields,
    }


def thread(identifier, items, pending=False):
    return Thread.from_dict(
        {"thread_id": identifier, "users": [FRIEND], "items": items, "pending": pending}
    )


def instagram(inbox, requests=()):
    calls = []

    async def read_inbox(options):
        calls.append(options)
        return Page(tuple(inbox))

    async def read_requests():
        return Page(tuple(requests))

    direct = SimpleNamespace(inbox=read_inbox, requests=read_requests)
    return SimpleNamespace(session=SimpleNamespace(user_id="11"), direct=direct), calls


async def test_messages_from_others_become_events():
    mine = item("3", user_id=11, is_sent_by_viewer=True, text="mine")
    log = item("4", item_type="action_log", action_log={"description": "pinned"})
    client, calls = instagram([thread("340", [item("2", text="hi"), mine, log])])
    (event,) = await DirectSource().poll(client)
    assert (event.id, event.type, event.text) == ("direct:2", "message", "hi")
    assert (event.actor.id, event.actor.username) == ("42", "friend")
    assert event.occurred_at == pytest.approx(1793399000.0)
    assert event.data["thread_id"] == "340" and event.data["message"]["item_id"] == "2"
    assert calls[0].message_limit == 10


async def test_reactions_by_others_carry_the_emoji_in_an_ascii_id():
    emojis = [
        {"sender_id": 42, "emoji": "😂", "timestamp": 1793399172000000, "client_context": "7"},
        {"sender_id": 11, "emoji": "❤️", "timestamp": 1793399173000000, "client_context": "8"},
        {"sender_id": "bad", "emoji": "x"},
    ]
    mine = item("3", user_id=11, is_sent_by_viewer=True, reactions={"emojis": emojis})
    client, _ = instagram([thread("340", [mine])])
    (reaction,) = await DirectSource().poll(client)
    assert reaction.type == "message_reaction" and reaction.text == "😂"
    assert reaction.id == "direct_reaction:3:42:7:1f602"
    assert reaction.id.isascii() and reaction.occurred_at == 1793399172.0
    assert reaction.data["message_id"] == "3" and reaction.actor.username == "friend"

    emojis[0] = {**emojis[0], "emoji": "👍"}  # a changed reaction keeps its client context
    changed = thread(
        "340", [item("3", user_id=11, is_sent_by_viewer=True, reactions={"emojis": emojis})]
    )
    client, _ = instagram([changed])
    (again,) = await DirectSource().poll(client)
    assert again.id == "direct_reaction:3:42:7:1f44d"


async def test_requests_emit_the_newest_message_from_the_sender():
    request = thread("341", [item("9", text="can we talk?"), item("8", text="older")], pending=True)
    silent = thread("342", [item("7", user_id=11, is_sent_by_viewer=True)], pending=True)
    client, _ = instagram([], [request, silent])
    (event,) = await DirectSource().poll(client)
    assert (event.id, event.type, event.text) == (
        "direct_request:341:9",
        "message_request",
        "can we talk?",
    )
    assert event.actor.username == "friend" and event.data["thread_id"] == "341"


def test_direct_is_a_default_source_and_ids_stay_header_safe():
    assert [source.name for source in TriggerOptions().sources] == ["activity", "tagged", "direct"]
    for bad in ("direct:😂", "direct:\n1"):
        with pytest.raises(ValueError):
            Event(bad, "message", "direct", "11")
