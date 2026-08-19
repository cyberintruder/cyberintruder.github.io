"""Generic multi-step form applier.

This is both the fallback for unknown ATS platforms and the engine the vendor
appliers reuse. It runs a bounded loop:

    introspect page -> resolve answers -> fill -> upload resume -> advance

and stops the moment it hits a required question it cannot answer honestly, a
page it cannot advance past, or a confirmation message.
"""

from __future__ import annotations

import logging

from ..browser import human_pause, screenshot
from ..forms.answer import AnswerResolver
from ..forms.introspect import fill_field, introspect_form
from .base import (
    NEXT_LABELS,
    ApplyContext,
    ApplyResult,
    Outcome,
    click_button_by_text,
    is_submit_label,
    looks_confirmed,
    page_text,
    register,
    upload_resume,
)

log = logging.getLogger(__name__)

MAX_STEPS = 12


class GenericFormApplier:
    name = "generic"
    #: Vendor appliers set this to skip the "is this even an application form" check.
    trust_page = False

    def handles(self, url: str, vendor: str | None) -> bool:  # noqa: ARG002
        return True

    async def apply(self, page, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(outcome=Outcome.FAILED, message="did not reach a terminal state")
        resolver = AnswerResolver()

        await page.goto(ctx.apply_url, wait_until="domcontentloaded")
        await human_pause(page, 1.2, 2.4)
        await self.prepare(page, ctx, result)

        uploaded = False
        for step in range(1, MAX_STEPS + 1):
            body = await page_text(page)
            if looks_confirmed(body):
                result.outcome = Outcome.SUBMITTED
                result.confirmation = _confirmation_snippet(body)
                result.message = f"confirmed at step {step}"
                break

            result.steps.append(f"step {step}: {page.url[:120]}")

            if not uploaded and ctx.resume_path:
                uploaded = await upload_resume(page, ctx.resume_path)
                if uploaded:
                    result.steps.append("resume uploaded")
                    await human_pause(page, 1.5, 3.0)
                    await self.after_resume_upload(page, ctx, result)

            fields = await introspect_form(page)
            fields = [f for f in fields if f.kind != "file"]

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
                        f"{len(blocking)} required question(s) need a human answer: "
                        + "; ".join(r.label[:60] for r in blocking[:3])
                    )
                    if ctx.screenshot_evidence:
                        result.screenshots.append(await screenshot(page, f"review-step{step}", ctx.application_id))
                    return result

                by_id = {f.field_id: f for f in fields}
                filled = 0
                for res in resolutions:
                    if not res.resolved:
                        continue
                    fld = by_id.get(res.field_id)
                    if fld and await fill_field(page, fld, res.value):
                        filled += 1
                result.steps.append(f"filled {filled}/{len(fields)} fields")
                await human_pause(page, 0.6, 1.4)

            if ctx.cover_letter:
                await self._fill_cover_letter(page, ctx.cover_letter, result)

            if ctx.screenshot_evidence:
                shot = await screenshot(page, f"step{step}", ctx.application_id)
                if shot:
                    result.screenshots.append(shot)

            advanced = await self._advance(page, ctx, result, step)
            if advanced is None:
                result.outcome = Outcome.NEEDS_REVIEW
                result.message = f"no way forward from step {step} — needs a human look"
                return result
            if advanced == "dry_run":
                result.outcome = Outcome.DRY_RUN
                result.message = "reached the submit button; stopped because dry_run is on"
                return result

            await page.wait_for_load_state("domcontentloaded")
            await human_pause(page, 1.4, 2.6)
        else:
            result.outcome = Outcome.NEEDS_REVIEW
            result.message = f"still not submitted after {MAX_STEPS} steps"

        if ctx.screenshot_evidence:
            shot = await screenshot(page, "final", ctx.application_id)
            if shot:
                result.screenshots.append(shot)
        return result

    # ------------------------------------------------------------ extension points

    async def prepare(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        """Vendor hook: run before the first introspection (dismiss cookie
        banners, click 'Apply', sign in, etc.)."""
        await dismiss_overlays(page)

    async def after_resume_upload(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        """Vendor hook: e.g. Workday's autofill takes several seconds to settle."""
        return None

    # ------------------------------------------------------------------ internals

    async def _advance(self, page, ctx: ApplyContext, result: ApplyResult, step: int) -> str | None:
        """Click the next/submit control. Returns the label, 'dry_run', or None."""
        submit_first = [lbl for lbl in NEXT_LABELS if is_submit_label(lbl)]
        nav_first = [lbl for lbl in NEXT_LABELS if not is_submit_label(lbl)]

        # Prefer moving forward through the flow; only submit when nothing else
        # remains, so we never submit a half-filled multi-page form.
        clicked = await click_button_by_text(page, nav_first)
        if clicked:
            result.steps.append(f"clicked '{clicked}'")
            return clicked

        for label in submit_first:
            if ctx.dry_run:
                if await _submit_present(page, label):
                    result.steps.append(f"submit '{label}' present — halted for dry run")
                    if ctx.screenshot_evidence:
                        result.screenshots.append(
                            await screenshot(page, f"dryrun-submit-step{step}", ctx.application_id)
                        )
                    return "dry_run"
                continue
            clicked = await click_button_by_text(page, [label])
            if clicked:
                result.steps.append(f"clicked submit '{clicked}'")
                return clicked
        return None

    async def _fill_cover_letter(self, page, letter: str, result: ApplyResult) -> None:
        for sel in (
            "textarea[name*='cover' i]", "textarea[id*='cover' i]",
            "textarea[aria-label*='cover' i]", "textarea[placeholder*='cover' i]",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible() and not (await loc.input_value()).strip():
                    await loc.fill(letter)
                    result.steps.append("cover letter written")
                    return
            except Exception:  # noqa: BLE001
                continue


async def _submit_present(page, label: str) -> bool:
    try:
        loc = page.locator(f"button:has-text('{label}')").first
        return bool(await loc.count()) and await loc.is_visible()
    except Exception:  # noqa: BLE001
        return False


async def dismiss_overlays(page) -> None:
    """Cookie banners and modals eat clicks; clear the common ones."""
    labels = [
        "Accept all", "Accept All Cookies", "Accept cookies", "I accept",
        "Got it", "Agree", "Allow all", "Close",
    ]
    for label in labels:
        try:
            loc = page.locator(f"button:has-text('{label}')").first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2500)
                await page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            continue


def _confirmation_snippet(body: str) -> str:
    low = body.lower()
    for phrase in ("application submitted", "thank you for applying", "application received",
                   "we have received your application", "successfully submitted"):
        idx = low.find(phrase)
        if idx >= 0:
            return body[max(0, idx - 60): idx + 240].strip()
    return body[:240].strip()


register(GenericFormApplier())
