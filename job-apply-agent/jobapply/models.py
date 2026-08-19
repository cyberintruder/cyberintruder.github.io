"""SQLAlchemy models — the single source of truth the dashboard renders from."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------- enums


class Source(str, enum.Enum):
    LINKEDIN = "linkedin"
    MANUAL_SCREENSHOT = "manual_screenshot"
    URL = "url"


class ApplyType(str, enum.Enum):
    EASY_APPLY = "easy_apply"
    EXTERNAL = "external"
    EMAIL = "email"
    UNKNOWN = "unknown"


class Channel(str, enum.Enum):
    LINKEDIN_EASY_APPLY = "linkedin_easy_apply"
    ATS = "ats"
    EMAIL = "email"


class AppStatus(str, enum.Enum):
    """Lifecycle of one application."""

    MATCHED = "matched"          # scored, not yet queued
    SCHEDULED = "scheduled"      # has a send time, waiting for it
    IN_PROGRESS = "in_progress"  # worker is driving the form right now
    NEEDS_REVIEW = "needs_review"  # blocked on a human answer/approval
    SUBMITTED = "submitted"      # confirmed submitted
    FAILED = "failed"
    SKIPPED = "skipped"          # filtered out or user-rejected

    @property
    def is_terminal(self) -> bool:
        return self in {AppStatus.SUBMITTED, AppStatus.SKIPPED}


class ScreenshotStatus(str, enum.Enum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    DRAFTED = "drafted"
    FAILED = "failed"
    IGNORED = "ignored"


class EmailStatus(str, enum.Enum):
    DRAFTED = "drafted"
    SENT = "sent"
    FAILED = "failed"


# -------------------------------------------------------------------- tables


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(Enum(Source), default=Source.LINKEDIN)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(1024), default="")
    apply_url: Mapped[str | None] = mapped_column(String(1024))
    ats_vendor: Mapped[str | None] = mapped_column(String(40), index=True)

    title: Mapped[str] = mapped_column(String(320), default="")
    company: Mapped[str] = mapped_column(String(320), default="", index=True)
    location: Mapped[str] = mapped_column(String(320), default="")
    workplace_type: Mapped[str | None] = mapped_column(String(40))  # remote/hybrid/onsite
    seniority: Mapped[str | None] = mapped_column(String(60))
    salary_text: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")

    apply_type: Mapped[ApplyType] = mapped_column(Enum(ApplyType), default=ApplyType.UNKNOWN)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_name: Mapped[str | None] = mapped_column(String(200))

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    search_name: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    applications: Mapped[list[Application]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_job_source_external"),
        Index("ix_job_company_title", "company", "title"),
    )

    @property
    def fingerprint(self) -> str:
        return f"{self.company.strip().lower()}::{self.title.strip().lower()}"


class ResumeRecord(Base):
    """A resume variant available to the agent — base or LLM-tailored."""

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(200), default="")
    path: Mapped[str] = mapped_column(String(1024), default="")
    fmt: Mapped[str] = mapped_column(String(16), default="pdf")
    summary: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    seniority: Mapped[str | None] = mapped_column(String(60))
    is_tailored: Mapped[bool] = mapped_column(default=False)
    parent_key: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    resume_key: Mapped[str | None] = mapped_column(String(120))
    resume_path: Mapped[str | None] = mapped_column(String(1024))

    status: Mapped[AppStatus] = mapped_column(Enum(AppStatus), default=AppStatus.MATCHED, index=True)
    channel: Mapped[Channel | None] = mapped_column(Enum(Channel))

    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    match_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSON, default=list)

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    schedule_reason: Mapped[str | None] = mapped_column(String(400))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cover_letter: Mapped[str | None] = mapped_column(Text)
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    open_questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    screenshots: Mapped[list[str]] = mapped_column(JSON, default=list)
    confirmation: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="applications")
    events: Mapped[list[Event]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Event.ts"
    )
    email: Mapped[EmailDraft | None] = relationship(
        back_populates="application", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (UniqueConstraint("job_id", name="uq_application_job"),)


class Event(Base):
    """Append-only audit trail rendered as the timeline in the UI."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(60), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    application: Mapped[Application | None] = relationship(back_populates="events")


class ScreenshotItem(Base):
    """One dropped screenshot of a LinkedIn post in the manual flow."""

    __tablename__ = "screenshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[ScreenshotStatus] = mapped_column(
        Enum(ScreenshotStatus), default=ScreenshotStatus.PENDING, index=True
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), unique=True
    )
    to_addr: Mapped[str] = mapped_column(String(400), default="")
    cc: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    attachment_path: Mapped[str | None] = mapped_column(String(1024))
    gmail_draft_id: Mapped[str | None] = mapped_column(String(120))
    gmail_message_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[EmailStatus] = mapped_column(Enum(EmailStatus), default=EmailStatus.DRAFTED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    application: Mapped[Application] = relationship(back_populates="email")


class RateCounter(Base):
    """Per-day / per-hour application counters backing the throughput guards."""

    __tablename__ = "rate_counters"

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # e.g. "day:2026-08-19"
    count: Mapped[int] = mapped_column(Integer, default=0)
