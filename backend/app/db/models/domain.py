from __future__ import annotations

from datetime import date, datetime, time, timezone
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for ORM defaults."""
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PROMISED = "Promised"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    BLOCKED = "Blocked"


class DecisionState(str, Enum):
    APPROVED = "Approved"
    CONTESTED = "Contested"
    OPEN_QUESTION = "Open Question"


def enum_values(enum_class: type[Enum]) -> list[str]:
    """Persist the public enum values instead of Python member identifiers."""
    return [str(member.value) for member in enum_class]


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_meetings_title_nonempty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    recording_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_segments: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    speaker_identities: Mapped[dict[str, dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    diarization_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_configured"
    )
    executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_topics: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    timeline: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    risks: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    behavioral_metrics: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    mom_recipients: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    mom_delivery_report: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    mom_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    action_items: Mapped[list[ActionItem]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ActionItem.id",
    )
    decisions: Mapped[list[Decision]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Decision.id",
    )


class ActionItem(Base):
    __tablename__ = "action_items"
    __table_args__ = (
        Index("ix_action_items_meeting_status", "meeting_id", "status"),
        CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_action_items_description_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(500), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        SQLAlchemyEnum(
            TaskStatus,
            name="task_status",
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=TaskStatus.PROMISED,
    )
    external_ticket_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    meeting: Mapped[Meeting] = relationship(back_populates="action_items")


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_meeting_state", "meeting_id", "state"),
        CheckConstraint(
            "length(trim(statement)) > 0",
            name="ck_decisions_statement_nonempty",
        ),
        CheckConstraint(
            "timestamp_seconds IS NULL OR timestamp_seconds >= 0",
            name="ck_decisions_timestamp_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[DecisionState] = mapped_column(
        SQLAlchemyEnum(
            DecisionState,
            name="decision_state",
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp_seconds: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    meeting: Mapped[Meeting] = relationship(back_populates="decisions")


class UnsubscribedEmail(Base):
    __tablename__ = "unsubscribed_emails"
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_unsubscribed_emails_email_normalized"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    unsubscribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    @validates("email")
    def normalize_email(self, key: str, value: str) -> str:
        """Trim an address and keep its case-insensitive key in sync."""
        del key
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("email must not be empty")
        self.email_normalized = normalized
        return value.strip()


class MomEmailRecipient(Base):
    """A locally managed address available for meeting-minutes delivery."""

    __tablename__ = "mom_email_recipients"
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_mom_email_recipients_normalized"),
        CheckConstraint("length(trim(email)) > 2", name="ck_mom_email_recipients_email_nonempty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    @validates("email")
    def normalize_email(self, key: str, value: str) -> str:
        """Keep a case-insensitive unique key in sync with the displayed address."""
        del key
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("email must not be empty")
        self.email_normalized = normalized
        return value.strip()

    @validates("display_name")
    def normalize_display_name(self, key: str, value: str | None) -> str | None:
        del key
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class ActionItemDrift(Base):
    """A later meeting's observation of an earlier, still-open action item."""

    __tablename__ = "action_item_drift"
    __table_args__ = (
        UniqueConstraint(
            "prior_action_item_id",
            "observed_in_meeting_id",
            name="uq_action_item_drift_prior_meeting",
        ),
        Index("ix_action_item_drift_observed_meeting", "observed_in_meeting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prior_action_item_id: Mapped[int] = mapped_column(
        ForeignKey("action_items.id", ondelete="CASCADE"), nullable=False
    )
    observed_action_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_items.id", ondelete="SET NULL"), nullable=True
    )
    observed_in_meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    drift_type: Mapped[str] = mapped_column(String(32), nullable=False)
    match_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ReminderDelivery(Base):
    """Idempotency record for a reminder sent about a task or risk on a date."""

    __tablename__ = "reminder_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "reminder_type",
            "reference_key",
            "reminder_date",
            "recipient_normalized",
            name="uq_reminder_delivery_daily_recipient",
        ),
        Index("ix_reminder_deliveries_reminder_date", "reminder_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reminder_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reminder_date: Mapped[date] = mapped_column(Date, nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class CalendarOAuthState(Base):
    """Single-use OAuth state record used to protect provider callbacks."""

    __tablename__ = "calendar_oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    pkce_verifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class CalendarConnection(Base):
    """One encrypted calendar OAuth connection per provider in this local workspace."""

    __tablename__ = "calendar_connections"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class CalendarTaskSync(Base):
    """Idempotency record for an action item copied to an external calendar."""

    __tablename__ = "calendar_task_syncs"
    __table_args__ = (
        UniqueConstraint("provider", "action_item_id", name="uq_calendar_task_sync_provider_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    action_item_id: Mapped[int] = mapped_column(
        ForeignKey("action_items.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MeetingRiskStatus(Base):
    """User-managed open/resolved state for one extracted risk in a meeting."""

    __tablename__ = "meeting_risk_statuses"
    __table_args__ = (
        UniqueConstraint("meeting_id", "risk_index", name="uq_meeting_risk_status_index"),
        Index("ix_meeting_risk_status_state", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    risk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
