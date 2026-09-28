from sqlalchemy import DateTime, TypeDecorator

from app.core.time import utc


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Persisted timestamps must be timezone-aware")
        return utc(value)

    def process_result_value(self, value, dialect):
        return utc(value) if value is not None else None
