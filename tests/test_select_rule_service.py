"""Unit tests for select_rule_service."""

import pytest
from sqlalchemy import select

from bot.models.select_rule import SelectRuleTemplate
from bot.services import select_rule_service
from bot.services.errors import ValidationError


@pytest.mark.asyncio
async def test_add_or_update_template_label(db_session):
    template = await select_rule_service.add_or_update_template_label(
        db_session,
        guild_id=1,
        created_by=100,
        template_name="標準",
        label="特選",
        point=2,
        min_count=0,
        max_count=1,
        comment_mode="none",
        set_default=True,
    )
    assert template.name == "標準"
    assert bool(template.is_default) is True

    # same label -> update
    template = await select_rule_service.add_or_update_template_label(
        db_session,
        guild_id=1,
        created_by=100,
        template_name="標準",
        label="特選",
        point=3,
        min_count=0,
        max_count=1,
        comment_mode="optional",
    )
    specs = select_rule_service.deserialize_template_specs(template.definition_json)
    assert len(specs) == 1
    assert specs[0]["point"] == 3
    assert specs[0]["comment_mode"] == "optional"
    assert specs[0]["rank_priority"] == 1


@pytest.mark.asyncio
async def test_build_kukai_specs_from_template_appends_author_comment(db_session):
    await select_rule_service.add_or_update_template_label(
        db_session,
        guild_id=1,
        created_by=100,
        template_name="二段階",
        label="特選",
        point=2,
        min_count=0,
        max_count=1,
        comment_mode="none",
    )
    await select_rule_service.add_or_update_template_label(
        db_session,
        guild_id=1,
        created_by=100,
        template_name="二段階",
        label="並選",
        point=1,
        min_count=0,
        max_count=3,
        comment_mode="none",
    )

    template = (
        await db_session.execute(
            select(SelectRuleTemplate).where(
                SelectRuleTemplate.guild_id == 1,
                SelectRuleTemplate.name == "二段階",
            )
        )
    ).scalar_one()

    specs = select_rule_service.build_kukai_specs_from_template(template)
    labels = [row["label"] for row in specs]
    assert "特選" in labels
    assert "並選" in labels
    assert "作者コメント" in labels


@pytest.mark.asyncio
async def test_template_rank_priority_is_preserved(db_session):
    template = await select_rule_service.add_or_update_template_label(
        db_session,
        guild_id=1,
        created_by=100,
        template_name="rank指定",
        label="並選",
        point=1,
        min_count=0,
        max_count=3,
        comment_mode="none",
        rank_priority=1,
    )
    template = await select_rule_service.add_or_update_template_label(
        db_session,
        guild_id=1,
        created_by=100,
        template_name="rank指定",
        label="特選",
        point=2,
        min_count=0,
        max_count=1,
        comment_mode="none",
        rank_priority=2,
    )

    specs = select_rule_service.build_kukai_specs_from_template(template)
    ranks = {row["label"]: row["rank_priority"] for row in specs}
    assert ranks["並選"] == 1
    assert ranks["特選"] == 2
    assert ranks["作者コメント"] == 999


def test_normalize_template_specs_rejects_duplicate_rank():
    with pytest.raises(ValidationError):
        select_rule_service.normalize_template_specs(
            [
                {"label": "特選", "point": 2, "rank_priority": 1},
                {"label": "並選", "point": 1, "rank_priority": 1},
            ]
        )


def test_normalize_kukai_specs_requires_non_author():
    with pytest.raises(ValidationError):
        select_rule_service.normalize_kukai_specs(
            [
                {
                    "label": "作者コメント",
                    "point": 0,
                    "min_count": 0,
                    "max_count": None,
                    "comment_mode": "required",
                }
            ]
        )
