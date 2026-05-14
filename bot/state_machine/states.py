from enum import StrEnum


class KukaiState(StrEnum):
    DRAFT = "draft"
    ENTRY_OPEN = "entry_open"
    ENTRY_CLOSED = "entry_closed"
    SUBMISSION_OPEN = "submission_open"
    SUBMISSION_CLOSED = "submission_closed"
    WAITING_PUBLISH = "waiting_publish"
    VOTING_OPEN = "voting_open"
    VOTING_CLOSED = "voting_closed"
    WAITING_RESULTS = "waiting_results"
    RESULTS = "results"
    ENDED = "ended"
    PAUSED = "paused"
    CANCELLED = "cancelled"

    @classmethod
    def active_states(cls) -> set["KukaiState"]:
        """States where participant operations (entry, submit, vote) may be allowed."""
        return {
            cls.ENTRY_OPEN,
            cls.ENTRY_CLOSED,
            cls.SUBMISSION_OPEN,
            cls.SUBMISSION_CLOSED,
            cls.WAITING_PUBLISH,
            cls.VOTING_OPEN,
            cls.VOTING_CLOSED,
            cls.WAITING_RESULTS,
            cls.RESULTS,
        }

    @classmethod
    def terminal_states(cls) -> set["KukaiState"]:
        return {cls.ENDED, cls.CANCELLED}
