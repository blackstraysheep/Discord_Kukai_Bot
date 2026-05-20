"""In-memory wizard state with 15-minute TTL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


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
    use_existing_channel: bool = False
    existing_channel_id: Optional[int] = None

    # Step 2: Entry
    entry_enabled: bool = True
    entry_approval: bool = False
    min_participants: int = 0

    # Step 3: Schedule
    entry_close_at: Optional[datetime] = None
    submission_close_at: Optional[datetime] = None
    selecting_close_at: Optional[datetime] = None

    # Step 4: Submission
    submission_min: int = 1
    submission_max: Optional[int] = 3
    submission_overflow: bool = False

    # Step 5: Select rule
    select_preset_template_id: Optional[int] = None
    select_preset_name: str = "デフォルト"
    select_points_enabled: bool = True
    select_preset_options: list[dict[str, Any]] = field(default_factory=list)
    select_label_specs: list[dict[str, Any]] = field(default_factory=list)
    selected_select_label: str = ""

    # Step 6: Publish / Result
    submission_mode: str = "manual"
    selecting_mode: str = "manual"
    publish_mode: str = "manual"
    result_mode: str = "manual"
    author_reveal: bool = True
    author_reveal_zero: bool = True

    # Step 7: Voice session
    voice_enabled: bool = False
    voice_channel_id: Optional[int] = None
    voice_start_at: Optional[datetime] = None
    voice_end_at: Optional[datetime] = None

    # Step 8: Notifications
    notification_specs: list[dict[str, Any]] = field(default_factory=list)
    notify_preset_options: list[dict[str, Any]] = field(default_factory=list)

    # Step 1 extras
    category_id: Optional[int] = None
    channel_name: str = ""

    @property
    def is_expired(self) -> bool:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds() > 900

    @property
    def can_confirm(self) -> bool:
        if not (self.title and self.submission_close_at and self.selecting_close_at):
            return False
        if self.entry_enabled and self.entry_close_at is None:
            return False
        if self.use_existing_channel and self.existing_channel_id is None:
            return False
        if self.voice_enabled and not (self.voice_channel_id and self.voice_start_at):
            return False
        return True


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
