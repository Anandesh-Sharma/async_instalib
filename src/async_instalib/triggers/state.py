"""Seen event IDs per source, saved privately so a restart does not deliver old events."""

import json
import os
import tempfile
from pathlib import Path

from .events import Event


class TriggerState:
    """Remembers the newest ``limit`` event IDs of each source.

    A source with no saved IDs has not been polled yet; its first poll is a baseline.
    """

    def __init__(self, path: Path | None = None, limit: int = 5000) -> None:
        if type(limit) is not int or limit < 100:
            raise ValueError("limit must be an integer of at least 100.")
        self.path = path
        self.limit = limit
        self._seen: dict[str, list[str]] = {}
        if path is not None and path.exists():
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("seen"), dict):
                raise ValueError("Trigger state file is not a saved TriggerState.")
            self._seen = {str(k): [str(i) for i in v] for k, v in data["seen"].items()}

    def started(self, source: str) -> bool:
        """Whether this source has a baseline."""
        return source in self._seen

    def unseen(self, source: str, events: list[Event]) -> list[Event]:
        """Events this source has not delivered before, oldest first."""
        known = set(self._seen.get(source, ()))
        fresh = [event for event in events if event.id not in known]
        return sorted(fresh, key=lambda event: event.occurred_at or 0.0)

    def remember(self, source: str, events: list[Event]) -> None:
        ids = self._seen.setdefault(source, [])
        ids.extend(event.id for event in events if event.id not in ids)
        del ids[: max(0, len(ids) - self.limit)]

    def save(self) -> None:
        """Write atomically with mode 0600; the file names your account's activity."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".triggers-")
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"version": 1, "seen": self._seen}, stream)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
