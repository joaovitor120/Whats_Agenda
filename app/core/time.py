from datetime import UTC, datetime


def utc(value: datetime) -> datetime:
    """SQLite returns naive values; all persisted timestamps represent UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
