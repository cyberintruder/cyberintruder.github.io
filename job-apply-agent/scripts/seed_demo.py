#!/usr/bin/env python3
"""Populate the database with realistic sample data.

Useful for exploring the dashboard before you have a LinkedIn session, and for
screenshotting the UI. Everything it creates is marked `[demo]` on the company
name so it is obvious what is real.

    python scripts/seed_demo.py [--reset]
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobapply import settings as settings_mod  # noqa: E402
from jobapply import timing  # noqa: E402
from jobapply.db import init_db, log_event, session_scope  # noqa: E402
from jobapply.models import (  # noqa: E402
    Application,
    ApplyType,
    AppStatus,
    Channel,
    EmailDraft,
    Job,
    Source,
)
from jobapply.resumes import ResumeRegistry, select_resume  # noqa: E402

SAMPLES = [
    ("Senior Cloud Security Engineer", "Nimbus Systems", "Bengaluru, India", "remote",
     "greenhouse", ApplyType.EXTERNAL,
     "Own AWS and Kubernetes security across a multi-account Organization. Design IAM "
     "least-privilege policies, roll out CSPM, write Terraform policy-as-code with OPA "
     "and Conftest, harden EKS. GuardDuty and Security Hub experience required.", 6),
    ("Application Security Engineer", "Lattice Pay", "Hyderabad, India", "hybrid",
     None, ApplyType.EASY_APPLY,
     "Threat modelling for new products, secure design review, ownership of the "
     "SAST/DAST/SCA pipeline, manual code review across Python and Java services, and "
     "running our security champions programme. OWASP ASVS familiarity expected.", 14),
    ("Detection Engineer", "Northwind Cloud", "Remote — India", "remote",
     "lever", ApplyType.EXTERNAL,
     "Build SIEM content in Splunk and Microsoft Sentinel. Author Sigma rules, map "
     "coverage against MITRE ATT&CK, tune CrowdStrike EDR alerts, and automate tier-1 "
     "triage with SOAR playbooks. Threat hunting experience valued.", 30),
    ("Staff Security Architect", "Meridian Health", "Pune, India", "onsite",
     "workday", ApplyType.EXTERNAL,
     "Define the enterprise security reference architecture, lead the zero-trust "
     "programme, own ISO 27001 and SOC 2 readiness, chair the architecture review "
     "board, and mentor a team of security engineers. CISSP preferred.", 52),
    ("DevSecOps Engineer", "Orbit Labs", "Remote — Europe", "remote",
     "ashby", ApplyType.EXTERNAL,
     "Shift security left: shared CI templates across GitHub Actions, SBOM generation "
     "and Sigstore signing, secrets management with Vault, OIDC workload identity "
     "migration off static cloud credentials, and hardened base images.", 9),
    ("Penetration Tester", "Kestrel Security", "Chennai, India", "hybrid",
     None, ApplyType.EASY_APPLY,
     "Deliver authorised network, web, API and Active Directory penetration tests. "
     "Red team operations with Cobalt Strike, adversary emulation mapped to ATT&CK, "
     "purple team exercises, and client-ready reporting. OSCP required.", 3),
    ("Cloud Security Architect", "Helio Freight", "Singapore", "hybrid",
     "smartrecruiters", ApplyType.EXTERNAL,
     "Multi-cloud security architecture across AWS and GCP, zero-trust network design, "
     "workload identity, and cloud compliance for a regulated logistics platform.", 20),
    ("Security Engineer — Detection", "Fernpath", "Remote — India", "remote",
     None, ApplyType.EMAIL,
     "Small security team looking for someone to own detection engineering end to end: "
     "Elastic SIEM, Sigma rules, alert tuning and incident response. Send your CV to "
     "careers@fernpath.example with the role in the subject line.", 5),
]

STATUS_PLAN = [
    AppStatus.SUBMITTED, AppStatus.SUBMITTED, AppStatus.NEEDS_REVIEW,
    AppStatus.SCHEDULED, AppStatus.SCHEDULED, AppStatus.SUBMITTED,
    AppStatus.FAILED, AppStatus.NEEDS_REVIEW,
]

OPEN_QS = [
    {"field_id": "f7", "label": "Why do you want to work at this company?", "value": "",
     "source": "unresolved", "confidence": 0.0, "required": True,
     "rationale": "free-text motivation question — not derivable from the profile"},
    {"field_id": "f9", "label": "What is your current annual compensation?", "value": "",
     "source": "unresolved", "confidence": 0.0, "required": True,
     "rationale": "matches a never_auto_answer rule"},
]

ANSWERS = [
    {"field_id": "f1", "label": "First name", "value": "Venkatesh", "source": "profile",
     "confidence": 1.0, "rationale": "profile.first_name", "required": True},
    {"field_id": "f2", "label": "Email", "value": "you@example.com", "source": "profile",
     "confidence": 1.0, "rationale": "profile.email", "required": True},
    {"field_id": "f3", "label": "Are you legally authorized to work in India?",
     "value": "Yes", "source": "answer_bank", "confidence": 1.0,
     "rationale": "answer bank rule: 'are you legally authorized to work'", "required": True},
    {"field_id": "f4", "label": "Will you now or in the future require sponsorship?",
     "value": "Yes", "source": "answer_bank", "confidence": 1.0,
     "rationale": "answer bank rule: 'will you now or in the future require sponsorship'",
     "required": True},
    {"field_id": "f5", "label": "Years of Kubernetes security experience", "value": "5",
     "source": "claude", "confidence": 0.81,
     "rationale": "resume evidences EKS admission control and cluster hardening since 2021",
     "required": False},
    {"field_id": "f6", "label": "Notice period", "value": "30 days", "source": "answer_bank",
     "confidence": 1.0, "rationale": "answer bank rule: 'notice period'", "required": True},
]


def seed(reset: bool = False) -> None:
    st = settings_mod.load()
    init_db()
    rng = random.Random(20260819)
    now = datetime.now(UTC)
    registry = ResumeRegistry.load()

    if reset:
        from sqlalchemy import delete

        from jobapply.models import Event, ScreenshotItem

        with session_scope() as sess:
            for model in (Event, EmailDraft, Application, ScreenshotItem, Job):
                sess.execute(delete(model))
        print("cleared existing rows")

    created = 0
    for idx, (title, company, location, workplace, vendor, apply_type, desc, age_h) in enumerate(SAMPLES):
        posted = now - timedelta(hours=age_h)
        with session_scope() as sess:
            job = Job(
                source=Source.MANUAL_SCREENSHOT if apply_type == ApplyType.EMAIL else Source.LINKEDIN,
                external_id=f"demo-{idx}",
                url=f"https://www.linkedin.com/jobs/view/40000{idx}/",
                apply_url=(f"https://boards.{vendor}.example/{company.lower().replace(' ', '-')}/apply"
                           if vendor else None),
                ats_vendor=vendor,
                title=title,
                company=f"{company} [demo]",
                location=location,
                workplace_type=workplace,
                description=desc,
                apply_type=apply_type,
                contact_email="careers@fernpath.example" if apply_type == ApplyType.EMAIL else None,
                posted_at=posted,
                search_name="demo seed",
                timezone=timing.resolve_timezone(location, st.config.timing.timezone_default),
                raw={"demo": True},
            )
            sess.add(job)
            sess.flush()

            match = select_resume(title, desc, registry=registry, use_llm=False,
                                  company=company, location=location)
            plan = timing.plan_send_time(
                posted_at=posted, location=location, cfg=st.config.timing,
                match_score=match.score, now=now, rng=rng,
            )
            status = STATUS_PLAN[idx % len(STATUS_PLAN)]
            channel = (Channel.EMAIL if apply_type == ApplyType.EMAIL
                       else Channel.LINKEDIN_EASY_APPLY if apply_type == ApplyType.EASY_APPLY
                       else Channel.ATS)

            app = Application(
                job_id=job.id,
                resume_key=match.resume_key,
                resume_path=str(match.resume.path) if match.resume else None,
                status=status,
                channel=channel,
                match_score=match.score,
                match_reasons=match.reasons,
                missing_skills=match.missing_skills or (["GCP at scale"] if idx % 3 == 0 else []),
                scheduled_for=plan.send_at,
                schedule_reason=plan.reason,
                created_at=now - timedelta(hours=age_h - 1),
                updated_at=now - timedelta(hours=max(0, age_h - 2)),
                answers=ANSWERS[: 4 + idx % 3],
                attempts=1,
            )
            if status == AppStatus.SUBMITTED:
                app.submitted_at = now - timedelta(hours=max(1, age_h - 3))
                app.confirmation = "Application submitted — thank you for applying."
            if status == AppStatus.NEEDS_REVIEW:
                app.open_questions = OPEN_QS[: 1 + idx % 2]
            if status == AppStatus.FAILED:
                app.error = "the apply page timed out after the third step"

            sess.add(app)
            sess.flush()

            log_event(sess, app, "matched",
                      f"scored {match.score:.0f} via keyword; chose resume {match.resume_key!r}")
            log_event(sess, app, "scheduled", plan.reason)
            if status == AppStatus.SUBMITTED:
                log_event(sess, app, "submitted", f"confirmed submission via {channel.value}")
            elif status == AppStatus.NEEDS_REVIEW:
                log_event(sess, app, "needs_review",
                          f"{len(app.open_questions)} required question(s) need a human answer")
            elif status == AppStatus.FAILED:
                log_event(sess, app, "error", app.error)

            if channel == Channel.EMAIL:
                sess.add(EmailDraft(
                    application_id=app.id,
                    to_addr="careers@fernpath.example",
                    subject=f"{title} — Venkatesh Golle",
                    body=(
                        "Hello,\n\n"
                        f"I saw your post about the {title} role and would like to be "
                        "considered. I have spent the last five years building detection "
                        "content as code — Sigma rules under CI validation, ATT&CK coverage "
                        "mapping, and alert tuning that took our true-positive rate from "
                        "single digits to the high sixties.\n\n"
                        "The Elastic side of your stack is where I have done the most recent "
                        "work, including the enrichment services that sit in front of triage.\n\n"
                        "My resume is attached. Happy to talk whenever suits you.\n\n"
                        "Regards,\nVenkatesh Golle"
                    ),
                    attachment_path=app.resume_path,
                    gmail_draft_id="r-demo-draft-001",
                ))
            created += 1

    print(f"seeded {created} demo applications — run `jobapply serve` to view them")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete existing rows first")
    seed(**vars(ap.parse_args()))
