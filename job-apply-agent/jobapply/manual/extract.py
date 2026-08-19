"""Read a screenshot of a LinkedIn post and turn it into a structured job.

This is the manual half of the workflow: you scroll LinkedIn, screenshot the
posts worth applying to, and drop them in `inbox/screenshots/`. Claude reads the
image — including posts that are plain text with a "DM me" or "send your CV to
..." instruction, which have no job ID and cannot be scraped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import llm as llm_mod

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RE = re.compile(r"https?://[^\s)>\]]+")

SYSTEM = """You read screenshots of LinkedIn job posts and extract the facts.

- Transcribe only what is visible. Never infer a company, salary, email or URL
  that is not shown in the image.
- `description` should capture the responsibilities and requirements as written,
  not a summary — this text is used to choose which resume to send.
- `contact_email` is only set when an address is literally visible in the post.
- `apply_instructions` captures wording like "DM me", "send your CV to X",
  "comment interested", or "apply via the link".
- `is_job_post` is false for anything that is not a job opening (a hiring
  announcement with no role, an article, a meme, a profile page).
- `confidence` is how legible and complete the post is: 1.0 for a crisp full
  post, 0.4 for a partially cut-off one."""

_RELATIVE = re.compile(r"(\d+)\s*(m|h|d|w|mo)\b", re.I)


@dataclass
class ExtractedPost:
    is_job_post: bool
    title: str = ""
    company: str = ""
    location: str = ""
    workplace_type: str = "unknown"
    seniority: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)
    contact_name: str = ""
    contact_email: str = ""
    apply_url: str = ""
    apply_instructions: str = ""
    posted_ago: str = ""
    confidence: float = 0.0
    source_path: str = ""

    @property
    def posted_at(self) -> datetime | None:
        return parse_relative_age(self.posted_ago)

    @property
    def has_recipient(self) -> bool:
        return bool(self.contact_email)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "is_job_post": self.is_job_post, "title": self.title, "company": self.company,
            "location": self.location, "workplace_type": self.workplace_type,
            "seniority": self.seniority, "description": self.description,
            "skills": self.skills, "contact_name": self.contact_name,
            "contact_email": self.contact_email, "apply_url": self.apply_url,
            "apply_instructions": self.apply_instructions, "posted_ago": self.posted_ago,
            "confidence": self.confidence, "source_path": self.source_path,
        }
        return d


def parse_relative_age(text: str, now: datetime | None = None) -> datetime | None:
    """LinkedIn's '3d', '2 weeks ago', '5h' -> absolute UTC time."""
    now = now or datetime.now(UTC)
    if not text:
        return None
    low = text.lower()
    m = re.search(r"(\d+)\s*(minute|hour|day|week|month)", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        return now - {
            "minute": timedelta(minutes=n), "hour": timedelta(hours=n),
            "day": timedelta(days=n), "week": timedelta(weeks=n),
            "month": timedelta(days=30 * n),
        }[unit]
    m = _RELATIVE.search(low)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        return now - {
            "m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n),
            "w": timedelta(weeks=n), "mo": timedelta(days=30 * n),
        }[unit]
    return None


def extract_from_screenshot(path: Path, client: llm_mod.Claude | None = None) -> ExtractedPost:
    """Vision-extract one screenshot. Raises if no API key is configured."""
    path = Path(path)
    client = client or llm_mod.get_client()
    if not client.available():
        raise llm_mod.LLMUnavailable(
            "reading screenshots requires ANTHROPIC_API_KEY — there is no offline fallback for vision"
        )

    data = client.structured(
        "Extract the job post from this LinkedIn screenshot.",
        llm_mod.JOB_POST_SCHEMA,
        system=SYSTEM,
        images=[path],
        effort=llm_mod.EFFORT_STANDARD,
        max_tokens=6000,
    )
    post = ExtractedPost(
        is_job_post=bool(data.get("is_job_post")),
        title=(data.get("title") or "").strip(),
        company=(data.get("company") or "").strip(),
        location=(data.get("location") or "").strip(),
        workplace_type=data.get("workplace_type") or "unknown",
        seniority=(data.get("seniority") or "").strip(),
        description=(data.get("description") or "").strip(),
        skills=[s for s in (data.get("skills") or []) if s],
        contact_name=(data.get("contact_name") or "").strip(),
        contact_email=(data.get("contact_email") or "").strip().lower(),
        apply_url=(data.get("apply_url") or "").strip(),
        apply_instructions=(data.get("apply_instructions") or "").strip(),
        posted_ago=(data.get("posted_ago") or "").strip(),
        confidence=float(data.get("confidence") or 0.0),
        source_path=str(path),
    )

    # Belt and braces: an address the model transcribed into another field still
    # counts, but anything that is not a well-formed address is dropped.
    if post.contact_email and not EMAIL_RE.fullmatch(post.contact_email):
        blob = f"{post.contact_email} {post.apply_instructions} {post.description}"
        m = EMAIL_RE.search(blob)
        post.contact_email = m.group(0).lower() if m else ""
    if not post.contact_email:
        m = EMAIL_RE.search(f"{post.apply_instructions} {post.description}")
        if m:
            post.contact_email = m.group(0).lower()
    if post.apply_url and not URL_RE.fullmatch(post.apply_url):
        m = URL_RE.search(f"{post.apply_url} {post.apply_instructions} {post.description}")
        post.apply_url = m.group(0) if m else ""
    return post
