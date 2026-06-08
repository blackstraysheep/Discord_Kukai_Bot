"""PDF generation service using LuaLaTeX + Jinja2 templates."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
import tomllib
from datetime import timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from bot.repositories import entry_repo, participant_repo, select_repo, submission_repo
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
PDF_TEMP_TTL_SECONDS: int = int(os.getenv("PDF_TEMP_TTL_SECONDS", str(24 * 60 * 60)))
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

_VARIATION_SELECTORS = {0xFE0E, 0xFE0F}
_SKIN_TONE_MODIFIERS = range(0x1F3FB, 0x1F400)
_REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)
_TAG_CHARS = range(0xE0020, 0xE0080)


def _is_emoji_base(ch: str) -> bool:
    code = ord(ch)
    return (
        code in {0x00A9, 0x00AE}
        or code in _REGIONAL_INDICATORS
        or 0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
    )


def _is_keycap_start(s: str, index: int) -> bool:
    if s[index] not in "#*0123456789":
        return False
    if index + 1 >= len(s):
        return False
    if ord(s[index + 1]) == 0x20E3:
        return True
    return index + 2 < len(s) and ord(s[index + 1]) == 0xFE0F and ord(s[index + 2]) == 0x20E3


def _is_emoji_continuation(ch: str) -> bool:
    code = ord(ch)
    return (
        code in _VARIATION_SELECTORS
        or code in _SKIN_TONE_MODIFIERS
        or code in _TAG_CHARS
        or code == 0x20E3  # combining enclosing keycap
    )


def _read_emoji_cluster(s: str, start: int) -> tuple[str, int]:
    end = start + 1
    if _is_keycap_start(s, start):
        if ord(s[end]) == 0xFE0F:
            end += 1
        return s[start:end + 1], end + 1
    while end < len(s) and _is_emoji_continuation(s[end]):
        end += 1
    while end + 1 < len(s) and ord(s[end]) == 0x200D and _is_emoji_base(s[end + 1]):
        end += 2
        while end < len(s) and _is_emoji_continuation(s[end]):
            end += 1
    if ord(s[start]) in _REGIONAL_INDICATORS and end < len(s) and ord(s[end]) in _REGIONAL_INDICATORS:
        end += 1
    return s[start:end], end


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
    parts: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if _is_emoji_base(ch) or _is_keycap_start(s, i):
            cluster, i = _read_emoji_cluster(s, i)
            parts.append(r"\emoji{" + cluster.translate(_TEX_ESCAPE) + "}")
            continue
        parts.append(ch.translate(_TEX_ESCAPE))
        i += 1
    return "".join(parts)


def tex_tcy_numbers(s: str) -> str:
    """Escape text and wrap each Arabic numeral run for tategaki."""
    parts: list[str] = []
    for part in re.split(r"([0-9]+)", s):
        if not part:
            continue
        if part.isascii() and part.isdecimal():
            parts.append(r"\rensuji{" + part + "}")
        else:
            parts.append(tex_escape(part))
    return "".join(parts)


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
    env.filters["tex_tcy"] = tex_tcy_numbers
    return env.get_template(template_name).render(**data, theme=theme_cfg)


def _extract_pdf_page_count(log: str) -> int | None:
    match = re.search(r"Output written on .+? \((\d+) pages?", log)
    if not match:
        return None
    return int(match.group(1))


async def _compile(tex_source: str) -> bytes:
    assert LUALATEX_BIN is not None

    async with _get_semaphore():
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = Path(tmpdir) / "main.tex"
            tex_path.write_text(tex_source, encoding="utf-8")
            page_count_path = Path(tmpdir) / "pdf_page_count.tex"
            page_count_path.write_text(r"\gdef\PDFLastPage{??}", encoding="utf-8")

            logger.debug("TeX source:\n%s", tex_source)
            for sty_file in TEMPLATES_DIR.glob("*.sty"):
                shutil.copy(sty_file, tmpdir)

            stdout = b""
            for pass_index in range(2):
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
                if proc.returncode:
                    break
                if pass_index == 0:
                    log = stdout.decode(errors="replace") if stdout else ""
                    page_count = _extract_pdf_page_count(log)
                    if page_count is not None:
                        page_count_path.write_text(
                            rf"\gdef\PDFLastPage{{{page_count}}}",
                            encoding="utf-8",
                        )
                    else:
                        logger.warning("Could not read PDF page count from LuaLaTeX log.")

            pdf_path = Path(tmpdir) / "main.pdf"
            if not pdf_path.exists():
                log = stdout.decode(errors="replace") if stdout else ""
                logger.error("LuaLaTeX compile failed:\n%s", log)
                raise PdfError("PDFのコンパイルに失敗しました。", log=log)

            return pdf_path.read_bytes()


def _display_name(user_id: int, haigo: str | None, guild: discord.Guild | None) -> str:
    if haigo:
        return haigo
    if guild is not None:
        member = guild.get_member(user_id)
        if member:
            return member.display_name
    return f"UID:{user_id}"


async def _build_participant_names(
    session: AsyncSession,
    kukai,
    guild: discord.Guild | None,
) -> tuple[dict[int, str], list[str]]:
    """Return display names for effective PDF participants.

    Entry kukai use the same effective-participant rule as entry counting:
    approval-required kukai count approved entries only; otherwise pending and
    approved entries both count. Non-entry kukai use participant profiles
    recorded through submission/select flows.
    """
    result: dict[int, str] = {}
    names: list[str] = []
    if kukai.entry_enabled:
        statuses = {"approved"} if kukai.entry_approval else {"pending", "approved"}
        entries = await entry_repo.list_by_kukai(session, kukai.id)
        for entry in entries:
            if entry.status not in statuses:
                continue
            name = _display_name(entry.user_id, entry.haigo, guild)
            result[entry.user_id] = name
            names.append(name)
        return result, names

    participants = await participant_repo.list_by_kukai(session, kukai.id)
    for participant in participants:
        name = _display_name(participant.user_id, participant.haigo, guild)
        result[participant.user_id] = name
        names.append(name)
    return result, names


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

    haigo_map, participants = await _build_participant_names(session, kukai, guild)

    data = {
        "title": kukai.title,
        "kukai_theme": kukai.theme,
        "date": _format_date(kukai),
        "participants": participants,
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
    show_reviewer: bool,
    theme: str,
) -> bytes:
    results = await result_service.compute_results(session, kukai)
    overall_comments = await select_repo.list_overall_comments(session, kukai.id)
    haigo_map, participants = await _build_participant_names(session, kukai, guild)

    data = {
        "title": kukai.title,
        "kukai_theme": kukai.theme,
        "date": _format_date(kukai),
        "participants": participants,
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
                        "point": ls.point,
                        "count": ls.count,
                        "comments": [
                            {
                                "author": haigo_map.get(
                                    c.selector_user_id, f"UID:{c.selector_user_id}"
                                ) if show_reviewer else None,
                                "text": c.text,
                            }
                            for c in ls.comments
                        ],
                        "all_selectors": [
                            haigo_map.get(uid, f"UID:{uid}")
                            for uid in ls.selector_user_ids
                        ] if show_reviewer else [],
                    }
                    for ls in r.label_selects
                ],
            }
            for r in results
        ],
        "overall_comments": [
            {
                "author": haigo_map.get(oc.user_id, f"UID:{oc.user_id}") if show_reviewer else None,
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
    _cleanup_expired_temp_pdfs(PDF_SERVE_DIR)
    subdir = PDF_SERVE_DIR / str(kukai_id)
    subdir.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(8)
    stem = Path(filename).stem
    out_path = subdir / f"{stem}_{token}.pdf"
    out_path.write_bytes(pdf_bytes)

    return f"{PDF_SERVE_BASE_URL}/{kukai_id}/{out_path.name}"


def _cleanup_expired_temp_pdfs(base_dir: Path, *, now: float | None = None) -> int:
    """Remove temp PDF files older than the configured TTL."""
    if PDF_TEMP_TTL_SECONDS <= 0 or not base_dir.exists():
        return 0

    cutoff = (time.time() if now is None else now) - PDF_TEMP_TTL_SECONDS
    removed = 0
    for path in base_dir.rglob("*.pdf"):
        try:
            if not path.is_file() or path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            removed += 1
            parent = path.parent
            if parent != base_dir and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            logger.warning("Failed to remove expired temp PDF: %s", path)
    return removed
