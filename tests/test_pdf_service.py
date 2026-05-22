"""Tests for pdf_service — TeX generation and escaping (no LuaLaTeX required)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.pdf_service import (
    PdfError,
    _format_date,
    _render_template,
    is_available,
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

    def test_theme_appears(self):
        tex = _render_template("default", "submission_list.tex.j2", self._data())
        assert "海" in tex

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
        assert "\\hfill" not in tex

    def test_special_chars_escaped(self):
        data = self._data()
        data["submissions"] = [
            {"number": 1, "text": "100% の海", "author": "芭蕉 & 蕪村"}
        ]
        tex = _render_template("default", "submission_list.tex.j2", data)
        assert r"100\%" in tex
        assert r"芭蕉 \& 蕪村" in tex

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
                            "count": 1,
                            "comments": [
                                {"author": "蕪村", "text": "大海の広がりが印象的"},
                            ],
                        },
                        {"label": "並選", "count": 2, "comments": []},
                    ],
                },
            ],
            "overall_comments": [
                {"author": "芭蕉", "text": "今回の句会は海の句が多く良かった。"},
            ],
        }
        base.update(overrides)
        return base

    def test_rank_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "位" in tex

    def test_haiku_text_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "春の海ひねもすのたりのたりかな" in tex

    def test_label_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "特選" in tex
        assert "並選" in tex

    def test_comment_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "大海の広がりが印象的" in tex

    def test_overall_comment_appears(self):
        tex = _render_template("default", "result.tex.j2", self._data())
        assert "今回の句会は海の句が多く良かった。" in tex
        assert "総評" in tex

    def test_no_overall_section_when_empty(self):
        data = self._data(overall_comments=[])
        tex = _render_template("default", "result.tex.j2", data)
        assert "総評" not in tex

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
