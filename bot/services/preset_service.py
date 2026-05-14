"""Preset application service (UI-facing wrapper for select_rule_service)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.services import select_rule_service


@dataclass
class PresetLabelView:
    label: str
    point: int
    min_count: int
    max_count: int | None
    comment_mode: str


@dataclass
class PresetView:
    id: int
    name: str
    is_default: bool
    points_enabled: bool
    labels: list[PresetLabelView]


def _to_view(template) -> PresetView:
    points_enabled, specs = select_rule_service.deserialize_template_payload(template.definition_json)
    labels = [
        PresetLabelView(
            label=spec["label"],
            point=spec["point"],
            min_count=spec.get("min_count", 0),
            max_count=spec.get("max_count"),
            comment_mode=spec.get("comment_mode", "none"),
        )
        for spec in specs
    ]
    return PresetView(
        id=template.id,
        name=template.name,
        is_default=bool(template.is_default),
        points_enabled=points_enabled,
        labels=labels,
    )


async def list_presets(session: AsyncSession, guild_id: int) -> list[PresetView]:
    templates = await select_rule_service.list_templates(session, guild_id)
    return [_to_view(t) for t in templates]


async def get_preset(session: AsyncSession, guild_id: int, preset_id: int) -> PresetView:
    template = await select_rule_service.get_template(session, guild_id, preset_id)
    return _to_view(template)


async def create_preset(
    session: AsyncSession,
    *,
    guild_id: int,
    created_by: int,
    name: str,
    points_enabled: bool = True,
    set_default: bool = False,
) -> PresetView:
    template = await select_rule_service.create_or_update_template(
        session,
        guild_id=guild_id,
        created_by=created_by,
        name=name,
        points_enabled=points_enabled,
        set_default=set_default,
    )
    return _to_view(template)


async def rename_preset(
    session: AsyncSession,
    guild_id: int,
    preset_id: int,
    new_name: str,
) -> PresetView:
    template = await select_rule_service.rename_template(session, guild_id, preset_id, new_name)
    return _to_view(template)


async def delete_preset(session: AsyncSession, guild_id: int, preset_id: int) -> PresetView:
    template = await select_rule_service.get_template(session, guild_id, preset_id)
    view = _to_view(template)
    await select_rule_service.delete_template(session, guild_id, preset_id)
    return view


async def set_preset_points(
    session: AsyncSession,
    guild_id: int,
    preset_id: int,
    points_enabled: bool,
) -> PresetView:
    template = await select_rule_service.set_template_points(
        session, guild_id, preset_id, points_enabled
    )
    return _to_view(template)


async def set_default_preset(
    session: AsyncSession,
    guild_id: int,
    preset_id: int,
) -> PresetView:
    template = await select_rule_service.set_template_default(session, guild_id, preset_id)
    return _to_view(template)


async def upsert_label(
    session: AsyncSession,
    *,
    guild_id: int,
    preset_id: int,
    label_name: str,
    point: int,
) -> PresetView:
    template = await select_rule_service.add_or_update_label(
        session,
        guild_id=guild_id,
        template_id=preset_id,
        label=label_name,
        point=point,
    )
    return _to_view(template)


async def edit_label(
    session: AsyncSession,
    *,
    guild_id: int,
    preset_id: int,
    label_name: str,
    new_name: str | None = None,
    point: int | None = None,
) -> PresetView:
    if new_name is not None:
        template = await select_rule_service.rename_label(
            session,
            guild_id=guild_id,
            template_id=preset_id,
            old_label=label_name,
            new_label=new_name,
            point=point,
        )
    else:
        if point is None:
            raise ValueError("point is required when new_name is None")
        template = await select_rule_service.add_or_update_label(
            session,
            guild_id=guild_id,
            template_id=preset_id,
            label=label_name,
            point=point,
        )
    return _to_view(template)


async def remove_label(
    session: AsyncSession,
    *,
    guild_id: int,
    preset_id: int,
    label_name: str,
) -> PresetView:
    template = await select_rule_service.remove_template_label(
        session,
        guild_id=guild_id,
        template_id=preset_id,
        label=label_name,
    )
    return _to_view(template)


async def replace_labels(
    session: AsyncSession,
    *,
    guild_id: int,
    preset_id: int,
    labels: list[dict[str, object]],
) -> PresetView:
    template = await select_rule_service.get_template(session, guild_id, preset_id)
    points_enabled, _ = select_rule_service.deserialize_template_payload(template.definition_json)

    specs: list[dict[str, object]] = []
    for row in labels:
        label = str(row["label"])
        point = int(row.get("point", 0))
        specs.append(
            {
                "label": label,
                "point": 0 if not points_enabled else point,
                "min_count": int(row.get("min_count", 0)),
                "max_count": row.get("max_count"),
                "comment_mode": str(row.get("comment_mode", "optional")),
            }
        )

    template.definition_json = select_rule_service.serialize_template_specs(
        specs,
        points_enabled=points_enabled,
    )
    await session.flush()
    return _to_view(template)
