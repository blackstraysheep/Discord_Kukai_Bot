"""Select rule preset (選句プリセット) service."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.select_rule import SelectRuleTemplate
from bot.services.errors import NotFoundError, ValidationError

AUTHOR_COMMENT_LABEL = "作者コメント"
COMMENT_MODES = {"none", "optional", "required"}

DEFAULT_SELECT_LABEL_SPECS: list[dict[str, Any]] = [
    {
        "label": "特選",
        "point": 2,
        "rank_priority": 1,
        "display_order": 1,
        "min_count": 0,
        "max_count": 1,
        "comment_mode": "none",
        "template_id": None,
    },
    {
        "label": "並選",
        "point": 1,
        "rank_priority": 2,
        "display_order": 2,
        "min_count": 0,
        "max_count": 5,
        "comment_mode": "none",
        "template_id": None,
    },
    {
        "label": "予選",
        "point": 0,
        "rank_priority": 3,
        "display_order": 3,
        "min_count": 0,
        "max_count": None,
        "comment_mode": "none",
        "template_id": None,
    },
    {
        "label": AUTHOR_COMMENT_LABEL,
        "point": 0,
        "rank_priority": 999,
        "display_order": 999,
        "min_count": 0,
        "max_count": None,
        "comment_mode": "required",
        "template_id": None,
    },
]


def default_kukai_specs() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_SELECT_LABEL_SPECS)


def _normalize_common(
    specs: list[dict[str, Any]],
    *,
    ensure_author_comment: bool,
    template_id: int | None = None,
) -> list[dict[str, Any]]:
    if not specs:
        raise ValidationError("選句種別が1件もありません。")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in specs:
        label = str(raw.get("label", "")).strip()
        if not label:
            raise ValidationError("選句種別名は空にできません。")
        if len(label) > 50:
            raise ValidationError("選句種別名は50文字以内にしてください。")
        if label in seen:
            raise ValidationError(f"選句種別「{label}」が重複しています。")
        seen.add(label)

        try:
            point = int(raw.get("point", 0))
        except (TypeError, ValueError):
            raise ValidationError(f"「{label}」の点数は整数で指定してください。") from None
        try:
            min_count = int(raw.get("min_count", 0))
        except (TypeError, ValueError):
            raise ValidationError(f"「{label}」の最小選句数は整数で指定してください。") from None

        max_count_raw = raw.get("max_count")
        if max_count_raw in (None, ""):
            max_count = None
        else:
            try:
                max_count = int(max_count_raw)
            except (TypeError, ValueError):
                raise ValidationError(f"「{label}」の最大選句数は整数または空にしてください。") from None

        if min_count < 0:
            raise ValidationError(f"「{label}」の最小選句数は0以上にしてください。")
        if max_count is not None and max_count < min_count:
            raise ValidationError(f"「{label}」の最大選句数は最小選句数以上にしてください。")

        comment_mode = str(raw.get("comment_mode", "none")).strip().lower()
        if comment_mode not in COMMENT_MODES:
            raise ValidationError(
                f"「{label}」のcomment_modeは none/optional/required のいずれかにしてください。"
            )

        spec = {
            "label": label,
            "point": point,
            "min_count": min_count,
            "max_count": max_count,
            "comment_mode": comment_mode,
            "template_id": template_id if template_id is not None else raw.get("template_id"),
        }
        normalized.append(spec)

    non_author = [s for s in normalized if s["label"] != AUTHOR_COMMENT_LABEL]
    if not non_author:
        raise ValidationError("作者コメント以外の選句種別を1件以上設定してください。")

    author_specs = [s for s in normalized if s["label"] == AUTHOR_COMMENT_LABEL]
    if len(author_specs) > 1:
        raise ValidationError("作者コメントは1件のみ設定できます。")

    if ensure_author_comment and not author_specs:
        author_specs = [
            {
                "label": AUTHOR_COMMENT_LABEL,
                "point": 0,
                "min_count": 0,
                "max_count": None,
                "comment_mode": "required",
                "template_id": template_id,
            }
        ]

    if author_specs:
        author = author_specs[0]
        author["point"] = 0
        author["min_count"] = 0
        author["max_count"] = None
        author["comment_mode"] = "required"

    ordered = non_author + author_specs
    for i, spec in enumerate(ordered, start=1):
        if spec["label"] == AUTHOR_COMMENT_LABEL:
            spec["display_order"] = 999
            spec["rank_priority"] = 999
        else:
            spec["display_order"] = i
            spec["rank_priority"] = i
    return ordered


def normalize_kukai_specs(
    specs: list[dict[str, Any]],
    *,
    template_id: int | None = None,
) -> list[dict[str, Any]]:
    return _normalize_common(specs, ensure_author_comment=True, template_id=template_id)


def normalize_template_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not specs:
        return []
    normalized = _normalize_common(specs, ensure_author_comment=False, template_id=None)
    return [s for s in normalized if s["label"] != AUTHOR_COMMENT_LABEL]


def serialize_template_specs(
    specs: list[dict[str, Any]],
    *,
    points_enabled: bool = True,
) -> str:
    compact = []
    for spec in normalize_template_specs(specs):
        compact.append(
            {
                "label": spec["label"],
                "point": spec["point"],
                "min_count": spec["min_count"],
                "max_count": spec["max_count"],
                "comment_mode": spec["comment_mode"],
            }
        )
    return json.dumps(
        {"points_enabled": bool(points_enabled), "labels": compact},
        ensure_ascii=False,
    )


def deserialize_template_payload(definition_json: str | None) -> tuple[bool, list[dict[str, Any]]]:
    if not definition_json:
        return True, []
    try:
        raw = json.loads(definition_json)
    except json.JSONDecodeError:
        return True, []

    if isinstance(raw, list):
        specs: list[dict[str, Any]] = [item for item in raw if isinstance(item, dict)]
        return True, normalize_template_specs(specs)

    if not isinstance(raw, dict):
        return True, []
    points_enabled = bool(raw.get("points_enabled", True))
    labels_raw = raw.get("labels", [])
    if not isinstance(labels_raw, list):
        return points_enabled, []
    specs = [item for item in labels_raw if isinstance(item, dict)]
    normalized = normalize_template_specs(specs)
    if not points_enabled:
        for spec in normalized:
            spec["point"] = 0
    return points_enabled, normalized


def deserialize_template_specs(definition_json: str | None) -> list[dict[str, Any]]:
    _, specs = deserialize_template_payload(definition_json)
    return specs


def build_kukai_specs_from_template(template: SelectRuleTemplate) -> list[dict[str, Any]]:
    points_enabled, specs = deserialize_template_payload(template.definition_json)
    if not specs:
        specs = [s for s in default_kukai_specs() if s["label"] != AUTHOR_COMMENT_LABEL]
    if not points_enabled:
        for spec in specs:
            spec["point"] = 0
    return normalize_kukai_specs(specs, template_id=template.id)


async def list_templates(session: AsyncSession, guild_id: int) -> list[SelectRuleTemplate]:
    result = await session.execute(
        select(SelectRuleTemplate)
        .where(SelectRuleTemplate.guild_id == guild_id)
        .order_by(SelectRuleTemplate.is_default.desc(), SelectRuleTemplate.name.asc())
    )
    return list(result.scalars().all())


async def get_template(
    session: AsyncSession, guild_id: int, template_id: int
) -> SelectRuleTemplate:
    template = await session.get(SelectRuleTemplate, template_id)
    if not template or template.guild_id != guild_id:
        raise NotFoundError("選句プリセットが見つかりません。")
    return template


async def add_or_update_template_label(
    session: AsyncSession,
    *,
    guild_id: int,
    created_by: int,
    template_name: str,
    label: str,
    point: int,
    min_count: int,
    max_count: int | None,
    comment_mode: str,
    set_default: bool = False,
) -> SelectRuleTemplate:
    template_name = template_name.strip()
    if not template_name:
        raise ValidationError("プリセット名は必須です。")
    if len(template_name) > 100:
        raise ValidationError("プリセット名は100文字以内にしてください。")
    if label.strip() == AUTHOR_COMMENT_LABEL:
        raise ValidationError("「作者コメント」は予約済みのためプリセット登録できません。")

    result = await session.execute(
        select(SelectRuleTemplate).where(
            SelectRuleTemplate.guild_id == guild_id,
            SelectRuleTemplate.name == template_name,
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        template = SelectRuleTemplate(
            guild_id=guild_id,
            name=template_name,
            created_by=created_by,
            is_default=1 if set_default else 0,
            definition_json="[]",
        )
        session.add(template)
        await session.flush()

    points_enabled, specs = deserialize_template_payload(template.definition_json)
    updated = False
    for spec in specs:
        if spec["label"] == label.strip():
            spec["point"] = point
            spec["min_count"] = min_count
            spec["max_count"] = max_count
            spec["comment_mode"] = comment_mode
            updated = True
            break
    if not updated:
        specs.append(
            {
                "label": label.strip(),
                "point": point,
                "min_count": min_count,
                "max_count": max_count,
                "comment_mode": comment_mode,
            }
        )

    if not points_enabled:
        for spec in specs:
            spec["point"] = 0
    template.definition_json = serialize_template_specs(specs, points_enabled=points_enabled)
    if set_default:
        defaults = await list_templates(session, guild_id)
        for row in defaults:
            row.is_default = 1 if row.id == template.id else 0
    await session.flush()
    return template


async def create_or_update_template(
    session: AsyncSession,
    *,
    guild_id: int,
    created_by: int,
    name: str,
    points_enabled: bool = True,
    set_default: bool = False,
) -> SelectRuleTemplate:
    name = name.strip()
    if not name:
        raise ValidationError("プリセット名は必須です。")
    if len(name) > 100:
        raise ValidationError("プリセット名は100文字以内にしてください。")

    result = await session.execute(
        select(SelectRuleTemplate).where(
            SelectRuleTemplate.guild_id == guild_id,
            SelectRuleTemplate.name == name,
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        template = SelectRuleTemplate(
            guild_id=guild_id,
            name=name,
            created_by=created_by,
            is_default=1 if set_default else 0,
            definition_json=serialize_template_specs([], points_enabled=points_enabled),
        )
        session.add(template)
    else:
        _, specs = deserialize_template_payload(template.definition_json)
        template.definition_json = serialize_template_specs(specs, points_enabled=points_enabled)
        if set_default:
            template.is_default = 1

    await session.flush()
    if set_default:
        defaults = await list_templates(session, guild_id)
        for row in defaults:
            row.is_default = 1 if row.id == template.id else 0
    await session.flush()
    return template


async def delete_template(session: AsyncSession, guild_id: int, template_id: int) -> None:
    template = await get_template(session, guild_id, template_id)
    await session.delete(template)


async def remove_template_label(
    session: AsyncSession,
    *,
    guild_id: int,
    template_id: int,
    label: str,
) -> SelectRuleTemplate:
    template = await get_template(session, guild_id, template_id)
    target_label = label.strip()
    points_enabled, specs = deserialize_template_payload(template.definition_json)
    remaining = [spec for spec in specs if spec["label"] != target_label]
    if len(remaining) == len(specs):
        raise NotFoundError("指定したラベルが見つかりません。")
    template.definition_json = serialize_template_specs(remaining, points_enabled=points_enabled)
    await session.flush()
    return template
