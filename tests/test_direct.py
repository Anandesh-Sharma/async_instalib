"""Recorded direct inbox reads and thread actions; synthetic IDs only."""

import json
from urllib.parse import parse_qs, parse_qsl

import httpx
import pytest

from async_instalib import ClientOptions, InboxOptions, LoginRequired, Session
from async_instalib.config.policy import RequestPolicy
from async_instalib.exceptions import ProtocolError
from async_instalib.resources.direct import Direct
from async_instalib.resources.direct_common import new_otid
from async_instalib.transport import Transport

THREAD = {
    "thread_id": "340",
    "thread_title": "friend",
    "users": [{"pk": "42", "username": "friend"}],
    "items": [
        {
            "item_id": "9002",
            "user_id": 42,
            "item_type": "text",
            "text": "hi",
            "timestamp": 1793399000981500,
            "client_context": "7522060602617868500",
            "is_sent_by_viewer": False,
        }
    ],
    "last_activity_at": 1793399000981000,
    "is_group": False,
    "muted": False,
    "pending": False,
}


class Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.reply(request) if callable(self.reply) else self.reply
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    def form(self, index=-1):
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode()).items()}


def make_direct(server, authenticated=True):
    session = (
        Session(authorization="Bearer synthetic", user_id="11") if authenticated else Session()
    )
    transport = Transport(
        session, ClientOptions(policy=RequestPolicy(min_interval=0)), httpx.MockTransport(server)
    )
    return Direct(transport), transport


async def test_inbox_reads_threads_with_their_newest_messages():
    server = Recorder({"inbox": {"threads": [THREAD], "pinned_threads": [THREAD]}, "status": "ok"})
    direct, transport = make_direct(server)
    try:
        page = await direct.inbox(InboxOptions(thread_limit=20, message_limit=8))
        query = dict(parse_qsl(server.requests[0].url.query.decode()))
        assert server.requests[0].url.path == "/api/v1/direct_v2/inbox/"
        assert (query["limit"], query["thread_message_limit"]) == ("20", "8")
        assert query["include_old_mrs"] == "false"
        (thread,) = page.items  # pinned and regular copies merge
        assert thread.users[0].username == "friend"
        message = thread.messages[0]
        assert (message.id, message.thread_id, message.user_id) == ("9002", "340", "42")
        assert message.sent_at == pytest.approx(1793399000.9815)
        assert message.client_context == "7522060602617868500"
    finally:
        await transport.aclose()


async def test_requests_parse_the_embedded_thread_json():
    def server(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        variables = json.loads(form["variables"])
        assert form["fb_api_req_friendly_name"] == "IGDirectGetPendingInboxQuery"
        assert variables["folder"] == "PENDING"
        assert variables["client_input_params"]["include_old_mrs"] is True
        node = {"full_json_thread_dict": json.dumps({**THREAD, "pending": True})}
        inbox = {"threads": {"edges": [{"node": node}]}}
        return httpx.Response(200, json={"data": {"ig_direct_get_inbox": inbox}})

    direct, transport = make_direct(Recorder(server))
    try:
        page = await direct.requests(include_older=True)
        assert page.items[0].pending is True and page.items[0].id == "340"
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    "call,path,form",
    [
        (lambda d: d.approve("340"), "/api/v1/direct_v2/threads/340/approve/", {}),
        (
            lambda d: d.hide("340"),
            "/api/v1/direct_v2/threads/340/hide/",
            {"should_move_future_requests_to_spam": "false"},
        ),
        (lambda d: d.mute_calls("340"), "/api/v1/direct_v2/threads/340/mute_video_call/", {}),
        (lambda d: d.unmute_calls("340"), "/api/v1/direct_v2/threads/340/unmute_video_call/", {}),
    ],
)
async def test_thread_forms_use_recorded_routes(call, path, form):
    server = Recorder({"status": "ok"})
    direct, transport = make_direct(server)
    try:
        await call(direct)
        assert server.requests[0].url.path == path
        assert server.form() == {**form, "_uuid": transport.session.device.id}
    finally:
        await transport.aclose()


async def test_mute_and_pin_use_their_mutations():
    def server(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        name = form["fb_api_req_friendly_name"]
        variables = json.loads(form["variables"])
        if name == "IGPinThreadMutation":
            assert request.url.path == "/graphql/query" and variables == {
                "thread_id": "340",
                "pin": True,
            }
            return httpx.Response(
                200, json={"data": {"xdt_pin_thread": {"json_response": '{"status":"ok"}'}}}
            )
        assert name == "IGDirectMuteThread"
        assert variables["ig_thread_igid"] == "340" and variables["mute"] is True
        assert variables["mute_seconds"] == 28800
        return httpx.Response(200, json={"data": {"xig_direct_mute_thread": True}})

    direct, transport = make_direct(Recorder(server))
    try:
        await direct.pin("340")
        await direct.mute("340", 28800)
        with pytest.raises(ValueError):
            await direct.mute("340", 60)
    finally:
        await transport.aclose()


async def test_presence_and_failures():
    server = Recorder(
        {
            "user_presence": {"42": {"is_active": True, "last_activity_at_ms": 1793399172000}},
            "status": "ok",
        }
    )
    direct, transport = make_direct(server)
    try:
        found = await direct.presence(["42"])
        assert found["42"].is_active is True and found["42"].last_active_at == 1793399172.0
        assert json.loads(server.form()["request_data"]) == [42]
        server.reply = {"status": "ok"}
        with pytest.raises(ProtocolError):
            await direct.inbox()
        with pytest.raises(ValueError):
            await direct.approve("../340")
    finally:
        await transport.aclose()
    direct, transport = make_direct(server, authenticated=False)
    try:
        with pytest.raises(LoginRequired):
            await direct.inbox()
    finally:
        await transport.aclose()


def test_offline_threading_ids_carry_the_time_like_the_app():
    identifier = int(new_otid())
    assert identifier >> 22 > 1_700_000_000_000
    assert len({new_otid() for _ in range(100)}) == 100
