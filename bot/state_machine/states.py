from __future__ import annotations

from enum import StrEnum


class KukaiState(StrEnum):
    DRAFT = "draft"
    ENTRY_OPEN = "entry_open"
    ENTRY_CLOSED = "entry_closed"
    SUBMISSION_OPEN = "submission_open"
    SUBMISSION_CLOSED = "submission_closed"
    WAITING_PUBLISH = "waiting_publish"
    SELECTING_OPEN = "selecting_open"
    SELECTING_CLOSED = "selecting_closed"
    WAITING_RESULTS = "waiting_results"
    RESULTS = "results"
    ENDED = "ended"
    PAUSED = "paused"
    CANCELLED = "cancelled"

    @classmethod
    def from_value(cls, raw: str | "KukaiState") -> "KukaiState":
        if isinstance(raw, cls):
            return raw
        return cls(raw)

    @classmethod
    def active_states(cls) -> set["KukaiState"]:
        """States where participant operations (entry, submit, select) may be allowed."""
        return {
            cls.ENTRY_OPEN,
            cls.ENTRY_CLOSED,
            cls.SUBMISSION_OPEN,
            cls.SUBMISSION_CLOSED,
            cls.WAITING_PUBLISH,
            cls.SELECTING_OPEN,
            cls.SELECTING_CLOSED,
            cls.WAITING_RESULTS,
            cls.RESULTS,
        }

    @classmethod
    def terminal_states(cls) -> set["KukaiState"]:
        return {cls.ENDED, cls.CANCELLED}
