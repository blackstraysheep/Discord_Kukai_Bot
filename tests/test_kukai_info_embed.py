"""Tests for /kukai info embed construction."""

from datetime import datetime, timezone
from types import SimpleNamespace

from bot.cogs.kukai_cog import _build_info_embed


def test_info_embed_shows_explicit_voice_session():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    kukai = SimpleNamespace(
        id=1,
        title="テスト句会",
        description=None,
        theme=None,
        state="entry_open",
        submission_min=1,
        submission_max=3,
        entry_enabled=True,
        entry_close_at=now,
        submission_close_at=now,
        selecting_close_at=now,
    )
    voice_session = SimpleNamespace(
        vc_channel_id=123456789,
        start_at=now,
        end_at=None,
    )

    embed = _build_info_embed(kukai, select_labels=[], voice_session=voice_session)

    fields = {field.name: field.value for field in embed.fields}
    assert "ボイス句会" in fields
    assert "<#123456789>" in fields["ボイス句会"]
