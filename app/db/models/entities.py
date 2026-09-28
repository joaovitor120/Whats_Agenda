from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC))
    events: Mapped[list["Event"]] = relationship(back_populates="user")


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC))


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC))


class ConversationState(Base):
    __tablename__ = "conversation_states"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class CalendarConnection(Base):
    __tablename__ = "calendar_connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        Index(
            "uq_active_calendar_user",
            "user_id",
            unique=True,
            postgresql_where=text("active = true"),
            sqlite_where=text("active = 1"),
        ),
    )


class PendingConnection(Base):
    __tablename__ = "pending_connections"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    encrypted_tokens: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    token_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), default="other")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reminder_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reminder_offsets_minutes: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    reminders_exclusive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    provider: Mapped[str] = mapped_column(String(20), default="fake")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendar_connections.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
    user: Mapped[User] = relationship(back_populates="events")
    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    send_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    occurrence_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    event: Mapped[Event] = relationship(back_populates="reminders")
    __table_args__ = (
        UniqueConstraint("event_id", "occurrence_at", "send_at", name="uq_reminder_occurrence"),
    )
