"""Workday.

Workday is the hardest common target: it needs an account, spreads the
application across five or six named pages, and renders its dropdowns as
button/listbox widgets rather than <select>. This applier reuses the generic
engine and adds the Workday-specific parts:

  * `Apply` -> `Apply Manually` / `Autofill with Resume` entry choice
  * sign-in vs. create-account branch (account creation is opt-in, see below)
  * the wait for Workday's resume parser to populate My Experience
  * page-name aware progress so we know when we're on the Review page
"""

from __future__ import annotations

import logging

from ..browser import human_pause
from .base import ApplyContext, ApplyResult, Outcome, click_button_by_text, page_text, register
from .generic_form import GenericFormApplier, dismiss_overlays

log = logging.getLogger(__name__)

PAGE_ORDER = [
    "my information", "my experience", "application questions",
    "voluntary disclosures", "self identify", "review",
]


class WorkdayApplier(GenericFormApplier):
    name = "workday"

    def handles(self, url: str, vendor: str | None) -> bool:
        return vendor == "workday" or "myworkdayjobs.com" in (url or "").lower()

    async def prepare(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        await dismiss_overlays(page)

        # 1. The job page has an Apply button before the form exists.
        clicked = await click_button_by_text(page, ["Apply", "Apply Now"])
        if clicked:
            result.steps.append("workday: opened apply flow")
            await human_pause(page, 1.5, 2.8)

        # 2. Workday offers "Autofill with Resume" / "Apply Manually".
        #    Autofill is better: it parses the resume and prefills My Experience.
        chosen = await click_button_by_text(page, ["Autofill with Resume", "Apply Manually", "Use My Last Application"])
        if chosen:
            result.steps.append(f"workday: chose '{chosen}'")
            await human_pause(page, 1.2, 2.2)

        await self._handle_account(page, ctx, result)

    async def _handle_account(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        """Sign in if a Workday account session exists; otherwise stop.

        Creating an account sets a password on a third-party system and cannot be
        undone by this tool, so it is never done automatically. Sign in once by
        hand (`jobapply login --site workday --url <tenant url>`) and the saved
        session is reused from then on.
        """
        body = (await page_text(page)).lower()
        if "sign in" not in body and "create account" not in body:
            return

        signed_in = await click_button_by_text(page, ["Sign In"])
        if signed_in:
            await human_pause(page, 1.5, 3.0)
            body = (await page_text(page)).lower()
            if "password" not in body:
                result.steps.append("workday: reused saved session")
                return

        result.outcome = Outcome.NEEDS_REVIEW
        result.message = (
            "Workday tenant requires an account. Run "
            "`jobapply login --site workday --url <tenant url>` once, then retry."
        )
        raise _NeedsAccount(result.message)

    async def after_resume_upload(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        # Workday's parser rewrites the whole My Experience section; introspecting
        # before it settles produces fields that get wiped a second later.
        await page.wait_for_timeout(4500)
        try:
            await page.wait_for_selector("[data-automation-id='formField-firstName'], input", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        result.steps.append("workday: waited for resume parsing")

    async def apply(self, page, ctx: ApplyContext) -> ApplyResult:
        try:
            return await super().apply(page, ctx)
        except _NeedsAccount as exc:
            return ApplyResult(outcome=Outcome.NEEDS_REVIEW, message=str(exc))


class _NeedsAccount(RuntimeError):
    pass


register(WorkdayApplier())
