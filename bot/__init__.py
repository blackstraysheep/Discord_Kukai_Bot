"""Text processing utilities.

normalize()    — NFC Unicode normalization applied on every save.
discord_safe() — Markdown/mention escaping applied only at display time.
                 The original DB value is never modified.
"""

import re
import unicodedata

# Discord Markdown special characters to escape
_MD_ESCAPE = re.compile(r"([\\*_~|>`\[\]()#])")

# Mentions that would ping users/roles if left unescaped
_MENTION_PATTERNS = re.compile(r"(@(?:everyone|here))|(<[@#][!&]?\d+>)")


def normalize(text: str) -> str:
    """Apply NFC normalization. Call this before persisting user input."""
    return unicodedata.normalize("NFC", text)


def discord_safe(text: str) -> str:
    """Escape Markdown and neutralize mentions for Discord display.

    Inserts a zero-width space after '@' in @everyone / @here and inside
    user/channel mention syntax so Discord does not render them as pings.
    Does NOT modify the source; only call this when building a message.
    """
    # Escape Markdown characters
    escaped = _MD_ESCAPE.sub(r"\\\1", text)
    # Neutralize @everyone, @here, and <@id> / <#id> mentions
    escaped = _MENTION_PATTERNS.sub(_neutralize_mention, escaped)
    return escaped


def _neutralize_mention(match: re.Match) -> str:
    s = match.group(0)
    # Insert zero-width space after '@' or '<'
    if s.startswith("@"):
        return "@​" + s[1:]
    return "<​" + s[1:]
