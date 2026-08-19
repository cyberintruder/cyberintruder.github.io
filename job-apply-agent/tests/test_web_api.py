"""The dashboard API, driven end to end against a real database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from jobapply import db as db_mod
from jobapply.models import (
    Application,
    ApplyType,
    AppStatus,
    Channel,
    EmailDraft,
    Job,
    Source,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBAPPLY_DATA_DIR", str(tmp_path))
    from jobapply import settings as settings_mod

    settings_mod.load(reload=True)
    db_mod.reset_for_tests(f"sqlite:///{tmp_path/'api.db'}")
    _seed()
    from jobapply.web.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _seed():
    now = datetime.now(UTC)
    with db_mod.session_scope() as sess:
        for idx, (title, company, status) in enumerate([
            ("Cloud Security Engineer", "Nimbus", AppStatus.SUBMITTED),
            ("Detection Engineer", "Northwind", AppStatus.NEEDS_REVIEW),
            ("DevSecOps Engineer", "Orbit", AppStatus.SCHEDULED),
        ]):
            job = Job(
                source=Source.LINKEDIN, external_id=f"j{idx}", title=title,
                company=company, location="India", description=f"{title} description",
                apply_type=ApplyType.EXTERNAL, ats_vendor="greenhouse",
                url=f"https://www.linkedin.com/jobs/view/{idx}/", posted_at=now - timedelta(hours=5),
            )
            sess.add(job)
            sess.flush()
            app_row = Application(
                job_id=job.id, resume_key="cloud-security", status=status,
                channel=Channel.ATS, match_score=80 + idx,
                match_reasons=["keyword overlap"],
                scheduled_for=now + timedelta(hours=1),
                schedule_reason="primary window",
                submitted_at=now if status == AppStatus.SUBMITTED else None,
                answers=[{"field_id": "f1", "label": "Email", "value": "a@b.c",
                          "source": "profile", "confidence": 1.0, "required": True}],
                open_questions=(
                    [{"field_id": "f9", "label": "Why us?", "value": "",
                      "source": "unresolved", "confidence": 0.0, "required": True,
                      "rationale": "free text"}]
                    if status == AppStatus.NEEDS_REVIEW else []
                ),
            )
            sess.add(app_row)
            sess.flush()
            db_mod.log_event(sess, app_row, "matched", "scored")
            if status == AppStatus.NEEDS_REVIEW:
                sess.add(EmailDraft(application_id=app_row.id, to_addr="hr@x.example",
                                    subject="Role", body="Hello"))


def test_pages_render(client):
    for path in ("/", "/applications", "/applications/1", "/review", "/queue", "/resumes"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "job-apply" in r.text


def test_stats_shape(client):
    data = client.get("/api/stats").json()
    assert data["totals"]["all"] == 3
    assert data["totals"]["submitted"] == 1
    assert data["totals"]["needs_review"] == 1
    assert data["totals"]["scheduled"] == 1
    assert {r["status"] for r in data["by_status"]} >= {"submitted", "needs_review"}
    assert "dry_run" in data["config"]


def test_application_list_and_filters(client):
    assert client.get("/api/applications").json()["total"] == 3
    assert client.get("/api/applications?status=submitted").json()["total"] == 1
    assert client.get("/api/applications?q=Detection").json()["total"] == 1
    assert client.get("/api/applications?q=nothing-here").json()["total"] == 0


def test_application_detail_includes_provenance(client):
    d = client.get("/api/applications/1").json()
    assert d["title"] == "Cloud Security Engineer"
    assert d["answers"][0]["source"] == "profile"
    assert d["events"] and d["events"][0]["kind"] == "matched"


def test_missing_application_is_404(client):
    assert client.get("/api/applications/999").status_code == 404


def test_review_queue_lists_open_questions(client):
    items = client.get("/api/review").json()["items"]
    assert len(items) == 1
    assert items[0]["open_questions"][0]["field_id"] == "f9"
    assert items[0]["email"]["to"] == "hr@x.example"


def test_answering_and_approving_moves_to_scheduled(client):
    app_id = client.get("/api/review").json()["items"][0]["id"]
    r = client.post(f"/api/applications/{app_id}/answers",
                    json={"answers": {"f9": "Your detection stack"}, "approve": True})
    assert r.json()["status"] == "scheduled"
    assert r.json()["open_questions"] == []

    detail = client.get(f"/api/applications/{app_id}").json()
    assert any(a["source"] == "human" for a in detail["answers"])
    assert detail["events"][-1]["kind"] == "approved"


def test_partial_answers_stay_in_review(client):
    app_id = client.get("/api/review").json()["items"][0]["id"]
    r = client.post(f"/api/applications/{app_id}/answers", json={"answers": {}, "approve": True})
    assert r.json()["status"] == "needs_review"
    assert len(r.json()["open_questions"]) == 1


def test_reschedule_skip_and_retry(client):
    assert client.post("/api/applications/3/reschedule",
                       json={"minutes_from_now": 30}).json()["ok"]
    assert client.post("/api/applications/3/skip").json()["ok"]
    assert client.get("/api/applications/3").json()["status"] == "skipped"
    assert client.post("/api/applications/3/retry").json()["ok"]
    assert client.get("/api/applications/3").json()["status"] == "scheduled"


def test_email_draft_can_be_edited(client):
    app_id = client.get("/api/review").json()["items"][0]["id"]
    r = client.patch(f"/api/applications/{app_id}/email",
                     json={"subject": "Updated", "to_addr": "new@x.example"})
    assert r.json()["email"]["subject"] == "Updated"
    assert r.json()["email"]["to"] == "new@x.example"


def test_queue_reports_due_times(client):
    items = client.get("/api/queue").json()["items"]
    assert items and items[0]["due_in_minutes"] is not None
    assert items[0]["schedule_reason"] == "primary window"


def test_resume_registry_endpoint(client):
    data = client.get("/api/resumes").json()
    assert len(data["items"]) >= 5
    keys = {r["key"] for r in data["items"]}
    assert "cloud-security" in keys
    assert next(r for r in data["items"] if r["key"] == "cloud-security")["used"] == 3


def test_path_traversal_is_blocked(client):
    assert client.get("/api/artifact?path=../../../../etc/passwd").status_code == 404
    assert client.get("/api/resume-file?path=/etc/passwd").status_code == 404


def test_resume_file_download_works(client):
    from jobapply import settings as settings_mod

    path = settings_mod.load().env.resume_dir / "cloud-security-engineer.md"
    r = client.get(f"/api/resume-file?path={path}")
    assert r.status_code == 200
    assert "Cloud Security Engineer" in r.text
