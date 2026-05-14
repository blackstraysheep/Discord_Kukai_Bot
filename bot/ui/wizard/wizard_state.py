"""In-memory wizard state with 15-minute TTL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class WizardState:
    user_id: int
    guild_id: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    step: int = 1

    # Step 1: Basic
    title: str = ""
    theme: str = ""
    description: str = ""

    # Step 2: Schedule
    submission_close_at: Optional[datetime] = None
    voting_close_at: Optional[datetime] = None

    # Step 3: Entry
    entry_enabled: bool = True
    entry_approval: bool = False
    min_participants: int = 0

    # Step 4: Submission
    submission_min: int = 1
    submission_max: int = 3
    submission_mode: str = "manual"
    submission_overflow: bool = False

    # Step 5: Publish / Result
    publish_mode: str = "manual"
    result_mode: str = "manual"
    author_reveal: bool = True

    @property
    def is_expired(self) -> bool:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds() > 900

    @property
    def can_confirm(self) -> bool:
        return bool(self.title and self.submission_close_at and self.voting_close_at)


_wizards: dict[int, WizardState] = {}


def get_wizard(user_id: int) -> WizardState | None:
    w = _wizards.get(user_id)
    if w is not None and w.is_expired:
        del _wizards[user_id]
        return None
    return w


def set_wizard(state: WizardState) -> None:
    _wizards[state.user_id] = state


def clear_wizard(user_id: int) -> None:
    _wizards.pop(user_id, None)
