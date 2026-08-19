"""Tailored resume generation.

The rule that makes this safe to automate: **the model may re-emphasise, reorder
and rewrite phrasing, but may not invent employers, titles, dates, certifications
or metrics.** The prompt states it, and `validate_tailored()` checks it afterwards
by comparing the structural facts of the two documents. A tailored resume that
fails validation is discarded and the base resume is used instead.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import llm as llm_mod
from . import render
from . import settings as settings_mod
from .resumes import Resume

log = logging.getLogger(__name__)

TAILOR_SYSTEM = """You adapt an existing resume to a specific job description.

HARD RULES — breaking any of these makes the output unusable:
1. Never invent, add, remove or alter an employer name, job title, date range,
   degree, certification or numeric metric. Copy them exactly as written.
2. Never claim a skill or technology the source resume does not already evidence.
3. Keep every placeholder token (anything inside angle brackets) exactly as-is.
4. Keep the same markdown section structure and heading levels.

WHAT YOU MAY DO:
- Rewrite the Summary so it speaks to this specific role.
- Reorder bullets within a role so the most relevant work is first.
- Rephrase bullets to use the job description's vocabulary where the underlying
  work genuinely matches.
- Reorder and re-weight the skills list; drop skills irrelevant to this job.
- Drop at most one bullet per role if it is clearly irrelevant.

Return ONLY the complete tailored resume in markdown. No preamble, no commentary."""

# Facts that must survive tailoring untouched.
_HEADING = re.compile(r"^#{1,3}\s+(.*)$", re.M)
_DATE_RANGE = re.compile(r"\b(\d{4})\b")
_CERT_WORDS = re.compile(r"\b(CISSP|OSCP|CEH|CCSP|CKS|CRTP|OSEP|GCIA|GCDA|TOGAF|CCSK)\b", re.I)


@dataclass
class TailorResult:
    ok: bool
    path: Path | None
    text: str = ""
    reason: str = ""


def _facts(text: str) -> dict[str, set[str]]:
    return {
        "headings": {h.strip().lower() for h in _HEADING.findall(text)},
        "years": set(_DATE_RANGE.findall(text)),
        "certs": {c.upper() for c in _CERT_WORDS.findall(text)},
    }


def validate_tailored(original: str, tailored: str) -> tuple[bool, str]:
    """Reject a tailored resume that changed something it was not allowed to."""
    if len(tailored) < len(original) * 0.55:
        return False, "tailored resume lost more than 45% of its content"

    a, b = _facts(original), _facts(tailored)

    lost_certs = a["certs"] - b["certs"]
    if lost_certs:
        return False, f"certifications dropped: {', '.join(sorted(lost_certs))}"
    new_certs = b["certs"] - a["certs"]
    if new_certs:
        return False, f"certifications invented: {', '.join(sorted(new_certs))}"

    new_years = b["years"] - a["years"]
    if new_years:
        return False, f"dates invented: {', '.join(sorted(new_years))}"

    # Employer/role headings (### level) must be preserved verbatim.
    lost_headings = a["headings"] - b["headings"]
    material = {h for h in lost_headings if "—" in h or "-" in h}
    if material:
        return False, f"experience headings altered: {'; '.join(sorted(material))[:200]}"

    return True, "ok"


def tailor_resume(
    resume: Resume,
    *,
    job_title: str,
    company: str,
    description: str,
    out_dir: Path | None = None,
) -> TailorResult:
    """Produce a job-specific variant of `resume`, or explain why it fell back."""
    st = settings_mod.load()
    client = llm_mod.get_client()

    if resume.fmt != "md":
        return TailorResult(False, None, reason=f"tailoring needs a markdown source, got .{resume.fmt}")
    if not client.available():
        return TailorResult(False, None, reason="no ANTHROPIC_API_KEY configured")

    original = resume.path.read_text(encoding="utf-8")
    prompt = (
        f"TARGET JOB\ntitle: {job_title}\ncompany: {company}\n\n"
        f"job description:\n{description[:14000]}\n\n"
        f"SOURCE RESUME (markdown)\n---\n{original}\n---\n\n"
        "Produce the tailored resume."
    )
    try:
        tailored = client.complete(
            prompt,
            system=TAILOR_SYSTEM,
            max_tokens=16000,
            effort=llm_mod.EFFORT_CAREFUL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("tailoring failed: %s", exc)
        return TailorResult(False, None, reason=f"model call failed: {exc}")

    tailored = _strip_fences(tailored)
    ok, why = validate_tailored(original, tailored)
    if not ok:
        log.warning("tailored resume rejected for %s @ %s: %s", job_title, company, why)
        return TailorResult(False, None, text=tailored, reason=why)

    out_dir = Path(out_dir or (st.generated_dir / "tailored"))
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(f"{company}-{job_title}")[:70]
    md_path = out_dir / f"{resume.key}--{slug}-{date.today():%Y%m%d}.md"
    md_path.write_text(tailored, encoding="utf-8")

    pdf_path = render.render_markdown_pdf(md_path, out_dir=out_dir)
    return TailorResult(True, pdf_path, text=tailored, reason="ok")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
