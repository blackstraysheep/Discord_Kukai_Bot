from bot.formatters.participation_record_embed_formatter import (
    _chunk_lines,
    build_participation_record_summary_embed,
)
from bot.formatters.participation_record_markdown_exporter import (
    build_participation_record_markdown,
)
from bot.services.participation_record_service import (
    ParticipationRecord,
    ParticipationRecordResult,
    ParticipationSelection,
    ParticipationSelectionGroup,
    ParticipationSubmission,
)


def _record(index: int, *, long_text: str | None = None) -> ParticipationRecord:
    text = long_text or f"投句{index}"
    return ParticipationRecord(
        kukai_id=index,
        guild_id=1,
        channel_id=10,
        result_message_id=20,
        title=f"句会{index}",
        title_url=f"https://example.test/{index}",
        state="results",
        participant_haigo="春風",
        participant_display_name="春風",
        submissions=[ParticipationSubmission(text=text, total_score=3)],
        selections_by_label=[
            ParticipationSelectionGroup(
                label="特選",
                selections=[
                    ParticipationSelection(
                        selected_text=f"選句{index}",
                        author_name="作者名",
                        comment="Embedに載せない選評",
                    )
                ],
            )
        ],
        overall_comment="Embedに載せない総評",
    )


def _result(records: list[ParticipationRecord]) -> ParticipationRecordResult:
    return ParticipationRecordResult(
        target_user_id=1,
        target_display_name="春風",
        scope="current",
        group_by="kukai",
        records=records,
        total_kukai_count=len(records),
        submission_count=sum(len(record.submissions) for record in records),
        selection_count=sum(
            len(group.selections) for record in records for group in record.selections_by_label
        ),
        overall_comment_count=sum(bool(record.overall_comment) for record in records),
    )


def _embed_text(embed) -> str:
    return "\n".join(
        [embed.description or "", *[f"{field.name}\n{field.value}" for field in embed.fields]]
    )


def test_embed_includes_submission_and_selection_but_not_comment_bodies():
    result = _result([_record(1)])

    embed = build_participation_record_summary_embed(
        result,
        guild_names={1: "サーバー"},
        limit=None,
        filename="record.md",
    )
    text = _embed_text(embed)
    markdown = build_participation_record_markdown(result, guild_names={1: "サーバー"})

    assert "投句1（3点）" in text
    assert "【特選】" in text
    assert "選句1（作者名）" in text
    assert "Embedに載せない選評" not in text
    assert "Embedに載せない総評" not in text
    assert "Embedに載せない選評" in markdown
    assert "Embedに載せない総評" in markdown


def test_blank_limit_targets_all_records_and_explicit_limit_reports_remaining():
    result = _result([_record(1), _record(2), _record(3)])

    all_embed = build_participation_record_summary_embed(
        result,
        guild_names={1: "サーバー"},
        limit=None,
        filename="record.md",
    )
    limited_embed = build_participation_record_summary_embed(
        result,
        guild_names={1: "サーバー"},
        limit=1,
        filename="record.md",
    )

    assert "表示対象: 全件" in all_embed.footer.text
    assert "詳細を全表示: 3件" in all_embed.footer.text
    assert "表示対象: 最大1件" in limited_embed.footer.text
    assert "未収録: 2件" in limited_embed.footer.text
    assert "投句2" not in _embed_text(limited_embed)


def test_embed_respects_discord_field_and_total_limits_with_many_records():
    result = _result([_record(index, long_text="長い投句" * 120) for index in range(1, 40)])

    embed = build_participation_record_summary_embed(
        result,
        guild_names={1: "サーバー"},
        limit=None,
        filename="record.md",
    )

    assert len(embed) <= 6000
    assert len(embed.fields) <= 25
    assert all(len(field.name) <= 256 for field in embed.fields)
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert "未収録:" in embed.footer.text


def test_chunk_lines_splits_only_between_lines_and_omits_oversized_line():
    exact = "x" * 1024
    oversized = "y" * 1025

    chunks, omitted = _chunk_lines([exact, "next", oversized])

    assert chunks == [exact, "next"]
    assert omitted is True
