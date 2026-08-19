"""Ashby — React app; fields render lazily and dropdowns are custom widgets."""

from __future__ import annotations

from ..browser import human_pause
from .base import ApplyContext, ApplyResult, click_button_by_text, register
from .generic_form import GenericFormApplier, dismiss_overlays


class AshbyApplier(GenericFormApplier):
    name = "ashby"

    def handles(self, url: str, vendor: str | None) -> bool:
        return vendor == "ashby" or "ashbyhq.com" in (url or "").lower()

    async def prepare(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        await dismiss_overlays(page)
        clicked = await click_button_by_text(page, ["Apply for this Job", "Apply", "Application"])
        if clicked:
            result.steps.append("ashby: opened application form")
        # The form mounts after hydration; introspecting too early sees nothing.
        try:
            await page.wait_for_selector("form input, form textarea", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        await human_pause(page, 0.8, 1.6)


register(AshbyApplier())
