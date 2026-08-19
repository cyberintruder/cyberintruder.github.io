"""Dashboard.

Server-rendered shell + a JSON API the page polls. No build step, no npm — the
whole UI is three files you can read.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import settings as settings_mod
from ..db import init_db, log_event, session_scope
from ..models import (
    Application,
    AppStatus,
    Channel,
    EmailDraft,
    Event,
    Job,
    ResumeRecord,
    ScreenshotItem,
)
from ..resumes import ResumeRegistry

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
STATUS_ORDER = [
    AppStatus.NEEDS_REVIEW, AppStatus.SCHEDULED, AppStatus.IN_PROGRESS,
    AppStatus.SUBMITTED, AppStatus.FAILED, AppStatus.MATCHED, AppStatus.SKIPPED,
]


class AnswerPatch(BaseModel):
    answers: dict[str, str] = {}
    approve: bool = False


class RescheduleBody(BaseModel):
    send_at: datetime | None = None
    minutes_from_now: int | None = None


class EmailPatch(BaseModel):
    to_addr: str | None = None
    subject: str | None = None
    body: str | None = None


def create_app() -> FastAPI:
    st = settings_mod.load()
    init_db()

    app = FastAPI(title="job-apply-agent", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {"page": "dashboard"})

    @app.get("/applications", response_class=HTMLResponse)
    def applications_page(request: Request):
        return templates.TemplateResponse(request, "applications.html", {"page": "applications"})

    @app.get("/applications/{app_id}", response_class=HTMLResponse)
    def application_page(request: Request, app_id: int):
        return templates.TemplateResponse(
            request, "detail.html", {"page": "applications", "app_id": app_id}
        )

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request):
        return templates.TemplateResponse(request, "review.html", {"page": "review"})

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request):
        return templates.TemplateResponse(request, "queue.html", {"page": "queue"})

    @app.get("/resumes", response_class=HTMLResponse)
    def resumes_page(request: Request):
        return templates.TemplateResponse(request, "resumes.html", {"page": "resumes"})

    # -------------------------------------------------------------------- api

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        now = datetime.now(UTC)
        with session_scope() as sess:
            counts = Counter(
                {row[0].value: row[1] for row in
                 sess.execute(select(Application.status, func.count(Application.id))
                              .group_by(Application.status)).all()}
            )
            total = sum(counts.values())
            submitted = counts.get(AppStatus.SUBMITTED.value, 0)
            considered = total - counts.get(AppStatus.SKIPPED.value, 0)

            rows = sess.execute(
                select(Application.created_at, Application.submitted_at, Application.status)
            ).all()
            per_day: dict[str, dict[str, int]] = defaultdict(lambda: {"queued": 0, "submitted": 0})
            for created, submitted_at, _status in rows:
                if created:
                    per_day[_day(created)]["queued"] += 1
                if submitted_at:
                    per_day[_day(submitted_at)]["submitted"] += 1

            top_companies = sess.execute(
                select(Job.company, func.count(Application.id))
                .join(Application, Application.job_id == Job.id)
                .where(Application.status != AppStatus.SKIPPED)
                .group_by(Job.company)
                .order_by(func.count(Application.id).desc())
                .limit(8)
            ).all()

            by_resume = sess.execute(
                select(Application.resume_key, func.count(Application.id))
                .where(Application.resume_key.is_not(None),
                       Application.status != AppStatus.SKIPPED)
                .group_by(Application.resume_key)
                .order_by(func.count(Application.id).desc())
            ).all()

            by_channel = sess.execute(
                select(Application.channel, func.count(Application.id))
                .where(Application.channel.is_not(None))
                .group_by(Application.channel)
            ).all()

            avg_score = sess.scalar(
                select(func.avg(Application.match_score))
                .where(Application.status != AppStatus.SKIPPED)
            ) or 0.0

            next_up = sess.scalar(
                select(func.min(Application.scheduled_for))
                .where(Application.status == AppStatus.SCHEDULED,
                       Application.scheduled_for >= now)
            )
            sent_today = sess.scalar(
                select(func.count(Application.id))
                .where(Application.submitted_at >= now - timedelta(days=1))
            ) or 0
            screenshots_pending = sess.scalar(
                select(func.count(ScreenshotItem.id))
                .where(ScreenshotItem.status == "pending")
            ) or 0

        days = sorted(per_day.keys())[-30:]
        return {
            "totals": {
                "all": total,
                "considered": considered,
                "submitted": submitted,
                "needs_review": counts.get(AppStatus.NEEDS_REVIEW.value, 0),
                "scheduled": counts.get(AppStatus.SCHEDULED.value, 0),
                "failed": counts.get(AppStatus.FAILED.value, 0),
                "skipped": counts.get(AppStatus.SKIPPED.value, 0),
                "sent_today": sent_today,
                "screenshots_pending": screenshots_pending,
                "avg_score": round(float(avg_score), 1),
                "submit_rate": round(100.0 * submitted / considered, 1) if considered else 0.0,
            },
            "by_status": [{"status": s.value, "count": counts.get(s.value, 0)} for s in STATUS_ORDER],
            "timeseries": [{"day": d, **per_day[d]} for d in days],
            "top_companies": [{"company": c or "—", "count": n} for c, n in top_companies],
            "by_resume": [{"resume": k, "count": n} for k, n in by_resume],
            "by_channel": [{"channel": (c.value if c else "—"), "count": n} for c, n in by_channel],
            "next_scheduled": next_up.isoformat() if next_up else None,
            "config": {
                "dry_run": st.env.dry_run,
                "require_review": st.env.require_review,
                "email_mode": st.env.email_mode,
                "daily_cap": st.config.timing.max_applications_per_day,
            },
        }

    @app.get("/api/applications")
    def list_applications(
        status: str | None = None,
        q: str | None = None,
        channel: str | None = None,
        limit: int = Query(100, le=500),
        offset: int = 0,
    ) -> dict[str, Any]:
        with session_scope() as sess:
            stmt = select(Application, Job).join(Job, Application.job_id == Job.id)
            if status and status != "all":
                stmt = stmt.where(Application.status == AppStatus(status))
            if channel and channel != "all":
                stmt = stmt.where(Application.channel == Channel(channel))
            if q:
                like = f"%{q}%"
                stmt = stmt.where(Job.title.ilike(like) | Job.company.ilike(like))
            total = sess.scalar(
                select(func.count()).select_from(stmt.subquery())
            ) or 0
            stmt = stmt.order_by(Application.updated_at.desc()).limit(limit).offset(offset)
            items = [_app_summary(a, j) for a, j in sess.execute(stmt).all()]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/applications/{app_id}")
    def get_application(app_id: int) -> dict[str, Any]:
        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row:
                raise HTTPException(404, "application not found")
            job = app_row.job
            data = _app_summary(app_row, job)
            data.update({
                "description": job.description,
                "url": job.url,
                "apply_url": job.apply_url,
                "ats_vendor": job.ats_vendor,
                "contact_email": job.contact_email,
                "contact_name": job.contact_name,
                "salary_text": job.salary_text,
                "workplace_type": job.workplace_type,
                "search_name": job.search_name,
                "posted_at": _iso(job.posted_at),
                "answers": app_row.answers or [],
                "open_questions": app_row.open_questions or [],
                "missing_skills": app_row.missing_skills or [],
                "screenshots": app_row.screenshots or [],
                "cover_letter": app_row.cover_letter,
                "confirmation": app_row.confirmation,
                "error": app_row.error,
                "attempts": app_row.attempts,
                "schedule_reason": app_row.schedule_reason,
                "events": [
                    {"ts": _iso(e.ts), "kind": e.kind, "message": e.message, "data": e.data}
                    for e in app_row.events
                ],
                "email": _email_dict(app_row.email),
            })
        return data

    @app.post("/api/applications/{app_id}/answers")
    def patch_answers(app_id: int, patch: AnswerPatch) -> dict[str, Any]:
        """Answer the open questions the agent parked, and optionally requeue."""
        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row:
                raise HTTPException(404, "application not found")

            open_qs = list(app_row.open_questions or [])
            answers = list(app_row.answers or [])
            still_open: list[dict] = []
            for q in open_qs:
                value = patch.answers.get(q.get("field_id") or q.get("label", ""))
                if value:
                    answers.append({**q, "value": value, "source": "human",
                                    "confidence": 1.0, "rationale": "answered in the dashboard"})
                else:
                    still_open.append(q)
            app_row.answers = answers
            app_row.open_questions = still_open

            if patch.approve and not still_open:
                app_row.status = AppStatus.SCHEDULED
                app_row.scheduled_for = datetime.now(UTC)
                app_row.schedule_reason = "approved in the dashboard — sending on the next worker pass"
                log_event(sess, app_row, "approved", "human answered the open questions and approved")
            else:
                log_event(sess, app_row, "answered",
                          f"{len(answers)} answer(s) recorded, {len(still_open)} still open")
            return {"ok": True, "open_questions": still_open, "status": app_row.status.value}

    @app.post("/api/applications/{app_id}/reschedule")
    def reschedule(app_id: int, body: RescheduleBody) -> dict[str, Any]:
        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row:
                raise HTTPException(404, "application not found")
            if body.send_at:
                when = body.send_at if body.send_at.tzinfo else body.send_at.replace(tzinfo=UTC)
            else:
                when = datetime.now(UTC) + timedelta(minutes=body.minutes_from_now or 0)
            app_row.scheduled_for = when
            app_row.status = AppStatus.SCHEDULED
            app_row.schedule_reason = "rescheduled from the dashboard"
            log_event(sess, app_row, "rescheduled", f"send time moved to {when.isoformat()}")
            return {"ok": True, "scheduled_for": when.isoformat()}

    @app.post("/api/applications/{app_id}/skip")
    def skip(app_id: int) -> dict[str, Any]:
        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row:
                raise HTTPException(404, "application not found")
            app_row.status = AppStatus.SKIPPED
            log_event(sess, app_row, "skipped", "skipped from the dashboard")
            return {"ok": True}

    @app.post("/api/applications/{app_id}/retry")
    def retry(app_id: int) -> dict[str, Any]:
        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row:
                raise HTTPException(404, "application not found")
            app_row.status = AppStatus.SCHEDULED
            app_row.scheduled_for = datetime.now(UTC)
            app_row.error = None
            app_row.schedule_reason = "retry requested from the dashboard"
            log_event(sess, app_row, "retry", "queued for another attempt")
            return {"ok": True}

    @app.post("/api/applications/{app_id}/run")
    async def run_now(app_id: int) -> dict[str, Any]:
        """Drive this one application immediately, in the foreground."""
        from .. import pipeline

        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row:
                raise HTTPException(404, "application not found")
            app_row.status = AppStatus.SCHEDULED
            app_row.scheduled_for = datetime.now(UTC) - timedelta(seconds=1)

        results = await pipeline.run_due(limit=1, headless=True)
        return {"ok": True, "results": results}

    @app.patch("/api/applications/{app_id}/email")
    def patch_email(app_id: int, patch: EmailPatch) -> dict[str, Any]:
        with session_scope() as sess:
            app_row = sess.get(Application, app_id)
            if not app_row or not app_row.email:
                raise HTTPException(404, "no email draft for this application")
            row = app_row.email
            if patch.to_addr is not None:
                row.to_addr = patch.to_addr
            if patch.subject is not None:
                row.subject = patch.subject
            if patch.body is not None:
                row.body = patch.body
            log_event(sess, app_row, "email_edited", "draft edited in the dashboard")
            return {"ok": True, "email": _email_dict(row)}

    @app.get("/api/queue")
    def queue() -> dict[str, Any]:
        now = datetime.now(UTC)
        with session_scope() as sess:
            rows = sess.execute(
                select(Application, Job)
                .join(Job, Application.job_id == Job.id)
                .where(Application.status == AppStatus.SCHEDULED)
                .order_by(Application.scheduled_for)
            ).all()
            items = []
            for a, j in rows:
                d = _app_summary(a, j)
                sched = _aware(a.scheduled_for)
                d["due_in_minutes"] = round((sched - now).total_seconds() / 60) if sched else None
                d["overdue"] = bool(sched and sched <= now)
                items.append(d)
        return {"items": items, "now": now.isoformat()}

    @app.get("/api/review")
    def review() -> dict[str, Any]:
        with session_scope() as sess:
            rows = sess.execute(
                select(Application, Job)
                .join(Job, Application.job_id == Job.id)
                .where(Application.status == AppStatus.NEEDS_REVIEW)
                .order_by(Application.updated_at.desc())
            ).all()
            items = []
            for a, j in rows:
                d = _app_summary(a, j)
                d["open_questions"] = a.open_questions or []
                d["email"] = _email_dict(a.email)
                d["screenshots"] = a.screenshots or []
                items.append(d)
        return {"items": items}

    @app.get("/api/resumes")
    def resumes() -> dict[str, Any]:
        registry = ResumeRegistry.load()
        with session_scope() as sess:
            usage = dict(sess.execute(
                select(Application.resume_key, func.count(Application.id))
                .group_by(Application.resume_key)
            ).all())
            tailored = [
                {"key": r.key, "parent": r.parent_key, "path": r.path,
                 "created_at": _iso(r.created_at)}
                for r in sess.scalars(
                    select(ResumeRecord).where(ResumeRecord.is_tailored.is_(True))
                    .order_by(ResumeRecord.created_at.desc()).limit(50)
                ).all()
            ]
        items = []
        for r in registry:
            d = r.to_dict()
            d["used"] = usage.get(r.key, 0)
            d["is_default"] = r.key == registry.default_key
            items.append(d)
        return {"items": items, "tailored": tailored}

    @app.get("/api/screenshots")
    def screenshots() -> dict[str, Any]:
        with session_scope() as sess:
            rows = sess.scalars(
                select(ScreenshotItem).order_by(ScreenshotItem.created_at.desc()).limit(100)
            ).all()
            items = [
                {"id": s.id, "path": s.path, "status": s.status.value, "job_id": s.job_id,
                 "extracted": s.extracted, "error": s.error, "created_at": _iso(s.created_at)}
                for s in rows
            ]
        return {"items": items}

    @app.post("/api/actions/ingest-screenshots")
    def action_ingest() -> dict[str, Any]:
        from ..manual.watcher import ScreenshotWatcher

        return {"results": ScreenshotWatcher(archive_processed=True).poll_once()}

    @app.post("/api/actions/discover")
    async def action_discover(limit: int = 25) -> dict[str, Any]:
        from .. import pipeline

        report = await pipeline.discover(headless=True, limit=limit)
        return report.as_dict()

    @app.post("/api/actions/run-due")
    async def action_run_due(limit: int = 3) -> dict[str, Any]:
        from .. import pipeline

        return {"results": await pipeline.run_due(limit=limit, headless=True)}

    @app.get("/api/artifact")
    def artifact(path: str):
        """Serve a screenshot from the artifacts dir, guarded against traversal."""
        base = st.env.data_dir.resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target)

    @app.get("/api/resume-file")
    def resume_file(path: str):
        allowed = [st.env.resume_dir.resolve(), st.env.data_dir.resolve()]
        target = Path(path).resolve()
        if not any(str(target).startswith(str(root)) for root in allowed) or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target, filename=target.name)

    @app.get("/api/events")
    def events(limit: int = Query(60, le=300)) -> dict[str, Any]:
        with session_scope() as sess:
            rows = sess.execute(
                select(Event, Application, Job)
                .outerjoin(Application, Event.application_id == Application.id)
                .outerjoin(Job, Application.job_id == Job.id)
                .order_by(Event.ts.desc()).limit(limit)
            ).all()
            items = [
                {
                    "ts": _iso(e.ts), "kind": e.kind, "message": e.message,
                    "application_id": e.application_id,
                    "title": j.title if j else None, "company": j.company if j else None,
                }
                for e, _a, j in rows
            ]
        return {"items": items}

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):  # pragma: no cover
        log.exception("unhandled error")
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    return app


# ------------------------------------------------------------------- helpers


def _app_summary(a: Application, j: Job) -> dict[str, Any]:
    return {
        "id": a.id,
        "job_id": j.id,
        "title": j.title,
        "company": j.company,
        "location": j.location,
        "source": j.source.value,
        "status": a.status.value,
        "channel": a.channel.value if a.channel else None,
        "ats_vendor": j.ats_vendor,
        "match_score": round(a.match_score, 1),
        "match_reasons": a.match_reasons or [],
        "resume_key": a.resume_key,
        "resume_path": a.resume_path,
        "scheduled_for": _iso(a.scheduled_for),
        "schedule_reason": a.schedule_reason,
        "submitted_at": _iso(a.submitted_at),
        "created_at": _iso(a.created_at),
        "updated_at": _iso(a.updated_at),
        "open_question_count": len(a.open_questions or []),
        "url": j.url,
        "error": a.error,
    }


def _email_dict(row: EmailDraft | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "to": row.to_addr, "cc": row.cc, "subject": row.subject, "body": row.body,
        "attachment_path": row.attachment_path, "gmail_draft_id": row.gmail_draft_id,
        "status": row.status.value, "sent_at": _iso(row.sent_at),
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware else None


def _day(value: datetime) -> str:
    return _aware(value).strftime("%Y-%m-%d")
