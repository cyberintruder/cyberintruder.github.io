"""LinkedIn Easy Apply.

Easy Apply is a modal wizard: Contact info -> Resume -> optional screening
questions -> Review -> Submit. The modal is a normal DOM subtree, so the generic
introspection engine works inside it; what differs is navigation (Next/Review/
Submit buttons inside the modal) and the resume chooser.
"""

from __future__ import annotations

import logging

from ..browser import human_pause, screenshot
from ..forms.answer import AnswerResolver
from ..forms.introspect import fill_field, introspect_form
from .base import ApplyContext, ApplyResult, Outcome, register

log = logging.getLogger(__name__)

MODAL = "div.jobs-easy-apply-modal, div[role='dialog'][aria-labelledby*='easy-apply'], div[data-test-modal]"
SEL = {
    "apply_button": "button.jobs-apply-button",
    "next": "button[aria-label='Continue to next step'], button[aria-label='Continue']",
    "review": "button[aria-label='Review your application']",
    "submit": "button[aria-label='Submit application']",
    "dismiss": "button[aria-label='Dismiss']",
    "discard": "button[data-control-name='discard_application_confirm_btn']",
    "upload_resume": "input[type='file'][name='file']",
    "choose_resume": "button:has-text('Choose'), label:has-text('Upload resume')",
    "error": "div.artdeco-inline-feedback--error, p.fb-dash-form-element__error-text",
    "progress": "progress, div.artdeco-completeness-meter-linear__progress-element",
}

MAX_STEPS = 10


class LinkedInEasyApplyApplier:
    name = "linkedin_easy_apply"

    def handles(self, url: str, vendor: str | None) -> bool:
        return vendor == "linkedin_easy_apply" or "linkedin.com/jobs/view" in (url or "").lower()

    async def apply(self, page, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(outcome=Outcome.FAILED, message="easy apply did not complete")
        resolver = AnswerResolver()

        if page.url.split("?")[0] != ctx.apply_url.split("?")[0]:
            await page.goto(ctx.apply_url, wait_until="domcontentloaded")
            await human_pause(page, 1.2, 2.2)

        btn = page.locator(SEL["apply_button"]).first
        if await btn.count() == 0:
            return ApplyResult(outcome=Outcome.SKIPPED, message="no Easy Apply button on this job")
        label = (await btn.inner_text()).strip().lower()
        if "easy apply" not in label:
            return ApplyResult(outcome=Outcome.SKIPPED, message="job uses an external application")

        await btn.click()
        await human_pause(page, 1.2, 2.4)
        try:
            await page.wait_for_selector(MODAL, timeout=12000)
        except Exception:  # noqa: BLE001
            return ApplyResult(outcome=Outcome.FAILED, message="Easy Apply modal never opened")

        result.steps.append("easy apply modal opened")
        uploaded = False

        for step in range(1, MAX_STEPS + 1):
            await human_pause(page, 0.6, 1.3)

            if not uploaded and ctx.resume_path:
                uploaded = await self._attach_resume(page, ctx.resume_path, result)

            fields = [f for f in await introspect_form(page, root=MODAL.split(",")[0].strip())
                      if f.kind != "file"]
            if fields:
                resolutions = resolver.resolve_all(
                    fields,
                    job_title=ctx.job_title,
                    company=ctx.company,
                    job_description=ctx.job_description,
                    resume_summary=ctx.resume_summary,
                )
                result.add_answers(resolutions)

                blocking = [r for r in resolutions if r.required and not r.resolved]
                if blocking and ctx.require_review:
                    result.outcome = Outcome.NEEDS_REVIEW
                    result.message = (
                        f"{len(blocking)} screening question(s) need a human answer: "
                        + "; ".join(r.label[:60] for r in blocking[:3])
                    )
                    if ctx.screenshot_evidence:
                        result.screenshots.append(await screenshot(page, f"ea-review{step}", ctx.application_id))
                    await self._close_and_save(page)
                    return result

                by_id = {f.field_id: f for f in fields}
                for res in resolutions:
                    if res.resolved and (fld := by_id.get(res.field_id)):
                        await fill_field(page, fld, res.value)
                result.steps.append(f"step {step}: answered {len(fields)} field(s)")

            if ctx.screenshot_evidence:
                shot = await screenshot(page, f"ea-step{step}", ctx.application_id)
                if shot:
                    result.screenshots.append(shot)

            # Validation errors mean the step did not accept our answers.
            errors = await self._errors(page)
            if errors:
                result.outcome = Outcome.NEEDS_REVIEW
                result.message = "LinkedIn rejected an answer: " + "; ".join(errors[:3])
                await self._close_and_save(page)
                return result

            submit = page.locator(SEL["submit"]).first
            if await submit.count() and await submit.is_visible():
                if ctx.dry_run:
                    result.outcome = Outcome.DRY_RUN
                    result.message = "reached Submit; stopped because dry_run is on"
                    await self._close_and_save(page)
                    return result
                await submit.click()
                await human_pause(page, 2.0, 3.5)
                result.outcome = Outcome.SUBMITTED
                result.confirmation = await self._confirmation(page)
                result.message = f"submitted after {step} step(s)"
                await self._dismiss(page)
                return result

            moved = await self._next(page)
            if not moved:
                result.outcome = Outcome.NEEDS_REVIEW
                result.message = f"stuck on Easy Apply step {step} — no Next/Review/Submit available"
                await self._close_and_save(page)
                return result
            result.steps.append(f"advanced past step {step}")

        result.outcome = Outcome.NEEDS_REVIEW
        result.message = f"Easy Apply exceeded {MAX_STEPS} steps"
        await self._close_and_save(page)
        return result

    # ------------------------------------------------------------------ pieces

    async def _attach_resume(self, page, resume_path: str, result: ApplyResult) -> bool:
        try:
            file_input = page.locator(SEL["upload_resume"]).first
            if await file_input.count():
                await file_input.set_input_files(resume_path)
                await page.wait_for_timeout(1800)
                result.steps.append("resume uploaded")
                return True
        except Exception as exc:  # noqa: BLE001
            log.debug("easy apply resume upload: %s", exc)
        # Some jobs only offer previously uploaded resumes; that is acceptable —
        # LinkedIn will attach the default one.
        return False

    async def _next(self, page) -> bool:
        for sel in (SEL["next"], SEL["review"]):
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible() and not await loc.is_disabled():
                    await loc.click()
                    await human_pause(page, 1.0, 2.0)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _errors(self, page) -> list[str]:
        out: list[str] = []
        try:
            for el in await page.query_selector_all(SEL["error"]):
                if await el.is_visible():
                    text = (await el.inner_text()).strip()
                    if text:
                        out.append(text)
        except Exception:  # noqa: BLE001
            pass
        return out

    async def _confirmation(self, page) -> str:
        try:
            body = await page.inner_text("body")
        except Exception:  # noqa: BLE001
            return "submitted"
        for phrase in ("Your application was sent", "Application sent", "Done"):
            if phrase.lower() in body.lower():
                idx = body.lower().find(phrase.lower())
                return body[idx: idx + 200].strip()
        return "submitted"

    async def _dismiss(self, page) -> None:
        try:
            loc = page.locator(SEL["dismiss"]).first
            if await loc.count():
                await loc.click()
                await page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass

    async def _close_and_save(self, page) -> None:
        """Close the modal keeping LinkedIn's draft, so a human can finish it."""
        try:
            loc = page.locator(SEL["dismiss"]).first
            if await loc.count():
                await loc.click()
                await page.wait_for_timeout(600)
                # "Save" keeps the partial application; "Discard" throws it away.
                save = page.locator("button:has-text('Save')").first
                if await save.count() and await save.is_visible():
                    await save.click()
        except Exception:  # noqa: BLE001
            pass


register(LinkedInEasyApplyApplier())
