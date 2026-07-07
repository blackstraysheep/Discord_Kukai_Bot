"""Tests for pdf_service — TeX generation and escaping (no LuaLaTeX required)."""

from __future__ import annotations

import os

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.pdf_service import (
    PdfError,
    cleanup_temp_pdfs,
    _cleanup_expired_temp_pdfs,
    _extract_pdf_page_count,
    _format_date,
    _render_template,
    _visible_author_ids,
    is_available,
    publish_temp,
    tex_submission_markup,
    tex_tcy_numbers,
    tex_escape,
)


# ---------------------------------------------------------------------------
# tex_escape
# ---------------------------------------------------------------------------

class TestTexEscape:
    def test_plain_text_unchanged(self):
        assert tex_escape("春の海") == "春の海"

    def test_backslash(self):
        assert tex_escape("a\\b") == r"a\textbackslash{}b"

    def test_braces(self):
        assert tex_escape("{a}") == r"\{a\}"

    def test_percent(self):
        assert tex_escape("50%") == r"50\%"

    def test_hash(self):
        assert tex_escape("#1") == r"\#1"

    def test_ampersand(self):
        assert tex_escape("A&B") == r"A\&B"

    def test_underscore(self):
        assert tex_escape("a_b") == r"a\_b"

    def test_dollar(self):
        assert tex_escape("$100") == r"\$100"

    def test_caret(self):
        assert tex_escape("x^2") == r"x\^{}2"

    def test_tilde(self):
        assert tex_escape("~a") == r"\textasciitilde{}a"

    def test_multiple_specials(self):
        result = tex_escape("100% & #1")
        assert r"\%" in result
        assert r"\&" in result
        assert r"\#" in result

    def test_emoji_wrapped_for_pdf_font(self):
        assert tex_escape("春の海🙂") == r"春の海\emoji{🙂}"

    def test_emoji_sequence_wrapped_as_cluster(self):
        assert tex_escape("家族👨‍👩‍👧‍👦") == r"家族\emoji{👨‍👩‍👧‍👦}"

    def test_emoji_with_special_character_still_escapes_tex(self):
        assert tex_escape("#️⃣") == r"\emoji{\#️⃣}"

    def test_tex_tcy_numbers_wraps_digit_runs_after_escaping(self):
        assert tex_tcy_numbers("第10回 2026年5月") == (
            r"第\rensuji{10}回 \rensuji{2026}年\rensuji{5}月"
        )

    def test_tex_submission_markup_converts_ruby(self):
        assert tex_submission_markup("｜遠山（とおやま）") == r"\ruby{遠山}{とおやま}"

    def test_tex_submission_markup_escapes_ruby_parts(self):
        assert tex_submission_markup("｜A&B（50%）") == r"\ruby{A\&B}{50\%}"


def test_extract_pdf_page_count_from_lualatex_log():
    log = "Output written on main.pdf (12 pages, 12345 bytes)."
    assert _extract_pdf_page_count(log) == 12


def test_visible_author_ids_respects_zero_score_setting():
    kukai = SimpleNamespace(author_reveal=True, author_reveal_zero=False)
    results = [
        SimpleNamespace(author_user_id=1, total_score=1),
        SimpleNamespace(author_user_id=2, total_score=0),
        SimpleNamespace(author_user_id=3, total_score=-1),
    ]
    assert _visible_author_ids(kukai, results) == {1}


def test_visible_author_ids_hidden_when_author_unrevealed():
    kukai = SimpleNamespace(author_reveal=False, author_reveal_zero=True)
    results = [SimpleNamespace(author_user_id=1, total_score=10)]
    assert _visible_author_ids(kukai, results) == set()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_false_when_bin_empty(self):
        with patch("bot.services.pdf_service.LUALATEX_BIN", None):
            assert is_available() is False

    def test_false_when_not_in_path(self):
        with patch("bot.services.pdf_service.LUALATEX_BIN", "lualatex"), \
             patch("shutil.which", return_value=None):
            assert is_available() is False

    def test_true_when_found(self):
        with patch("bot.services.pdf_service.LUALATEX_BIN", "lualatex"), \
             patch("shutil.which", return_value="/usr/bin/lualatex"):
            assert is_available() is True


# ---------------------------------------------------------------------------
# temp PDF publishing
# ---------------------------------------------------------------------------

class TestTempPdfPublishing:
    def test_cleanup_expired_temp_pdfs_removes_only_old_files(self, tmp_path):
        old_dir = tmp_path / "1"
        old_dir.mkdir()
        old_pdf = old_dir / "old.pdf"
        old_pdf.write_bytes(b"old")
        new_pdf = old_dir / "new.pdf"
        new_pdf.write_bytes(b"new")

        now = 1_000_000.0
        os.utime(old_pdf, (now - 90_000, now - 90_000))
        os.utime(new_pdf, (now - 10, now - 10))

        removed = _cleanup_expired_temp_pdfs(tmp_path, now=now)

        assert removed == 1
        assert not old_pdf.exists()
        assert new_pdf.exists()

    def test_cleanup_temp_pdfs_uses_configured_directory(self, tmp_path):
        old_pdf = tmp_path / "old.pdf"
        old_pdf.write_bytes(b"old")
        now = 1_000_000.0
        os.utime(old_pdf, (now - 90_000, now - 90_000))

        with patch("bot.services.pdf_service.PDF_SERVE_DIR", tmp_path):
            removed = cleanup_temp_pdfs(now=now)

        assert removed == 1
        assert not old_pdf.exists()

    @pytest.mark.asyncio
    async def test_publish_temp_cleans_expired_files_and_returns_url(self, tmp_path):
        old_dir = tmp_path / "1"
        old_dir.mkdir()
        old_pdf = old_dir / "old.pdf"
        old_pdf.write_bytes(b"old")
        now = 1_000_000.0
        os.utime(old_pdf, (now - 90_000, now - 90_000))

        with patch("bot.services.pdf_service.PDF_SERVE_BASE_URL", "https://example.test/pdfs"), \
             patch("bot.services.pdf_service.PDF_SERVE_DIR", tmp_path), \
             patch("bot.services.pdf_service.time.time", return_value=now), \
             patch("bot.services.pdf_service.secrets.token_hex", return_value="token"):
            url = await publish_temp(b"%PDF", "result_1_named.pdf", 1)

        assert url == "https://example.test/pdfs/1/result_1_named_token.pdf"
        assert not old_pdf.exists()
        assert (tmp_path / "1" / "result_1_named_token.pdf").read_bytes() == b"%PDF"


# ---------------------------------------------------------------------------
# _format_date
# ---------------------------------------------------------------------------

class TestFormatDate:
    def _make_kukai(self, submission_close_at=None, entry_close_at=None):
        k = MagicMock()
        k.submission_close_at = submission_close_at
        k.entry_close_at = entry_close_at
        return k

    def test_returns_empty_when_no_date(self):
        kukai = self._make_kukai()
        assert _format_date(kukai) == ""

    def test_uses_submission_close_at(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
        kukai = self._make_kukai(submission_close_at=dt)
        result = _format_date(kukai)
        assert "2026年" in result
        assert "5月" in result
        assert "16日" in result

    def test_falls_back_to_entry_close_at(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        kukai = self._make_kukai(submission_close_at=None, entry_close_at=dt)
        result = _format_date(kukai)
        assert "2026年" in result


# ---------------------------------------------------------------------------
# _render_template (submission_list)
# ---------------------------------------------------------------------------

class TestRenderSubmissionList:
    def _data(self, **overrides):
        base = {
            "title": "春の句会",
            "kukai_theme": "海",
            "date": "2026年5月16日",
            "submissions": [
                {"number": 1, "text": "春の海ひねもすのたりのたりかな", "author": "芭蕉"},
                {"number": 2, "text": "古池や蛙飛び込む水の音", "author": None},
            ],
        }
        base.update(overrides)
        return base

    def test_title_appears(self):
        tex = _render_template("default", "submission_list.tex.j2", self._data())
        assert "春の句会" in tex

    def test_page_number_footer_appears(self):
        tex = _render_template("default", "submission_list.tex.j2", self._data())
        assert r"\thepage/\@ifundefined{PDFLastPage}{??}{\PDFLastPage}" in tex
        assert r"\input{pdf_page_count.tex}" in tex
        assert r"\raisebox{.3\baselineskip}" in tex
        assert "lastpage" not in tex

    def test_theme_appears(self):
        tex = _render_template("default", "submission_list.tex.j2", self._data())
        assert "海" in tex

    def test_title_and_date_digits_use_tcy(self):
        tex = _render_template(
            "default",
            "submission_list.tex.j2",
            self._data(title="第10回春の句会", date="2026年5月16日"),
        )
        assert r"第\rensuji{10}回春の句会" in tex
        assert r"\rensuji{2026}年\rensuji{5}月\rensuji{16}日" in tex

    def test_haiku_text_appears(self):
        tex = _render_template("default", "submission_list.tex.j2", self._data())
        assert "春の海ひねもすのたりのたりかな" in tex

    def test_author_appears(self):
        tex = _render_template("default", "submission_list.tex.j2", self._data())
        assert "芭蕉" in tex

    def test_no_author_when_none(self):
        data = self._data()
        data["submissions"] = [{"number": 1, "text": "古池や", "author": None}]
        tex = _render_template("default", "submission_list.tex.j2", data)
        assert "古池や" in tex
        assert "芭蕉" not in tex

    def test_special_chars_escaped(self):
        data = self._data()
        data["submissions"] = [
            {"number": 1, "text": "100% の海", "author": "芭蕉 & 蕪村"}
        ]
        tex = _render_template("default", "submission_list.tex.j2", data)
        assert r"100\%" in tex
        assert r"芭蕉 \& 蕪村" in tex

    def test_emoji_font_macro_is_defined(self):
        data = self._data()
        data["submissions"] = [
            {"number": 1, "text": "春の海🙂", "author": "芭蕉😀"}
        ]
        tex = _render_template("default", "submission_list.tex.j2", data)
        assert r"\newcommand{\emoji}" in tex
        assert tex.index("Noto Color Emoji") < tex.index("Segoe UI Emoji")
        assert r"春の海\emoji{🙂}" in tex
        assert r"芭蕉\emoji{😀}" in tex

    def test_ruby_package_and_markup_appear(self):
        data = self._data()
        data["submissions"] = [
            {"number": 1, "text": "｜遠山（とおやま）", "author": None}
        ]
        tex = _render_template("default", "submission_list.tex.j2", data)
        assert r"\usepackage{luatexja-ruby}" in tex
        assert r"\ruby{遠山}{とおやま}" in tex

    def test_no_theme_block_when_empty(self):
        data = self._data(kukai_theme=None)
        tex = _render_template("default", "submission_list.tex.j2", data)
        assert "兼題" not in tex

    def test_invalid_theme_raises(self):
        with pytest.raises(PdfError, match="テーマ"):
            _render_template("nonexistent", "submission_list.tex.j2", self._data())


# ---------------------------------------------------------------------------
# _render_template (result)
# ---------------------------------------------------------------------------

class TestRenderResult:
    def _data(self, **overrides):
        base = {
            "title": "春の句会",
            "kukai_theme": "海",
            "date": "2026年5月16日",
            "results": [
                {
                    "rank": 1,
                    "score": 10,
                    "number": 3,
                    "text": "春の海ひねもすのたりのたりかな",
                    "author": "芭蕉",
                    "label_selects": [
                        {
                            "label": "特選",
                            "point": 3,
                            "count": 1,
                            "all_selectors": ["蕪村"],
                            "comments": [
                                {"author": "蕪村", "text": "大海の広がりが印象的"},
                            ],
                        },
                        {
                            "label": "並選",
                            "point": 1,
                            "count": 2,
                            "all_selectors": ["一茶", "子規"],
                            "comments": [],
                        },
                    ],
                },
            ],
            "overall_comments": [
                {"author": "芭蕉", "text": "今回の句会は海の句が多く良かった。"},
            ],
        }
        base.update(overrides)
        return base

    def test_submission_number_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "No." in tex

    def test_page_number_footer_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert r"\thepage/\@ifundefined{PDFLastPage}{??}{\PDFLastPage}" in tex
        assert r"\input{pdf_page_count.tex}" in tex
        assert r"\pageref{LastPage}" not in tex
        assert "lastpage" not in tex

    def test_haiku_text_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "春の海ひねもすのたりのたりかな" in tex

    def test_label_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "特選" in tex
        assert "並選" in tex

    def test_select_summary_lists_label_point_count_and_selectors_first(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        summary = r"{\gtfamily\small 特選}{\mcfamily\small （\rensuji{ 3 }点）×\rensuji{ 1 }"
        assert summary in tex
        assert "蕪村" in tex
        assert tex.index(summary) < tex.index("大海の広がりが印象的")

    def test_comment_label_is_label_only(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert r"{\leftskip=1\zw{\gtfamily\small 特選}\par}%" in tex

    def test_comment_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "大海の広がりが印象的" in tex

    def test_overall_comment_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "今回の句会は海の句が多く良かった。" in tex
        assert "総評" in tex
        assert r"\hbox{\tate\small ――芭蕉}" in tex

    def test_title_and_date_digits_use_tcy(self):
        tex = _render_template(
            "default",
            "result.tex.j2",
            self._data(title="第10回春の句会", date="2026年5月16日"),
        )
        assert r"第\rensuji{10}回春の句会" in tex
        assert r"\rensuji{2026}年\rensuji{5}月\rensuji{16}日" in tex

    def test_emoji_font_macro_is_defined(self):
        data = self._data()
        data["results"][0]["text"] = "春の海🙂"
        data["results"][0]["author"] = "芭蕉😀"
        data["results"][0]["label_selects"][0]["comments"][0]["text"] = "よい🙂"
        tex = _render_template("default", "result.tex.j2", data)
        assert r"\newcommand{\emoji}" in tex
        assert tex.index("Noto Color Emoji") < tex.index("Segoe UI Emoji")
        assert r"春の海\emoji{🙂}" in tex
        assert r"芭蕉\emoji{😀}" in tex
        assert r"よい\emoji{🙂}" in tex

    def test_ruby_package_and_markup_appear(self):
        data = self._data()
        data["results"][0]["text"] = "｜枯野（かれの）かな"
        tex = _render_template("default", "result.tex.j2", data)
        assert r"\usepackage{luatexja-ruby}" in tex
        assert r"\ruby{枯野}{かれの}かな" in tex

    def test_no_overall_section_when_empty(self):
        data = self._data(overall_comments=[])
        tex = _render_template("default", "result.tex.j2", data)
        assert "{\\gtfamily\\large 総評}" not in tex

    def test_author_hidden_when_none(self):
        data = self._data()
        data["results"][0]["author"] = None
        tex = _render_template("default", "result.tex.j2", data)
        assert "芭蕉" not in tex or "蕪村" in tex  # 蕪村はコメント内なので残る


# ---------------------------------------------------------------------------
# build_submission_pdf / build_result_pdf (compile step mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBuildPdf:
    async def test_build_submission_pdf_calls_compile(self):
        mock_session = AsyncMock()
        mock_kukai = MagicMock()
        mock_kukai.id = 1
        mock_kukai.title = "テスト句会"
        mock_kukai.theme = "海"
        mock_kukai.submission_close_at = None
        mock_kukai.entry_close_at = None

        mock_ps = MagicMock()
        mock_ps.number = 1
        mock_ps.submission.text = "春の海"
        mock_ps.submission.user_id = 100

        with patch("bot.services.pdf_service.submission_repo.list_published",
                   new=AsyncMock(return_value=[mock_ps])), \
             patch("bot.services.pdf_service.entry_repo.list_by_kukai",
                   new=AsyncMock(return_value=[])), \
             patch("bot.services.pdf_service._compile",
                   new=AsyncMock(return_value=b"%PDF-1.4 mock")):

            from bot.services.pdf_service import build_submission_pdf
            result = await build_submission_pdf(
                mock_session, mock_kukai, None,
                show_author=False, theme="default"
            )

        assert result == b"%PDF-1.4 mock"

    async def test_build_submission_pdf_raises_when_no_published(self):
        mock_session = AsyncMock()
        mock_kukai = MagicMock()
        mock_kukai.id = 1

        with patch("bot.services.pdf_service.submission_repo.list_published",
                   new=AsyncMock(return_value=[])):
            from bot.services.pdf_service import build_submission_pdf
            with pytest.raises(PdfError, match="公開されていません"):
                await build_submission_pdf(
                    mock_session, mock_kukai, None,
                    show_author=False, theme="default"
                )
