"""Helpers for newline-based bulk command input."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.utils.datetime_utils import parse_datetime


class BulkParseError(ValueError):
    """Raised when bulk command text cannot be parsed."""


@dataclass(frozen=True)
class BulkField:
    key: str
    value: str
    line_no: int


def parse_fields(text: str) -> list[BulkField]:
    fields: list[BulkField] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BulkParseError(f"{line_no}行目: key=value 形式で入力してください。")
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            raise BulkParseError(f"{line_no}行目: key が空です。")
        fields.append(BulkField(key=key, value=value, line_no=line_no))
    if not fields:
        raise BulkParseError("入力が空です。")
    return fields


def first_value(fields: list[BulkField], key: str, default: str | None = None) -> str | None:
    key = key.lower()
    for field in fields:
        if field.key == key:
            return field.value
    return default


def values_for(fields: list[BulkField], key: str) -> list[BulkField]:
    key = key.lower()
    return [field for field in fields if field.key == key]


def reject_unknown_keys(fields: list[BulkField], allowed: set[str]) -> None:
    unknown = [field for field in fields if field.key not in allowed]
    if unknown:
        first = unknown[0]
        raise BulkParseError(f"{first.line_no}行目: 未対応の項目です: {first.key}")


def parse_bool(value: str, *, name: str = "値") -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "t", "yes", "y", "on", "1", "有効"}:
        return True
    if normalized in {"false", "f", "no", "n", "off", "0", "無効"}:
        return False
    raise BulkParseError(f"{name} は true/false, on/off, yes/no, 1/0 で指定してください。")


def parse_int(value: str, *, name: str = "値", min_value: int | None = None) -> int:
    try:
        parsed = int(value.strip())
    except ValueError:
        raise BulkParseError(f"{name} は整数で指定してください。") from None
    if min_value is not None and parsed < min_value:
        raise BulkParseError(f"{name} は{min_value}以上で指定してください。")
    return parsed


def parse_optional_int(
    value: str,
    *,
    name: str = "値",
    min_value: int | None = None,
) -> int | None:
    normalized = value.strip().lower()
    if normalized in {"", "none", "null", "unlimited", "infinity", "inf", "∞"}:
        return None
    return parse_int(value, name=name, min_value=min_value)


def parse_datetime_field(value: str, *, name: str = "日時") -> datetime:
    try:
        return parse_datetime(value)
    except ValueError as exc:
        raise BulkParseError(f"{name}: {exc}") from None


def parse_label_spec(value: str, *, line_no: int | None = None) -> dict[str, object]:
    parts = [part.strip() for part in value.split(",")]
    prefix = f"{line_no}行目: " if line_no is not None else ""
    if len(parts) not in {1, 2, 3, 5, 6}:
        raise BulkParseError(
            f"{prefix}label は `名前,点数,rank,最小数,最大数,コメントモード` "
            "または rank 省略の `名前,点数,最小数,最大数,コメントモード` で指定してください。"
        )

    label = parts[0].strip()
    if not label:
        raise BulkParseError(f"{prefix}ラベル名は空にできません。")

    point = parse_int(parts[1], name=f"{prefix}{label} の点数") if len(parts) >= 2 else 0
    rank_priority: int | None = None
    min_count = 0
    max_count: int | None = None
    comment_mode = "optional"

    if len(parts) == 3:
        rank_priority = parse_int(parts[2], name=f"{prefix}{label} のrank", min_value=1)
    elif len(parts) == 5:
        min_count = parse_int(parts[2], name=f"{prefix}{label} の最小数", min_value=0)
        max_count = parse_optional_int(parts[3], name=f"{prefix}{label} の最大数", min_value=0)
        comment_mode = parts[4].lower()
    elif len(parts) == 6:
        rank_priority = parse_int(parts[2], name=f"{prefix}{label} のrank", min_value=1)
        min_count = parse_int(parts[3], name=f"{prefix}{label} の最小数", min_value=0)
        max_count = parse_optional_int(parts[4], name=f"{prefix}{label} の最大数", min_value=0)
        comment_mode = parts[5].lower()

    if max_count is not None and max_count < min_count:
        raise BulkParseError(f"{prefix}{label} の最大数は最小数以上にしてください。")
    if comment_mode not in {"none", "optional", "required"}:
        raise BulkParseError(f"{prefix}{label} のコメントモードは none/optional/required で指定してください。")

    spec: dict[str, object] = {
        "label": label,
        "point": point,
        "min_count": min_count,
        "max_count": max_count,
        "comment_mode": comment_mode,
    }
    if rank_priority is not None:
        spec["rank_priority"] = rank_priority
    return spec
