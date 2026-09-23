"""Calendar settings and explicit timezone boundaries; source dates stay untouched."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def valid_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Choose a valid timezone, such as America/Los_Angeles.') from None
    return value


Timezone = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(valid_timezone)]


class ScheduleSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timezone: Timezone = 'UTC'
    daily_time: str = Field(default='00:00', pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')

    def instant(self, day):
        local = datetime.combine(day, time.fromisoformat(self.daily_time), ZoneInfo(self.timezone))
        # The first occurrence wins when clocks repeat. A nonexistent local time
        # advances by the spring-forward gap through the UTC round trip.
        return local.replace(fold=0).astimezone(UTC)

    def latest(self, current):
        day = current.astimezone(ZoneInfo(self.timezone)).date()
        if self.instant(day) > current:
            day -= timedelta(days=1)
        return day

    def next_run(self, current):
        day = self.latest(current) + timedelta(days=1)
        return self.instant(day).isoformat()


class ScheduleUpdate(ScheduleSettings):
    revision: int = Field(ge=0)


class DisplayPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timezone: Timezone | None = None


def instant(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def activity_timestamp(record):
    """Date live agent updates by receipt only when no event time was supplied.

    Shared by daily summaries and search. Never assign an import or an explicitly
    timezone-unknown event to its upload day, or modify the original timestamps.
    """
    stamp = instant(record.get('created_at'))
    if not record.get('created_at') and not record.get('historical') and (
            record.get('metadata', {}).get('evidence_type') == 'agent_memory'
            or record.get('source') in {'mcp', 'codex', 'claude'}):
        stamp = instant(record.get('captured_at'))
    return stamp


def date_boundary(value, timezone='UTC'):
    if not value:
        return None
    if len(value) == 10:
        try:
            return datetime.combine(date.fromisoformat(value), time.min, ZoneInfo(timezone)).astimezone(UTC)
        except ValueError:
            pass
    result = instant(value)
    if result is None:
        raise ValueError('Use a calendar date or a timestamp with a timezone offset.')
    return result
