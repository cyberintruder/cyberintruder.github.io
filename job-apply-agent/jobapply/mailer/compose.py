"""Write the application email.

Claude drafts it when a key is available; otherwise a deterministic template is
used so the manual flow still produces a real, sendable draft offline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .. import llm as llm_mod
from .. import settings as settings_mod

log = logging.getLogger(__name__)

SYSTEM = """You write short job application emails on behalf of a candidate.

Requirements:
- 110-180 words in the body. Recruiters skim; long emails lose.
- Open by naming the exact role and where you saw it. No "I hope this finds you well".
- Two or three sentences of concrete, specific overlap between the candidate's
  actual background and the posting's actual requirements. Reference real things
  from the profile — never invent experience, employers, metrics or tools.
- One short closing line offering to talk. No begging, no superlatives, no
  "I am passionate about".
- Plain text only. No markdown, no bullet characters, no emoji.
- Do not write a signature or sign-off name — one is appended automatically.
- Subject line: "<Role> — <Candidate name>" unless the post asks for a specific
  subject, in which case follow the post."""


@dataclass
class ComposedEmail:
    subject: str
    body: str
    source: str  # claude | template

    def with_signature(self, signature: str) -> ComposedEmail:
        if not signature.strip():
            return self
        body = self.body.rstrip() + "\n\n" + signature.strip() + "\n"
        return ComposedEmail(self.subject, body, self.source)


def compose_application_email(
    *,
    job_title: str,
    company: str,
    job_description: str,
    resume_role: str,
    resume_summary: str,
    apply_instructions: str = "",
    contact_name: str = "",
    client: llm_mod.Claude | None = None,
) -> ComposedEmail:
    st = settings_mod.load()
    profile = st.config.profile
    client = client or llm_mod.get_client()

    if not client.available():
        return _template(job_title, company, contact_name, profile, resume_role)

    prompt = (
        f"CANDIDATE\nname: {profile.full_name}\nheadline: {profile.headline}\n"
        f"current: {profile.current_title} at {profile.current_company}\n"
        f"years of experience: {profile.years_experience}\n"
        f"location: {profile.city}, {profile.country}\n"
        f"pitch: {' '.join((profile.pitch or '').split())}\n\n"
        f"RESUME BEING ATTACHED\nvariant: {resume_role}\nemphasis: {' '.join(resume_summary.split())}\n\n"
        f"JOB\ntitle: {job_title}\ncompany: {company}\n"
        f"recipient: {contact_name or 'the hiring team'}\n"
        f"how they asked people to apply: {apply_instructions or 'not specified'}\n\n"
        f"job post text:\n{(job_description or '')[:8000]}\n\n"
        "Write the email."
    )
    try:
        data = client.structured(
            prompt, llm_mod.EMAIL_SCHEMA, system=SYSTEM,
            effort=llm_mod.EFFORT_CAREFUL, max_tokens=4000,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email composition fell back to template: %s", exc)
        return _template(job_title, company, contact_name, profile, resume_role)

    subject = _clean(data.get("subject") or f"{job_title} — {profile.full_name}")
    body = (data.get("body") or "").strip()
    if not body:
        return _template(job_title, company, contact_name, profile, resume_role)
    return ComposedEmail(subject=subject, body=body, source="claude")


def _template(job_title: str, company: str, contact_name: str, profile, resume_role: str) -> ComposedEmail:
    greeting = f"Hi {contact_name.split()[0]}," if contact_name else "Hello,"
    at_company = f" at {company}" if company else ""
    body = (
        f"{greeting}\n\n"
        f"I saw your post about the {job_title} role{at_company} on LinkedIn and I would "
        f"like to be considered.\n\n"
        f"I am a {profile.current_title or profile.headline} with {profile.years_experience} "
        f"years of experience, currently at {profile.current_company or 'my present employer'}. "
        f"My background lines up closely with this role — I have attached the resume variant "
        f"covering my {resume_role} work so the most relevant experience is on the first page.\n\n"
        f"Happy to walk through any of it at a time that suits you.\n"
    )
    return ComposedEmail(
        subject=f"{job_title} — {profile.full_name}".strip(" —"),
        body=body,
        source="template",
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:250]
