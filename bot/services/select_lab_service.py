"""Shared data and edit-all operations for the experimental selection UIs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.select import OverallSelectComment, Select, SelectComment
from bot.models.select_rule import SelectLabel
from bot.repositories import entry_repo, select_repo, submission_repo
from bot.services import select_service
from bot.services.errors import InvalidStateError, ValidationError
from bot.state_machine.states import KukaiState

AUTHOR_COMMENT_LABEL = "作者コメント"
FORM_INPUT_LIMIT = 4000


@dataclass(slots=True)
class SelectLabData:
    submissions: list
    labels: list[SelectLabel]
    selects_by_submission: dict[int, Select]
    overall_comment: str

    @property
    def normal_labels(self) -> list[SelectLabel]:
        return [label for label in self.labels if label.label != AUTHOR_COMMENT_LABEL]

    @property
    def author_label(self) -> SelectLabel:
        return next(label for label in self.labels if label.label == AUTHOR_COMMENT_LABEL)


@dataclass(slots=True)
class SelectFormPayload:
    assignments: dict[int, str]
    comments: dict[int, str]
    author_comments: dict[int, str]
    overall_comment: str


async def ensure_author_label(session: AsyncSession, kukai_id: int) -> SelectLabel:
    result = await session.execute(
        select(SelectLabel).where(
            SelectLabel.kukai_id == kukai_id,
            SelectLabel.label == AUTHOR_COMMENT_LABEL,
        )
    )
    label = result.scalar_one_or_none()
    if label is not None:
        return label
    label = SelectLabel(
        kukai_id=kukai_id,
        template_id=None,
        display_order=999,
        label=AUTHOR_COMMENT_LABEL,
        point=0,
        rank_priority=999,
        min_count=0,
        max_count=None,
        comment_mode="required",
    )
    session.add(label)
    await session.flush()
    return label


async def load_lab_data(
    session: AsyncSession, kukai_id: int, selector_user_id: int
) -> SelectLabData:
    await ensure_author_label(session, kukai_id)
    submissions = await submission_repo.list_published(session, kukai_id)
    result = await session.execute(
        select(SelectLabel)
        .where(SelectLabel.kukai_id == kukai_id)
        .order_by(SelectLabel.display_order, SelectLabel.id)
    )
    labels = list(result.scalars().all())
    selects = await select_repo.get_selects_by_selector(session, kukai_id, selector_user_id)
    overall = await select_repo.get_overall_comment(session, kukai_id, selector_user_id)
    return SelectLabData(
        submissions=submissions,
        labels=labels,
        selects_by_submission={row.submission_id: row for row in selects},
        overall_comment=overall.comment if overall else "",
    )


def escape_form_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def unescape_form_text(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            output.append(char)
            index += 1
            continue
        following = value[index + 1]
        if following == "n":
            output.append("\n")
        elif following == "\\":
            output.append("\\")
        else:
            output.extend(("\\", following))
        index += 2
    return "".join(output)


def _parse_number(value: str, *, line_no: int, field_name: str) -> int:
    try:
        number = int(value.strip())
    except ValueError as exc:
        raise ValidationError(f"{field_name} {line_no}行目: 句番号「{value.strip()}」が不正です。") from exc
    if number < 1:
        raise ValidationError(f"{field_name} {line_no}行目: 句番号は1以上にしてください。")
    return number


def _parse_comment_lines(raw: str, field_name: str) -> dict[int, str]:
    parsed: dict[int, str] = {}
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        left, separator, right = raw_line.partition("=")
        if not separator:
            raise ValidationError(f"{field_name} {line_no}行目: 「番号=本文」で入力してください。")
        number = _parse_number(left, line_no=line_no, field_name=field_name)
        text = unescape_form_text(right).strip()
        if not text:
            raise ValidationError(f"{field_name} {line_no}行目: 本文が空です。")
        if len(text) > 500:
            raise ValidationError(f"{field_name} {line_no}行目: 本文は500文字以内にしてください。")
        if number in parsed:
            raise ValidationError(f"{field_name}: No.{number} が重複しています。")
        parsed[number] = text
    return parsed


def parse_form_payload(
    selections: str,
    comments: str,
    author_comments: str,
    overall_comment: str,
) -> SelectFormPayload:
    if len(overall_comment.strip()) > 2000:
        raise ValidationError("総評は2000文字以内にしてください。")
    assignments: dict[int, str] = {}
    for line_no, raw_line in enumerate(selections.splitlines(), start=1):
        if not raw_line.strip():
            continue
        label_name, separator, raw_numbers = raw_line.partition("=")
        label_name = label_name.strip()
        if not separator or not label_name:
            raise ValidationError(f"選句 {line_no}行目: 「ラベル=1,2」の形式で入力してください。")
        values = [part.strip() for part in raw_numbers.split(",") if part.strip()]
        if not values:
            raise ValidationError(f"選句 {line_no}行目: 句番号を1つ以上指定してください。")
        for value in values:
            number = _parse_number(value, line_no=line_no, field_name="選句")
            if number in assignments:
                raise ValidationError(f"選句: No.{number} が複数のラベルに指定されています。")
            assignments[number] = label_name

    return SelectFormPayload(
        assignments=assignments,
        comments=_parse_comment_lines(comments, "選評"),
        author_comments=_parse_comment_lines(author_comments, "作者コメント"),
        overall_comment=overall_comment.strip(),
    )


def serialize_form_fields(data: SelectLabData, selector_user_id: int) -> tuple[str, str, str, str]:
    number_by_id = {item.submission_id: item.number for item in data.submissions}
    grouped: dict[int, list[int]] = {label.id: [] for label in data.normal_labels}
    comments: list[tuple[int, str]] = []
    author_comments: list[tuple[int, str]] = []
    for submission_id, selected in data.selects_by_submission.items():
        number = number_by_id.get(submission_id)
        if number is None:
            continue
        if selected.is_self_comment:
            if selected.comment:
                author_comments.append((number, selected.comment.comment))
            continue
        grouped.setdefault(selected.select_label_id, []).append(number)
        if selected.comment:
            comments.append((number, selected.comment.comment))

    selection_lines = []
    for label in data.normal_labels:
        numbers = sorted(grouped.get(label.id, []))
        if numbers:
            selection_lines.append(f"{label.label}={','.join(str(number) for number in numbers)}")
    comment_lines = [f"{number}={escape_form_text(text)}" for number, text in sorted(comments)]
    author_lines = [f"{number}={escape_form_text(text)}" for number, text in sorted(author_comments)]
    fields = (
        "\n".join(selection_lines),
        "\n".join(comment_lines),
        "\n".join(author_lines),
        data.overall_comment,
    )
    if any(len(value) > FORM_INPUT_LIMIT for value in fields):
        raise ValidationError(
            "既存内容がフォームの入力上限を超えています。/select-lab review または batch を利用してください。"
        )
    return fields


async def _ensure_can_select(session: AsyncSession, kukai, selector_user_id: int) -> None:
    if KukaiState.from_value(kukai.state) != KukaiState.SELECTING_OPEN:
        raise InvalidStateError("現在選句を受け付けていません。")
    if not kukai.entry_enabled:
        return
    entry = await entry_repo.get_by_user(session, kukai.id, selector_user_id)
    if entry is None or entry.status != "approved":
        raise InvalidStateError("選句にはこの句会への承認済み参加登録が必要です。")


async def clear_overall_comment(
    session: AsyncSession, kukai, selector_user_id: int
) -> None:
    await _ensure_can_select(session, kukai, selector_user_id)
    overall = await select_repo.get_overall_comment(session, kukai.id, selector_user_id)
    if overall is not None:
        await session.delete(overall)


async def replace_from_form(
    session: AsyncSession,
    kukai,
    selector_user_id: int,
    payload: SelectFormPayload,
) -> None:
    await _ensure_can_select(session, kukai, selector_user_id)
    data = await load_lab_data(session, kukai.id, selector_user_id)
    submission_by_number = {item.number: item for item in data.submissions}
    label_by_name = {label.label: label for label in data.normal_labels}

    referenced_numbers = set(payload.assignments) | set(payload.comments) | set(payload.author_comments)
    unknown = sorted(referenced_numbers - set(submission_by_number))
    if unknown:
        raise ValidationError(f"公開番号 No.{unknown[0]} が見つかりません。")

    counts: dict[int, int] = {}
    for number, label_name in payload.assignments.items():
        item = submission_by_number[number]
        if item.submission.user_id == selector_user_id:
            raise ValidationError(f"No.{number} は自句です。通常の選句には指定できません。")
        label = label_by_name.get(label_name)
        if label is None:
            raise ValidationError(f"選句ラベル「{label_name}」が見つかりません。")
        counts[label.id] = counts.get(label.id, 0) + 1
        comment = payload.comments.get(number)
        if label.comment_mode == "required" and not comment:
            raise ValidationError(f"No.{number} の「{label.label}」には選評が必須です。")
        if label.comment_mode == "none" and comment:
            raise ValidationError(f"「{label.label}」には選評を入力できません（No.{number}）。")
    for label in data.normal_labels:
        if label.max_count is not None and counts.get(label.id, 0) > label.max_count:
            raise ValidationError(f"「{label.label}」の上限は{label.max_count}句です。")

    extra_comments = sorted(set(payload.comments) - set(payload.assignments))
    if extra_comments:
        raise ValidationError(f"No.{extra_comments[0]} の選評に対応する選句がありません。")
    for number in payload.author_comments:
        if submission_by_number[number].submission.user_id != selector_user_id:
            raise ValidationError(f"No.{number} は自句ではないため作者コメントを設定できません。")

    select_ids = select(Select.id).where(
        Select.kukai_id == kukai.id, Select.selector_user_id == selector_user_id
    )
    await session.execute(delete(SelectComment).where(SelectComment.select_id.in_(select_ids)))
    await session.execute(
        delete(Select).where(
            Select.kukai_id == kukai.id, Select.selector_user_id == selector_user_id
        )
    )
    overall = await select_repo.get_overall_comment(session, kukai.id, selector_user_id)
    if overall is not None:
        await session.delete(overall)
    await session.flush()

    for number, label_name in sorted(payload.assignments.items()):
        item = submission_by_number[number]
        label = label_by_name[label_name]
        await select_service.cast_select(
            session,
            kukai,
            selector_user_id,
            item.submission_id,
            label.id,
            comment=payload.comments.get(number),
        )
    for number, comment in sorted(payload.author_comments.items()):
        await select_service.cast_select(
            session,
            kukai,
            selector_user_id,
            submission_by_number[number].submission_id,
            data.author_label.id,
            comment=comment,
            is_self_comment=True,
        )
    if payload.overall_comment:
        await select_service.set_overall_comment(
            session, kukai, selector_user_id, payload.overall_comment
        )


async def reconcile_batch_page(
    session: AsyncSession,
    kukai,
    selector_user_id: int,
    label: SelectLabel,
    page_submission_ids: set[int],
    selected_submission_ids: set[int],
) -> None:
    if label.comment_mode == "required":
        raise ValidationError("選評必須ラベルは1句ずつ登録してください。")
    if not selected_submission_ids <= page_submission_ids:
        raise ValidationError("このページにない句が指定されました。")
    data = await load_lab_data(session, kukai.id, selector_user_id)
    page_by_id = {item.submission_id: item for item in data.submissions if item.submission_id in page_submission_ids}
    if any(page_by_id[item_id].submission.user_id == selector_user_id for item_id in selected_submission_ids):
        raise ValidationError("自句は通常の選句に指定できません。")

    for submission_id in page_submission_ids - selected_submission_ids:
        existing = data.selects_by_submission.get(submission_id)
        if existing is not None and not existing.is_self_comment and existing.select_label_id == label.id:
            await select_service.remove_select(session, kukai, selector_user_id, submission_id)
    await session.flush()

    for submission_id in selected_submission_ids:
        existing = data.selects_by_submission.get(submission_id)
        comment = None
        if label.comment_mode == "optional" and existing is not None and existing.comment:
            comment = existing.comment.comment
        await select_service.cast_select(
            session,
            kukai,
            selector_user_id,
            submission_id,
            label.id,
            comment=comment,
        )
