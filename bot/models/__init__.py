# Import all models so SQLAlchemy registers them with Base.metadata.
# Alembic and create_all() require this to see every table.
from bot.models.entry import Entry
from bot.models.guild_settings import GuildSettings
from bot.models.kukai import Kukai, KukaiAdmin
from bot.models.notification import NotificationLog, NotificationSchedule
from bot.models.notification_preset import NotificationPreset
from bot.models.participant import KukaiParticipant
from bot.models.submission import PublishedSubmission, Submission
from bot.models.voice_session import VoiceSession
from bot.models.select import OverallSelectComment, Select, SelectComment
from bot.models.select_rule import SelectLabel, SelectRuleTemplate

__all__ = [
    "GuildSettings",
    "Kukai",
    "KukaiAdmin",
    "SelectRuleTemplate",
    "SelectLabel",
    "Entry",
    "Submission",
    "PublishedSubmission",
    "Select",
    "SelectComment",
    "OverallSelectComment",
    "NotificationSchedule",
    "NotificationLog",
    "NotificationPreset",
    "KukaiParticipant",
    "VoiceSession",
]
