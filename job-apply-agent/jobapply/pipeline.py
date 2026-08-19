"""The orchestrator.

Three entry points, all safe to run repeatedly:

  `discover()`  LinkedIn search -> Job rows -> matched Applications with a send time
  `ingest_screenshots()`  screenshots -> Job rows -> Gmail drafts
  `run_due()`   picks up applications whose send time has arrived and applies

Everything is written to the database as it happens, so a crashed run resumes
cleanly and the dashboard is always current.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from . import render, timing
from . import settings as settings_mod
from . import tailor as tailor_mod
from .appliers.base import ApplyContext, Outcome, pick_applier
from .browser import BrowserOptions, browser_context
from .db import log_event, session_scope
from .mailer.compose import compose_application_email
from .mailer.gmail import GmailClient, GmailUnavailable
from .models import (
    Application,
    ApplyType,
    AppStatus,
    Channel,
    EmailDraft,
    EmailStatus,
    Job,
    ResumeRecord,
    ScreenshotItem,
    ScreenshotStatus,
    Source,
)
from .resumes import ResumeRegistry, select_resume
from .sources.linkedin import LinkedInSource, ScrapedJob

log = logging.getLogger(__name__)


@dataclass
class DiscoverReport:
    scanned: int = 0
    new_jobs: int = 0
    matched: int = 0
    skipped_low_score: int = 0
    skipped_filter: int = 0
    scheduled: int = 0
    tailored: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned, "new_jobs": self.new_jobs, "matched": self.matched,
            "skipped_low_score": self.skipped_low_score, "skipped_filter": self.skipped_filter,
            "scheduled": self.scheduled, "tailored": self.tailored,
            "errors": self.errors or [],
        }


# --------------------------------------------------------------------- filters


def passes_hard_filters(title: str, company: str, description: str, cfg) -> tuple[bool, str]:
    low_title = (title or "").lower()
    for term in cfg.exclude_title_terms:
        if term.lower() in low_title:
            return False, f"title contains excluded term {term!r}"
    for name in cfg.exclude_companies:
        if name.lower() in (company or "").lower():
            return False, f"company {name!r} is excluded"
    if cfg.require_terms:
        blob = f"{title} {description}".lower()
        if not any(t.lower() in blob for t in cfg.require_terms):
            return False, "none of the required terms appear in the posting"
    return True, ""


# -------------------------------------------------------------------- discover


async def discover(headless: bool = True, limit: int | None = None) -> DiscoverReport:
    """Search LinkedIn, score every result, and queue the ones worth applying to."""
    st = settings_mod.load()
    report = DiscoverReport(errors=[])
    specs = st.config.searches
    if not specs:
        report.errors.append("no searches configured in config.yaml")
        return report

    source = LinkedInSource(headless=headless)
    scraped = await source.search(specs)
    if limit:
        scraped = scraped[:limit]
    report.scanned = len(scraped)

    registry = ResumeRegistry.load()
    for sj in scraped:
        try:
            _ingest_scraped(sj, registry, st, report)
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to ingest %s", sj.external_id)
            report.errors.append(f"{sj.external_id}: {exc}")
    return report


def _ingest_scraped(sj: ScrapedJob, registry: ResumeRegistry, st, report: DiscoverReport) -> None:
    with session_scope() as sess:
        existing = sess.scalar(
            select(Job).where(Job.source == Source.LINKEDIN, Job.external_id == sj.external_id)
        )
        if existing:
            return

        job = Job(
            source=Source.LINKEDIN,
            external_id=sj.external_id,
            url=sj.url,
            apply_url=sj.apply_url,
            ats_vendor=sj.ats_vendor,
            title=sj.title,
            company=sj.company,
            location=sj.location,
            workplace_type=sj.workplace_type,
            salary_text=sj.salary_text,
            description=sj.description,
            apply_type=ApplyType(sj.apply_type),
            posted_at=sj.posted_at,
            search_name=sj.search_name,
            timezone=timing.resolve_timezone(sj.location, st.config.timing.timezone_default),
            raw=sj.raw,
        )
        sess.add(job)
        sess.flush()
        report.new_jobs += 1

        ok, why = passes_hard_filters(job.title, job.company, job.description, st.config.matching)
        if not ok:
            app = Application(job_id=job.id, status=AppStatus.SKIPPED, match_reasons=[why])
            sess.add(app)
            sess.flush()
            log_event(sess, app, "filtered", why)
            report.skipped_filter += 1
            return

        match = select_resume(
            job.title, job.description, registry=registry,
            company=job.company, location=job.location,
        )
        app = Application(
            job_id=job.id,
            resume_key=match.resume_key,
            match_score=match.score,
            match_reasons=match.reasons,
            missing_skills=match.missing_skills,
            channel=_channel_for(job),
        )
        sess.add(app)
        sess.flush()
        log_event(
            sess, app, "matched",
            f"scored {match.score:.0f} via {match.source}; chose resume {match.resume_key!r}",
            ranked=match.ranked, seniority_fit=match.seniority_fit,
        )

        if match.score < st.config.matching.min_score:
            app.status = AppStatus.SKIPPED
            app.match_reasons = (app.match_reasons or []) + [
                f"score {match.score:.0f} below threshold {st.config.matching.min_score}"
            ]
            log_event(sess, app, "skipped", f"score {match.score:.0f} below minimum")
            report.skipped_low_score += 1
            return

        report.matched += 1
        _attach_resume_file(sess, app, match, job, st, report)
        _schedule(sess, app, job, st)
        report.scheduled += 1


def _channel_for(job: Job) -> Channel:
    if job.apply_type == ApplyType.EASY_APPLY:
        return Channel.LINKEDIN_EASY_APPLY
    if job.apply_type == ApplyType.EMAIL:
        return Channel.EMAIL
    return Channel.ATS


def _attach_resume_file(sess, app: Application, match, job: Job, st, report: DiscoverReport) -> None:
    """Resolve the concrete file to send, tailoring it when the score warrants."""
    if not match.resume:
        return
    cfg = st.config.matching

    if match.score >= cfg.tailor_above_score and _tailored_today(sess) < cfg.max_tailored_per_day:
        outcome = tailor_mod.tailor_resume(
            match.resume, job_title=job.title, company=job.company, description=job.description
        )
        if outcome.ok and outcome.path:
            app.resume_path = str(outcome.path)
            sess.add(ResumeRecord(
                key=f"{match.resume.key}@{job.id}",
                role=match.resume.role,
                path=str(outcome.path),
                fmt=outcome.path.suffix.lstrip("."),
                summary=match.resume.summary,
                keywords=match.resume.keywords,
                seniority=match.resume.seniority,
                is_tailored=True,
                parent_key=match.resume.key,
            ))
            log_event(sess, app, "tailored",
                      f"generated a tailored variant of {match.resume.key!r} for this posting")
            report.tailored += 1
            return
        log_event(sess, app, "tailor_skipped", f"using the base resume: {outcome.reason}")

    try:
        app.resume_path = str(render.ensure_attachable(match.resume.path))
    except FileNotFoundError as exc:
        log_event(sess, app, "error", str(exc))


def _tailored_today(sess) -> int:
    since = datetime.now(UTC) - timedelta(days=1)
    return sess.scalar(
        select(func.count(ResumeRecord.id)).where(
            ResumeRecord.is_tailored.is_(True), ResumeRecord.created_at >= since
        )
    ) or 0


def _schedule(sess, app: Application, job: Job, st) -> None:
    now = datetime.now(UTC)
    last = sess.scalar(
        select(func.max(Application.submitted_at)).where(Application.submitted_at.is_not(None))
    )
    sent_today = sess.scalar(
        select(func.count(Application.id)).where(
            Application.submitted_at >= now - timedelta(days=1)
        )
    ) or 0
    sent_hour = sess.scalar(
        select(func.count(Application.id)).where(
            Application.submitted_at >= now - timedelta(hours=1)
        )
    ) or 0

    plan = timing.plan_send_time(
        posted_at=job.posted_at,
        location=job.location,
        cfg=st.config.timing,
        match_score=app.match_score,
        now=now,
        last_sent_at=_as_utc(last),
        sent_today=sent_today,
        sent_this_hour=sent_hour,
    )
    if plan.skip:
        app.status = AppStatus.SKIPPED
        app.schedule_reason = plan.reason
        log_event(sess, app, "skipped", plan.reason)
        return

    app.status = AppStatus.SCHEDULED
    app.scheduled_for = plan.send_at
    app.schedule_reason = plan.reason
    log_event(sess, app, "scheduled", plan.reason, send_at=plan.send_at.isoformat(),
              priority=plan.priority)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------- run due


async def run_due(limit: int = 5, headless: bool = True, force: bool = False) -> list[dict]:
    """Apply to every application whose scheduled time has arrived."""
    st = settings_mod.load()
    now = datetime.now(UTC)

    with session_scope() as sess:
        stmt = select(Application).where(Application.status == AppStatus.SCHEDULED)
        if not force:
            stmt = stmt.where(Application.scheduled_for <= now)
        stmt = stmt.order_by(Application.scheduled_for).limit(limit)
        due_ids = [a.id for a in sess.scalars(stmt).all()]

    if not due_ids:
        return []

    results: list[dict] = []
    browser_ids = []
    email_ids = []
    with session_scope() as sess:
        for app_id in due_ids:
            app = sess.get(Application, app_id)
            (email_ids if app.channel == Channel.EMAIL else browser_ids).append(app_id)

    for app_id in email_ids:
        results.append(_run_email_application(app_id, st))

    if browser_ids:
        async with browser_context(BrowserOptions(headless=headless)) as page:
            for app_id in browser_ids:
                results.append(await _run_browser_application(page, app_id, st))
    return results


async def _run_browser_application(page, app_id: int, st) -> dict:
    with session_scope() as sess:
        app = sess.get(Application, app_id)
        job = app.job
        app.status = AppStatus.IN_PROGRESS
        app.started_at = datetime.now(UTC)
        app.attempts += 1
        registry = ResumeRegistry.load()
        resume = registry.get((app.resume_key or "").split("@")[0])
        ctx = ApplyContext(
            job_title=job.title,
            company=job.company,
            job_description=job.description,
            apply_url=job.apply_url or job.url,
            resume_path=app.resume_path or "",
            resume_summary=resume.summary if resume else "",
            cover_letter=app.cover_letter,
            application_id=app.id,
            dry_run=st.env.dry_run,
            require_review=st.env.require_review,
            screenshot_evidence=st.config.apply.screenshot_evidence,
        )
        vendor = "linkedin_easy_apply" if app.channel == Channel.LINKEDIN_EASY_APPLY else job.ats_vendor
        log_event(sess, app, "apply_start", f"driving {vendor or 'generic'} at {ctx.apply_url[:120]}")

    applier = pick_applier(ctx.apply_url, vendor)
    try:
        result = await applier.apply(page, ctx)
    except Exception as exc:  # noqa: BLE001
        log.exception("applier %s crashed on application %s", applier.name, app_id)
        with session_scope() as sess:
            app = sess.get(Application, app_id)
            app.status = AppStatus.FAILED
            app.error = f"{type(exc).__name__}: {exc}"
            log_event(sess, app, "error", app.error)
        return {"application_id": app_id, "outcome": "failed", "message": str(exc)}

    with session_scope() as sess:
        app = sess.get(Application, app_id)
        app.answers = result.answers
        app.open_questions = result.open_questions
        app.screenshots = (app.screenshots or []) + result.screenshots
        app.confirmation = result.confirmation or None
        app.status = _status_for(result.outcome)
        if result.outcome == Outcome.SUBMITTED:
            app.submitted_at = datetime.now(UTC)
        if result.outcome == Outcome.FAILED:
            app.error = result.message
        log_event(sess, app, result.outcome, result.message, steps=result.steps,
                  applier=applier.name)
    return {"application_id": app_id, "outcome": result.outcome, "message": result.message,
            "applier": applier.name}


def _status_for(outcome: str) -> AppStatus:
    return {
        Outcome.SUBMITTED: AppStatus.SUBMITTED,
        Outcome.NEEDS_REVIEW: AppStatus.NEEDS_REVIEW,
        Outcome.FAILED: AppStatus.FAILED,
        Outcome.SKIPPED: AppStatus.SKIPPED,
        Outcome.DRY_RUN: AppStatus.NEEDS_REVIEW,
    }.get(outcome, AppStatus.FAILED)


# ------------------------------------------------------------- email channel


def _run_email_application(app_id: int, st) -> dict:
    with session_scope() as sess:
        app = sess.get(Application, app_id)
        job = app.job
        if not job.contact_email:
            app.status = AppStatus.NEEDS_REVIEW
            app.open_questions = [{"label": "recipient address", "value": "",
                                   "source": "unresolved", "required": True,
                                   "rationale": "the post named no email address"}]
            log_event(sess, app, "needs_review", "no recipient address found in the post")
            return {"application_id": app_id, "outcome": "needs_review",
                    "message": "no recipient address"}
        payload = {
            "to": job.contact_email, "title": job.title, "company": job.company,
            "description": job.description, "instructions": (job.raw or {}).get("apply_instructions", ""),
            "contact_name": job.contact_name or "", "resume_path": app.resume_path,
            "resume_key": app.resume_key,
        }
        app.status = AppStatus.IN_PROGRESS
        app.attempts += 1

    registry = ResumeRegistry.load()
    resume = registry.get((payload["resume_key"] or "").split("@")[0])
    email = compose_application_email(
        job_title=payload["title"],
        company=payload["company"],
        job_description=payload["description"],
        resume_role=resume.role if resume else "",
        resume_summary=resume.summary if resume else "",
        apply_instructions=payload["instructions"],
        contact_name=payload["contact_name"],
    ).with_signature(st.config.manual.signature)

    send = st.env.email_mode.lower() == "send" and not st.env.dry_run
    attachment = Path(payload["resume_path"]) if payload["resume_path"] else None

    try:
        outcome = GmailClient().deliver(
            to=payload["to"], subject=email.subject, body=email.body,
            attachment=attachment, cc=st.config.manual.cc, send=send,
        )
    except GmailUnavailable as exc:
        with session_scope() as sess:
            app = sess.get(Application, app_id)
            app.status = AppStatus.NEEDS_REVIEW
            app.error = str(exc)
            _save_draft_row(sess, app, payload["to"], email, attachment, st, None, None)
            log_event(sess, app, "needs_review", f"gmail unavailable: {exc}")
        return {"application_id": app_id, "outcome": "needs_review", "message": str(exc)}

    with session_scope() as sess:
        app = sess.get(Application, app_id)
        _save_draft_row(sess, app, payload["to"], email, attachment, st,
                        outcome.draft_id, outcome.message_id)
        if outcome.sent:
            app.status = AppStatus.SUBMITTED
            app.submitted_at = datetime.now(UTC)
            app.confirmation = f"emailed {payload['to']} (gmail id {outcome.message_id})"
            log_event(sess, app, "submitted", f"email sent to {payload['to']}")
        else:
            app.status = AppStatus.NEEDS_REVIEW
            log_event(sess, app, "drafted",
                      f"Gmail draft ready for {payload['to']} — review and send")
    return {
        "application_id": app_id,
        "outcome": "submitted" if outcome.sent else "needs_review",
        "message": f"gmail draft {outcome.draft_id}",
    }


def _save_draft_row(sess, app, to, email, attachment, st, draft_id, message_id) -> None:
    row = app.email or EmailDraft(application_id=app.id)
    row.to_addr = to
    row.cc = st.config.manual.cc
    row.subject = email.subject
    row.body = email.body
    row.attachment_path = str(attachment) if attachment else None
    row.gmail_draft_id = draft_id
    row.gmail_message_id = message_id
    row.status = EmailStatus.SENT if message_id else EmailStatus.DRAFTED
    if message_id:
        row.sent_at = datetime.now(UTC)
    sess.add(row)
    app.email = row


# ---------------------------------------------------------------- screenshots


def ingest_screenshots(paths: list[Path] | None = None) -> list[dict]:
    """Read pending screenshots, create jobs, and queue email applications."""
    from .manual.watcher import pending_screenshots, register_screenshot

    st = settings_mod.load()
    registry = ResumeRegistry.load()
    items = paths if paths is not None else pending_screenshots(st)
    out: list[dict] = []

    for path in items:
        row_id = register_screenshot(Path(path))
        if row_id is None:
            out.append({"path": str(path), "status": "duplicate"})
            continue
        out.append(_process_screenshot(row_id, registry, st))
    return out


def _process_screenshot(screenshot_id: int, registry: ResumeRegistry, st) -> dict:
    from .manual.extract import extract_from_screenshot

    with session_scope() as sess:
        item = sess.get(ScreenshotItem, screenshot_id)
        path = Path(item.path)

    try:
        post = extract_from_screenshot(path)
    except Exception as exc:  # noqa: BLE001
        with session_scope() as sess:
            item = sess.get(ScreenshotItem, screenshot_id)
            item.status = ScreenshotStatus.FAILED
            item.error = str(exc)
        return {"screenshot_id": screenshot_id, "status": "failed", "error": str(exc)}

    if not post.is_job_post:
        with session_scope() as sess:
            item = sess.get(ScreenshotItem, screenshot_id)
            item.status = ScreenshotStatus.IGNORED
            item.extracted = post.to_dict()
        return {"screenshot_id": screenshot_id, "status": "ignored",
                "reason": "not a job post"}

    match = select_resume(post.title, post.description, registry=registry,
                          company=post.company, location=post.location)

    with session_scope() as sess:
        item = sess.get(ScreenshotItem, screenshot_id)
        item.extracted = post.to_dict()
        item.status = ScreenshotStatus.EXTRACTED

        job = Job(
            source=Source.MANUAL_SCREENSHOT,
            external_id=f"shot-{screenshot_id}",
            url=post.apply_url or "",
            apply_url=post.apply_url or None,
            title=post.title,
            company=post.company,
            location=post.location,
            workplace_type=post.workplace_type if post.workplace_type != "unknown" else None,
            seniority=post.seniority or None,
            description=post.description,
            apply_type=ApplyType.EMAIL if post.contact_email else ApplyType.EXTERNAL,
            contact_email=post.contact_email or None,
            contact_name=post.contact_name or None,
            posted_at=post.posted_at,
            timezone=timing.resolve_timezone(post.location, st.config.timing.timezone_default),
            raw={"apply_instructions": post.apply_instructions,
                 "skills": post.skills, "confidence": post.confidence},
        )
        sess.add(job)
        sess.flush()
        item.job_id = job.id

        app = Application(
            job_id=job.id,
            resume_key=match.resume_key,
            match_score=match.score,
            match_reasons=match.reasons,
            missing_skills=match.missing_skills,
            channel=Channel.EMAIL if post.contact_email else Channel.ATS,
        )
        sess.add(app)
        sess.flush()
        log_event(sess, app, "matched",
                  f"from screenshot: scored {match.score:.0f}, resume {match.resume_key!r}")

        report = DiscoverReport(errors=[])
        _attach_resume_file(sess, app, match, job, st, report)
        _schedule(sess, app, job, st)
        item.status = ScreenshotStatus.DRAFTED
        app_id = app.id

    return {"screenshot_id": screenshot_id, "status": "queued", "application_id": app_id,
            "company": post.company, "title": post.title,
            "recipient": post.contact_email or None, "score": match.score}

