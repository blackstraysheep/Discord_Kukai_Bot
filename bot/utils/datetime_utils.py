"""Datetime parsing helpers (JST-aware)."""

import re
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")

_OFFSET_PATTERN = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%m/%d %H:%M",
]


def parse_datetime(text: str) -> datetime:
    """Parse a datetime string into a UTC-naive datetime (stored as UTC internally).

    Accepts formats like '2026-05-20 23:59' and treats them as JST.
    Raises ValueError on failure.
    """
    text = text.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            # Treat input as JST, convert to UTC-naive
            if dt.year == 1900:
                now = datetime.now(JST)
                dt = dt.replace(year=now.year)
            jst_dt = dt.replace(tzinfo=JST)
            return jst_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError(f"日時形式が正しくありません: '{text}'\n例: 2026-05-20 23:59")


def parse_offset(text: str) -> int:
    """Parse an offset string like '24h', '30m', '1d6h' into total seconds."""
    text = text.strip().lower()
    m = _OFFSET_PATTERN.match(text)
    if not m or not any(m.groups()):
        raise ValueError(f"オフセット形式が正しくありません: '{text}'\n例: 24h, 30m, 1d6h")
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError("オフセットは1秒以上にしてください。")
    return total


def to_jst(dt: datetime) -> datetime:
    """Convert a UTC-naive datetime (stored as UTC) to JST for display."""
    return dt.replace(tzinfo=timezone.utc).astimezone(JST)


def format_jst(dt: datetime) -> str:
    """Format a UTC-naive datetime as 'YYYY-MM-DD HH:MM (JST)'."""
    return to_jst(dt).strftime("%Y-%m-%d %H:%M (JST)")
