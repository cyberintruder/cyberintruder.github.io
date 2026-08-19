"""Lever — /apply page, single form, straightforward field names."""

from __future__ import annotations

from ..browser import human_pause
from .base import ApplyContext, ApplyResult, click_button_by_text, register
from .generic_form import GenericFormApplier, dismiss_overlays


class LeverApplier(GenericFormApplier):
    name = "lever"

    def handles(self, url: str, vendor: str | None) -> bool:
        return vendor == "lever" or "lever.co" in (url or "").lower()

    async def prepare(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        await dismiss_overlays(page)
        # Lever posting pages link to the form at <posting-url>/apply.
        if "/apply" not in page.url:
            target = page.url.rstrip("/") + "/apply"
            try:
                await page.goto(target, wait_until="domcontentloaded")
                result.steps.append("lever: navigated to /apply")
                await human_pause(page, 0.8, 1.6)
            except Exception:  # noqa: BLE001
                await click_button_by_text(page, ["Apply for this job", "Apply"])


register(LeverApplier())
