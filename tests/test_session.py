import json
import time

import pytest

from async_instalib import Session
from async_instalib.session import SessionCookie


def test_session_round_trip_preserves_identity_and_scoped_cookies(tmp_path):
    session = Session(authorization="Bearer synthetic-secret", user_id="42")
    session.cookies = [
        SessionCookie("same", "first", ".instagram.com", "/", True, int(time.time()) + 1000),
        SessionCookie("same", "second", "i.instagram.com", "/api/v1/", True, None, True),
    ]
    destination = tmp_path / "private" / "session.json"
    session.save(destination)
    restored = Session.load(destination)
    assert restored.device == session.device
    assert restored.cookies == session.cookies
    assert restored.cookies[1].to_cookie().domain_specified is False
    assert restored.authorization == session.authorization
    assert "synthetic-secret" not in repr(restored)
    assert destination.stat().st_mode & 0o777 == 0o600
    destination.chmod(0o644)
    session.save(destination)
    assert destination.stat().st_mode & 0o777 == 0o600


def test_expired_or_foreign_cookie_does_not_mark_session_authenticated():
    session = Session(user_id="42")
    session.cookies = [SessionCookie("sessionid", "expired", ".instagram.com", expires=1)]
    assert not session.authenticated
    session.cookies = [SessionCookie("sessionid", "foreign", "other.invalid")]
    assert not session.authenticated
    session.cookies = [SessionCookie("sessionid", "valid", ".instagram.com")]
    assert session.authenticated


@pytest.mark.parametrize("payload", [[], {}, {"schema_version": 77}, {"schema_version": 1}])
def test_malformed_sessions_are_rejected(tmp_path, payload):
    path = tmp_path / "session.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        Session.load(path)
