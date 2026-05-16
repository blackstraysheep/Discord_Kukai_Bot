"""Export/import helpers for kukai data."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models.entry import Entry
from bot.models.kukai import Kukai, KukaiAdmin
from bot.models.notification import NotificationLog, NotificationSchedule
from bot.models.submission import PublishedSubmission, Submission
from bot.models.select import OverallSelectComment, Select, SelectComment
from bot.models.select_rule import SelectLabel
from bot.models.voice_session import VoiceSession
from bot.services import result_service
from bot.services.errors import InvalidStateError, NotFoundError, ValidationError


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _serialize_results(results: list[result_service.SubmissionResult]) -> list[dict[str, Any]]:
    return [
        {
            "rank": item.rank,
            "number": item.number,
            "text": item.text,
            "author_user_id": item.author_user_id,
            "total_score": item.total_score,
            "label_selects": [
                {
                    "label": lv.label,
                    "point": lv.point,
                    "rank_priority": lv.rank_priority,
                    "count": lv.count,
                    "comments": lv.comments,
                }
                for lv in item.label_selects
            ],
        }
        for item in results
    ]


async def export_payload(
    session: AsyncSession,
    *,
    guild_id: int,
    kukai_id: int | None = None,
) -> dict[str, Any]:
    stmt = (
        select(Kukai)
        .where(Kukai.guild_id == guild_id)
        .options(
            selectinload(Kukai.admins),
            selectinload(Kukai.select_labels),
            selectinload(Kukai.entries),
            selectinload(Kukai.submissions).selectinload(Submission.published),
            selectinload(Kukai.selects).selectinload(Select.comment),
            selectinload(Kukai.overall_comments),
            selectinload(Kukai.notification_schedules).selectinload(NotificationSchedule.logs),
            selectinload(Kukai.voice_session),
        )
    )
    if kukai_id is not None:
        stmt = stmt.where(Kukai.id == kukai_id)

    result = await session.execute(stmt.order_by(Kukai.id))
    kukais = list(result.scalars().all())

    if kukai_id is not None and not kukais:
        raise NotFoundError(f"句会 ID {kukai_id} が見つかりません。")

    bundles: list[dict[str, Any]] = []
    for kukai in kukais:
        try:
            computed = await result_service.compute_results(session, kukai)
            results_data = _serialize_results(computed)
        except InvalidStateError:
            results_data = []

        bundle = {
            "kukai": {
                "id": kukai.id,
                "guild_id": kukai.guild_id,
                "channel_id": kukai.channel_id,
                "title": kukai.title,
                "theme": kukai.theme,
                "description": kukai.description,
                "state": kukai.state,
                "pre_pause_state": kukai.pre_pause_state,
                "created_by": kukai.created_by,
                "entry_open_at": _dt_to_str(kukai.entry_open_at),
                "entry_close_at": _dt_to_str(kukai.entry_close_at),
                "submission_open_at": _dt_to_str(kukai.submission_open_at),
                "submission_close_at": _dt_to_str(kukai.submission_close_at),
                "selecting_open_at": _dt_to_str(kukai.selecting_open_at),
                "selecting_close_at": _dt_to_str(kukai.selecting_close_at),
                "results_at": _dt_to_str(kukai.results_at),
                "entry_enabled": kukai.entry_enabled,
                "entry_approval": kukai.entry_approval,
                "min_participants": kukai.min_participants,
                "min_participants_action": kukai.min_participants_action,
                "submission_min": kukai.submission_min,
                "submission_max": kukai.submission_max,
                "submission_overflow": kukai.submission_overflow,
                "submission_underflow": kukai.submission_underflow,
                "submission_mode": kukai.submission_mode,
                "submission_incomplete": kukai.submission_incomplete,
                "selecting_mode": kukai.selecting_mode,
                "selecting_incomplete": kukai.selecting_incomplete,
                "points_enabled": kukai.points_enabled,
                "publish_mode": kukai.publish_mode,
                "result_mode": kukai.result_mode,
                "author_reveal": kukai.author_reveal,
                "author_reveal_zero": kukai.author_reveal_zero,
                "result_display_default": kukai.result_display_default,
                "notify_channel_id": kukai.notify_channel_id,
                "submission_message_id": kukai.submission_message_id,
                "result_message_id": kukai.result_message_id,
                "created_at": _dt_to_str(kukai.created_at),
                "updated_at": _dt_to_str(kukai.updated_at),
            },
            "admins": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "user_id": row.user_id,
                    "added_by": row.added_by,
                    "created_at": _dt_to_str(row.created_at),
                    "updated_at": _dt_to_str(row.updated_at),
                }
                for row in sorted(kukai.admins, key=lambda x: x.id)
            ],
            "select_labels": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "template_id": row.template_id,
                    "display_order": row.display_order,
                    "label": row.label,
                    "point": row.point,
                    "rank_priority": row.rank_priority,
                    "min_count": row.min_count,
                    "max_count": row.max_count,
                    "comment_mode": row.comment_mode,
                }
                for row in sorted(kukai.select_labels, key=lambda x: x.display_order)
            ],
            "entries": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "user_id": row.user_id,
                    "haigo": row.haigo,
                    "status": row.status,
                    "is_special": row.is_special,
                    "approved_by": row.approved_by,
                    "approved_at": _dt_to_str(row.approved_at),
                    "created_at": _dt_to_str(row.created_at),
                    "updated_at": _dt_to_str(row.updated_at),
                }
                for row in sorted(kukai.entries, key=lambda x: x.id)
            ],
            "submissions": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "user_id": row.user_id,
                    "text": row.text,
                    "is_discarded": row.is_discarded,
                    "created_at": _dt_to_str(row.created_at),
                    "updated_at": _dt_to_str(row.updated_at),
                }
                for row in sorted(kukai.submissions, key=lambda x: x.id)
            ],
            "published_submissions": [
                {
                    "id": row.published.id,
                    "kukai_id": row.published.kukai_id,
                    "submission_id": row.published.submission_id,
                    "number": row.published.number,
                    "published_at": _dt_to_str(row.published.published_at),
                }
                for row in sorted(kukai.submissions, key=lambda x: x.id)
                if row.published is not None
            ],
            "selects": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "selector_user_id": row.selector_user_id,
                    "submission_id": row.submission_id,
                    "select_label_id": row.select_label_id,
                    "is_self_comment": row.is_self_comment,
                    "created_at": _dt_to_str(row.created_at),
                    "updated_at": _dt_to_str(row.updated_at),
                }
                for row in sorted(kukai.selects, key=lambda x: x.id)
            ],
            "select_comments": [
                {
                    "id": row.comment.id,
                    "select_id": row.comment.select_id,
                    "comment": row.comment.comment,
                    "created_at": _dt_to_str(row.comment.created_at),
                    "updated_at": _dt_to_str(row.comment.updated_at),
                }
                for row in sorted(kukai.selects, key=lambda x: x.id)
                if row.comment is not None
            ],
            "overall_comments": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "user_id": row.user_id,
                    "comment": row.comment,
                    "created_at": _dt_to_str(row.created_at),
                    "updated_at": _dt_to_str(row.updated_at),
                }
                for row in sorted(kukai.overall_comments, key=lambda x: x.id)
            ],
            "notification_schedules": [
                {
                    "id": row.id,
                    "kukai_id": row.kukai_id,
                    "event_type": row.event_type,
                    "offset_secs": row.offset_secs,
                    "target": row.target,
                    "channel_id": row.channel_id,
                    "mention": row.mention,
                    "fired": row.fired,
                    "job_id": row.job_id,
                    "created_at": _dt_to_str(row.created_at),
                    "updated_at": _dt_to_str(row.updated_at),
                }
                for row in sorted(kukai.notification_schedules, key=lambda x: x.id)
            ],
            "notification_logs": [
                {
                    "id": log.id,
                    "schedule_id": log.schedule_id,
                    "sent_at": _dt_to_str(log.sent_at),
                    "target_count": log.target_count,
                    "error": log.error,
                }
                for row in sorted(kukai.notification_schedules, key=lambda x: x.id)
                for log in sorted(row.logs, key=lambda x: x.id)
            ],
            "voice_session": (
                {
                    "id": kukai.voice_session.id,
                    "kukai_id": kukai.voice_session.kukai_id,
                    "vc_channel_id": kukai.voice_session.vc_channel_id,
                    "start_at": _dt_to_str(kukai.voice_session.start_at),
                    "end_at": _dt_to_str(kukai.voice_session.end_at),
                    "created_at": _dt_to_str(kukai.voice_session.created_at),
                    "updated_at": _dt_to_str(kukai.voice_session.updated_at),
                }
                if kukai.voice_session is not None
                else None
            ),
            "results": results_data,
        }
        bundles.append(bundle)

    return {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "guild_id": guild_id,
        "kukai_count": len(bundles),
        "kukais": bundles,
    }


def payload_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def payload_to_csv(payload: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["kukai_id", "section", "record_json"])

    for bundle in payload.get("kukais", []):
        kukai = bundle.get("kukai", {})
        kukai_id = kukai.get("id")
        writer.writerow([kukai_id, "kukai", json.dumps(kukai, ensure_ascii=False)])
        for section in (
            "admins",
            "select_labels",
            "entries",
            "submissions",
            "published_submissions",
            "selects",
            "select_comments",
            "overall_comments",
            "notification_schedules",
            "notification_logs",
            "results",
        ):
            for row in bundle.get(section, []):
                writer.writerow([kukai_id, section, json.dumps(row, ensure_ascii=False)])
        if bundle.get("voice_session") is not None:
            writer.writerow(
                [kukai_id, "voice_session", json.dumps(bundle["voice_session"], ensure_ascii=False)]
            )

    return output.getvalue()


def _require_list(value: Any, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{name} は配列である必要があります。")
    if not all(isinstance(item, dict) for item in value):
        raise ValidationError(f"{name} の各要素はオブジェクトである必要があります。")
    return value


async def import_payload(
    session: AsyncSession,
    *,
    guild_id: int,
    payload: dict[str, Any],
) -> list[int]:
    if payload.get("schema_version") != 1:
        raise ValidationError("未対応のschema_versionです。")

    bundles = payload.get("kukais")
    if not isinstance(bundles, list) or not bundles:
        raise ValidationError("インポートデータに句会情報がありません。")

    created_ids: list[int] = []

    for bundle in bundles:
        if not isinstance(bundle, dict):
            raise ValidationError("句会データ形式が不正です。")
        source_kukai = bundle.get("kukai")
        if not isinstance(source_kukai, dict):
            raise ValidationError("kukai オブジェクトが不正です。")
        if source_kukai.get("guild_id") != guild_id:
            raise ValidationError("同一サーバー(guild)のデータのみインポートできます。")

        kukai = Kukai(
            guild_id=guild_id,
            channel_id=source_kukai.get("channel_id"),
            title=source_kukai.get("title") or "Imported Kukai",
            theme=source_kukai.get("theme"),
            description=source_kukai.get("description"),
            state=source_kukai.get("state") or "draft",
            pre_pause_state=source_kukai.get("pre_pause_state"),
            created_by=int(source_kukai.get("created_by")),
            entry_open_at=_str_to_dt(source_kukai.get("entry_open_at")),
            entry_close_at=_str_to_dt(source_kukai.get("entry_close_at")),
            submission_open_at=_str_to_dt(source_kukai.get("submission_open_at")),
            submission_close_at=_str_to_dt(source_kukai.get("submission_close_at")),
            selecting_open_at=_str_to_dt(
                source_kukai.get("selecting_open_at") or source_kukai.get("selecting_open_at")
            ),
            selecting_close_at=_str_to_dt(
                source_kukai.get("selecting_close_at") or source_kukai.get("selecting_close_at")
            ),
            results_at=_str_to_dt(source_kukai.get("results_at")),
            entry_enabled=bool(source_kukai.get("entry_enabled", True)),
            entry_approval=bool(source_kukai.get("entry_approval", False)),
            min_participants=int(source_kukai.get("min_participants", 0)),
            min_participants_action=source_kukai.get("min_participants_action") or "admin",
            submission_min=int(source_kukai.get("submission_min", 1)),
            submission_max=(
                int(source_kukai["submission_max"])
                if source_kukai.get("submission_max") is not None
                else None
            ),
            submission_overflow=bool(source_kukai.get("submission_overflow", False)),
            submission_underflow=bool(source_kukai.get("submission_underflow", False)),
            submission_mode=source_kukai.get("submission_mode") or "manual",
            submission_incomplete=source_kukai.get("submission_incomplete") or "keep",
            selecting_mode=(
                source_kukai.get("selecting_mode")
                or source_kukai.get("selecting_mode")
                or "manual"
            ),
            selecting_incomplete=(
                source_kukai.get("selecting_incomplete")
                or source_kukai.get("selecting_incomplete")
                or "keep"
            ),
            points_enabled=bool(source_kukai.get("points_enabled", True)),
            publish_mode=source_kukai.get("publish_mode") or "manual",
            result_mode=source_kukai.get("result_mode") or "manual",
            author_reveal=bool(source_kukai.get("author_reveal", True)),
            author_reveal_zero=bool(source_kukai.get("author_reveal_zero", True)),
            result_display_default=source_kukai.get("result_display_default") or "score",
            notify_channel_id=source_kukai.get("notify_channel_id"),
            submission_message_id=None,
            result_message_id=None,
        )
        session.add(kukai)
        await session.flush()

        created_ids.append(kukai.id)
        label_id_map: dict[int, int] = {}
        submission_id_map: dict[int, int] = {}
        select_id_map: dict[int, int] = {}
        schedule_id_map: dict[int, int] = {}

        for row in _require_list(bundle.get("admins"), "admins"):
            user_id = int(row["user_id"])
            if user_id == kukai.created_by:
                continue
            session.add(
                KukaiAdmin(
                    kukai_id=kukai.id,
                    user_id=user_id,
                    added_by=int(row.get("added_by", kukai.created_by)),
                )
            )

        for row in _require_list(bundle.get("select_labels"), "select_labels"):
            label = SelectLabel(
                kukai_id=kukai.id,
                template_id=row.get("template_id"),
                display_order=int(row.get("display_order", 1)),
                label=row.get("label") or "ラベル",
                point=int(row.get("point", 0)),
                rank_priority=int(row.get("rank_priority", 1)),
                min_count=int(row.get("min_count", 0)),
                max_count=row.get("max_count"),
                comment_mode=row.get("comment_mode") or "none",
            )
            session.add(label)
            await session.flush()
            label_id_map[int(row["id"])] = label.id

        for row in _require_list(bundle.get("entries"), "entries"):
            session.add(
                Entry(
                    kukai_id=kukai.id,
                    user_id=int(row["user_id"]),
                    haigo=row.get("haigo"),
                    status=row.get("status") or "pending",
                    is_special=bool(row.get("is_special", False)),
                    approved_by=row.get("approved_by"),
                    approved_at=_str_to_dt(row.get("approved_at")),
                )
            )

        for row in _require_list(bundle.get("submissions"), "submissions"):
            submission = Submission(
                kukai_id=kukai.id,
                user_id=int(row["user_id"]),
                text=row.get("text") or "",
                is_discarded=bool(row.get("is_discarded", False)),
            )
            session.add(submission)
            await session.flush()
            submission_id_map[int(row["id"])] = submission.id

        for row in _require_list(bundle.get("published_submissions"), "published_submissions"):
            mapped_submission_id = submission_id_map.get(int(row["submission_id"]))
            if mapped_submission_id is None:
                continue
            session.add(
                PublishedSubmission(
                    kukai_id=kukai.id,
                    submission_id=mapped_submission_id,
                    number=int(row["number"]),
                    published_at=_str_to_dt(row.get("published_at"))
                    or datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )

        for row in _require_list(bundle.get("selects"), "selects"):
            mapped_submission_id = submission_id_map.get(int(row["submission_id"]))
            mapped_label_id = label_id_map.get(int(row["select_label_id"]))
            if mapped_submission_id is None or mapped_label_id is None:
                continue
            sel = Select(
                kukai_id=kukai.id,
                selector_user_id=int(row["selector_user_id"]),
                submission_id=mapped_submission_id,
                select_label_id=mapped_label_id,
                is_self_comment=bool(row.get("is_self_comment", False)),
            )
            session.add(sel)
            await session.flush()
            select_id_map[int(row["id"])] = sel.id

        for row in _require_list(bundle.get("select_comments"), "select_comments"):
            mapped_select_id = select_id_map.get(int(row["select_id"]))
            if mapped_select_id is None:
                continue
            session.add(
                SelectComment(
                    select_id=mapped_select_id,
                    comment=row.get("comment") or "",
                )
            )

        for row in _require_list(bundle.get("overall_comments"), "overall_comments"):
            session.add(
                OverallSelectComment(
                    kukai_id=kukai.id,
                    user_id=int(row["user_id"]),
                    comment=row.get("comment") or "",
                )
            )

        voice_row = bundle.get("voice_session")
        if isinstance(voice_row, dict):
            session.add(
                VoiceSession(
                    kukai_id=kukai.id,
                    vc_channel_id=int(voice_row["vc_channel_id"]),
                    start_at=_str_to_dt(voice_row.get("start_at")),
                    end_at=_str_to_dt(voice_row.get("end_at")),
                )
            )

        for row in _require_list(bundle.get("notification_schedules"), "notification_schedules"):
            schedule = NotificationSchedule(
                kukai_id=kukai.id,
                event_type=row.get("event_type") or "submission_close",
                offset_secs=int(row.get("offset_secs", 86400)),
                target=row.get("target") or "all",
                channel_id=row.get("channel_id"),
                mention=bool(row.get("mention", False)),
                fired=bool(row.get("fired", False)),
                job_id=None,
            )
            session.add(schedule)
            await session.flush()
            schedule_id_map[int(row["id"])] = schedule.id

        for row in _require_list(bundle.get("notification_logs"), "notification_logs"):
            mapped_schedule_id = schedule_id_map.get(int(row["schedule_id"]))
            if mapped_schedule_id is None:
                continue
            session.add(
                NotificationLog(
                    schedule_id=mapped_schedule_id,
                    sent_at=_str_to_dt(row.get("sent_at"))
                    or datetime.now(timezone.utc).replace(tzinfo=None),
                    target_count=int(row.get("target_count", 0)),
                    error=row.get("error"),
                )
            )

    return created_ids
