# Import all models so SQLAlchemy registers them with Base.metadata.
# Alembic and create_all() require this to see every table.
from bot.models.entry import Entry
from bot.models.guild_settings import GuildSettings
from bot.models.kukai import Kukai, KukaiAdmin
from bot.models.notification import NotificationLog, NotificationSchedule
from bot.models.submission import PublishedSubmission, Submission
from bot.models.voice_session import VoiceSession
from bot.models.vote import OverallComment, Vote, VoteComment
from bot.models.vote_rule import VoteLabel, VoteRuleTemplate

__all__ = [
    "GuildSettings",
    "Kukai",
    "KukaiAdmin",
    "VoteRuleTemplate",
    "VoteLabel",
    "Entry",
    "Submission",
    "PublishedSubmission",
    "Vote",
    "VoteComment",
    "OverallComment",
    "NotificationSchedule",
    "NotificationLog",
    "VoiceSession",
]
