"""Unit tests for preset_service."""

import pytest

from bot.services import preset_service


@pytest.mark.asyncio
async def test_create_and_list_presets(db_session):
    await preset_service.create_preset(
        db_session,
        guild_id=1,
        created_by=100,
        name="標準",
        points_enabled=True,
        set_default=False,
    )

    presets = await preset_service.list_presets(db_session, guild_id=1)
    assert len(presets) == 1
    assert presets[0].name == "標準"
    assert presets[0].points_enabled is True
    assert presets[0].labels == []


@pytest.mark.asyncio
async def test_set_default_preset_exclusive(db_session):
    p1 = await preset_service.create_preset(
        db_session,
        guild_id=1,
        created_by=100,
        name="A",
        points_enabled=True,
        set_default=False,
    )
    p2 = await preset_service.create_preset(
        db_session,
        guild_id=1,
        created_by=100,
        name="B",
        points_enabled=True,
        set_default=False,
    )

    updated = await preset_service.set_default_preset(db_session, guild_id=1, preset_id=p2.id)
    assert updated.id == p2.id
    assert updated.is_default is True

    presets = await preset_service.list_presets(db_session, guild_id=1)
    default_ids = [p.id for p in presets if p.is_default]
    assert default_ids == [p2.id]
    assert p1.id != p2.id


@pytest.mark.asyncio
async def test_replace_labels_with_optional_fields(db_session):
    preset = await preset_service.create_preset(
        db_session,
        guild_id=1,
        created_by=100,
        name="詳細設定",
        points_enabled=True,
        set_default=False,
    )
    updated = await preset_service.replace_labels(
        db_session,
        guild_id=1,
        preset_id=preset.id,
        labels=[
            {
                "label": "特選",
                "point": 2,
                "min_count": 1,
                "max_count": 2,
                "comment_mode": "required",
            },
            {
                "label": "並選",
                "point": 1,
                "min_count": 0,
                "max_count": None,
                "comment_mode": "optional",
            },
        ],
    )
    assert len(updated.labels) == 2
    first = updated.labels[0]
    assert first.label == "特選"
    assert first.point == 2
    assert first.min_count == 1
    assert first.max_count == 2
    assert first.comment_mode == "required"


@pytest.mark.asyncio
async def test_replace_labels_points_off_forces_zero(db_session):
    preset = await preset_service.create_preset(
        db_session,
        guild_id=1,
        created_by=100,
        name="点数なし",
        points_enabled=False,
        set_default=False,
    )
    updated = await preset_service.replace_labels(
        db_session,
        guild_id=1,
        preset_id=preset.id,
        labels=[
            {"label": "特選", "point": 9, "min_count": 0, "max_count": 1, "comment_mode": "none"},
        ],
    )
    assert len(updated.labels) == 1
    assert updated.labels[0].point == 0


@pytest.mark.asyncio
async def test_replace_labels_default_comment_mode_optional(db_session):
    preset = await preset_service.create_preset(
        db_session,
        guild_id=1,
        created_by=100,
        name="コメント既定",
        points_enabled=True,
        set_default=False,
    )
    updated = await preset_service.replace_labels(
        db_session,
        guild_id=1,
        preset_id=preset.id,
        labels=[
            {"label": "特選", "point": 2},
        ],
    )
    assert len(updated.labels) == 1
    assert updated.labels[0].comment_mode == "optional"
