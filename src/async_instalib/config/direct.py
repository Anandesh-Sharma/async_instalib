"""Options for reading the direct-message inbox."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboxOptions:
    """How many threads to read and how many newest messages each one includes.

    The app requests 15 threads with 5 messages each; both are the recorded fields.
    """

    thread_limit: int = 15
    message_limit: int = 5

    def __post_init__(self) -> None:
        for value in (self.thread_limit, self.message_limit):
            if type(value) is not int or not 1 <= value <= 100:
                raise ValueError("Inbox limits must be integers from 1 to 100.")
