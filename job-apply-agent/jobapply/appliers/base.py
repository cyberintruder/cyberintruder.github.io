"""Applier contract shared by every channel.

An applier drives one application to a terminal state and reports back what it
did. The important design rule: **an applier never silently guesses.** If it
cannot answer a required question it returns `NEEDS_REVIEW` with the exact open
questions attached, and the dashboard asks you.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..forms.answer import AnswerResolution

log = logging.getLogger(__name__)


class Outcome:
    SUBMITTED = "submitted"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class ApplyContext:
    """Everything an applier needs, assembled by the pipeline."""

    job_title: str
    company: str
    job_description: str
    apply_url: str
    resume_path: str
    resume_summary: str = ""
    cover_letter: str | None = None
    application_id: int | None = None
    dry_run: bool = True
    require_review: bool = True
    screenshot_evidence: bool = True


@dataclass
class ApplyResult:
    outcome: str
    message: str = ""
    answers: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    confirmation: str = ""
    steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.SUBMITTED, Outcome.DRY_RUN)

    def add_answers(self, resolutions: list[AnswerResolution]) -> None:
        seen = {a["field_id"] for a in self.answers}
        for r in resolutions:
            if r.field_id in seen:
                continue
            (self.open_questions if (r.required and not r.resolved) else self.answers).append(r.to_dict())
            seen.add(r.field_id)


class Applier(Protocol):
    name: str

    def handles(self, url: str, vendor: str | None) -> bool: ...

    async def apply(self, page, ctx: ApplyContext) -> ApplyResult: ...


_REGISTRY: list[Applier] = []


def register(applier: Applier) -> Applier:
    _REGISTRY.append(applier)
    return applier


def registry() -> list[Applier]:
    if not _REGISTRY:
        _load_builtin()
    return list(_REGISTRY)


def _load_builtin() -> None:
    from . import ashby, generic_form, greenhouse, lever, linkedin_easy_apply, workday  # noqa: F401


def pick_applier(url: str, vendor: str | None = None) -> Applier:
    """Choose the most specific applier for a URL; generic is the last resort."""
    candidates = registry()
    for applier in candidates:
        if applier.name != "generic" and applier.handles(url, vendor):
            return applier
    for applier in candidates:
        if applier.name == "generic":
            return applier
    raise RuntimeError("no applier registered")


# ------------------------------------------------------------------- helpers

NEXT_LABELS = [
    "save and continue", "save & continue", "continue", "next", "next step",
    "review", "proceed", "submit application", "submit", "apply", "done",
]
SUBMIT_LABELS = ["submit application", "submit", "send application", "apply now", "finish"]
CONFIRM_PATTERNS = [
    "application submitted", "thank you for applying", "your application has been",
    "we have received your application", "application received", "successfully submitted",
    "thanks for applying", "application complete",
]


def is_submit_label(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(low == lbl or low.startswith(lbl) for lbl in SUBMIT_LABELS)


def looks_confirmed(page_text: str) -> bool:
    low = (page_text or "").lower()
    return any(p in low for p in CONFIRM_PATTERNS)


async def click_button_by_text(page, labels: list[str], timeout_ms: int = 4000) -> str | None:
    """Click the first visible, enabled button matching any label. Returns it."""
    for label in labels:
        for sel in (
            f"button:has-text('{label}')",
            f"a[role='button']:has-text('{label}')",
            f"input[type='submit'][value='{label}' i]",
            f"[data-automation-id*='{label.replace(' ', '')}' i]",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible():
                    continue
                if await loc.is_disabled():
                    continue
                await loc.scroll_into_view_if_needed(timeout=timeout_ms)
                await loc.click(timeout=timeout_ms)
                return label
            except Exception:  # noqa: BLE001
                continue
    return None


async def page_text(page) -> str:
    try:
        return await page.inner_text("body")
    except Exception:  # noqa: BLE001
        return ""


async def upload_resume(page, resume_path: str) -> bool:
    """Find the resume file input and attach the chosen resume."""
    selectors = [
        "input[type='file'][name*='resume' i]",
        "input[type='file'][id*='resume' i]",
        "input[type='file'][name*='cv' i]",
        "input[type='file'][data-automation-id*='file' i]",
        "input[type='file']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.set_input_files(resume_path)
            await page.wait_for_timeout(1200)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False
