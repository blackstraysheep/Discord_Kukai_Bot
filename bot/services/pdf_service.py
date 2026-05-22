"""PDF generation service using LuaLaTeX + Jinja2 templates."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import tempfile
import tomllib
from datetime import timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from bot.repositories import entry_repo, select_repo, submission_repo
from bot.services import result_service
from bot.services.errors import ServiceError

if TYPE_CHECKING:
    import discord

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------

LUALATEX_BIN: str | None = os.getenv("LUALATEX_BIN", "lualatex") or None
DEFAULT_THEME: str = os.getenv("PDF_DEFAULT_THEME", "default")
PDF_MAX_CONCURRENT: int = int(os.getenv("PDF_MAX_CONCURRENT", "2"))
PDF_SERVE_BASE_URL: str = os.getenv("PDF_SERVE_BASE_URL", "").rstrip("/")
PDF_SERVE_DIR: Path = Path(os.getenv("PDF_SERVE_DIR", "/srv/pdfs"))
COMPILE_TIMEOUT: int = int(os.getenv("PDF_COMPILE_TIMEOUT", "60"))

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "pdf"

_JST = timezone(timedelta(hours=9))

_semaphore: asyncio.Semaphore | None = None

# TeX special character escape table
_TEX_ESCAPE = str.maketrans({
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "#": r"\#",
    "&": r"\&",
    "_": r"\_",
    "$": r"\$",
    "^": r"\^{}",
    "~": r"\textasciitilde{}",
})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

class PdfError(ServiceError):
    def __init__(self, message: str, *, log: str = "") -> None:
        super().__init__(message)
        self.log = log


def is_available() -> bool:
    """Return True if LuaLaTeX is configured and reachable."""
    if not LUALATEX_BIN:
        return False
    return shutil.which(LUALATEX_BIN) is not None


def tex_escape(s: str) -> str:
    """Escape TeX special characters in user-supplied text."""
    return s.translate(_TEX_ESCAPE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(PDF_MAX_CONCURRENT)
    return _semaphore


def _load_theme(theme: str) -> dict:
    theme_dir = TEMPLATES_DIR / theme
    if not theme_dir.exists():
        raise PdfError(f"テーマ '{theme}' が見つかりません。")
    with open(theme_dir / "theme.toml", "rb") as f:
        return tomllib.load(f)


def _render_template(theme: str, template_name: str, data: dict) -> str:
    theme_cfg = _load_theme(theme)
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR / theme)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.filters["tex"] = tex_escape
    return env.get_template(template_name).render(**data, theme=theme_cfg)


async def _compile(tex_source: str) -> bytes:
    assert LUALATEX_BIN is not None

    async with _get_semaphore():
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = Path(tmpdir) / "main.tex"
            tex_path.write_text(tex_source, encoding="utf-8")

            logger.debug("TeX source:\n%s", tex_source)
            for sty_file in TEMPLATES_DIR.glob("*.sty"):
                shutil.copy(sty_file, tmpdir)

            proc = await asyncio.create_subprocess_exec(
                LUALATEX_BIN,
                "--interaction=nonstopmode",
                "--halt-on-error",
                "main.tex",
                cwd=tmpdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=COMPILE_TIMEOUT
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise PdfError("PDFコンパイルがタイムアウトしました。")

            pdf_path = Path(tmpdir) / "main.pdf"
            if not pdf_path.exists():
                log = stdout.decode(errors="replace") if stdout else ""
                logger.error("LuaLaTeX compile failed:\n%s", log)
                raise PdfError("PDFのコンパイルに失敗しました。", log=log)

            return pdf_path.read_bytes()


async def _build_haigo_map(
    session: AsyncSession,
    kukai_id: int,
    guild: discord.Guild | None,
) -> dict[int, str]:
    entries = await entry_repo.list_by_kukai(session, kukai_id)
    result: dict[int, str] = {}
    for e in entries:
        if e.haigo:
            result[e.user_id] = e.haigo
        elif guild is not None:
            member = guild.get_member(e.user_id)
            result[e.user_id] = member.display_name if member else f"UID:{e.user_id}"
        else:
            result[e.user_id] = f"UID:{e.user_id}"
    return result


def _format_date(kukai) -> str:
    dt = kukai.submission_close_at or kukai.entry_close_at
    if dt is None:
        return ""
    dt_jst = dt.astimezone(_JST)
    return f"{dt_jst.year}年{dt_jst.month}月{dt_jst.day}日"


# ---------------------------------------------------------------------------
# Public PDF builders
# ---------------------------------------------------------------------------

async def build_submission_pdf(
    session: AsyncSession,
    kukai,
    guild: discord.Guild | None,
    *,
    show_author: bool,
    theme: str,
) -> bytes:
    published = await submission_repo.list_published(session, kukai.id)
    if not published:
        raise PdfError("投句一覧がまだ公開されていません。")

    haigo_map = await _build_haigo_map(session, kukai.id, guild)

    data = {
        "title": kukai.title,
        "kukai_theme": kukai.theme,
        "date": _format_date(kukai),
        "participants": list(haigo_map.values()),
        "submissions": [
            {
                "number": ps.number,
                "text": ps.submission.text,
                "author": haigo_map.get(ps.submission.user_id) if show_author else None,
            }
            for ps in published
        ],
    }
    tex = _render_template(theme, "submission_list.tex.j2", data)
    return await _compile(tex)


async def build_result_pdf(
    session: AsyncSession,
    kukai,
    guild: discord.Guild | None,
    *,
    show_author: bool,
    theme: str,
) -> bytes:
    results = await result_service.compute_results(session, kukai)
    overall_comments = await select_repo.list_overall_comments(session, kukai.id)
    haigo_map = await _build_haigo_map(session, kukai.id, guild) if show_author else {}

    data = {
        "title": kukai.title,
        "kukai_theme": kukai.theme,
        "date": _format_date(kukai),
        "results": [
            {
                "rank": r.rank,
                "score": r.total_score,
                "number": r.number,
                "text": r.text,
                "author": haigo_map.get(r.author_user_id) if show_author else None,
                "label_selects": [
                    {
                        "label": ls.label,
                        "count": ls.count,
                        "comments": [
                            {
                                "author": haigo_map.get(
                                    c.selector_user_id, f"UID:{c.selector_user_id}"
                                ),
                                "text": c.text,
                            }
                            for c in ls.comments
                        ],
                    }
                    for ls in r.label_selects
                ],
            }
            for r in results
        ],
        "overall_comments": [
            {
                "author": haigo_map.get(oc.user_id, f"UID:{oc.user_id}"),
                "text": oc.comment,
            }
            for oc in overall_comments
        ],
    }
    tex = _render_template(theme, "result.tex.j2", data)
    return await _compile(tex)


async def publish_temp(pdf_bytes: bytes, filename: str, kukai_id: int) -> str:
    """Save PDF to the temp serve directory and return its public URL."""
    if not PDF_SERVE_BASE_URL:
        raise PdfError(
            "PDF_SERVE_BASE_URL が設定されていないため一時URLを発行できません。"
        )
    subdir = PDF_SERVE_DIR / str(kukai_id)
    subdir.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(8)
    stem = Path(filename).stem
    out_path = subdir / f"{stem}_{token}.pdf"
    out_path.write_bytes(pdf_bytes)

    return f"{PDF_SERVE_BASE_URL}/{kukai_id}/{out_path.name}"
