"""Greenhouse — single-page form, occasionally embedded in an iframe."""

from __future__ import annotations

from ..browser import human_pause
from .base import ApplyContext, ApplyResult, click_button_by_text, register
from .generic_form import GenericFormApplier, dismiss_overlays


class GreenhouseApplier(GenericFormApplier):
    name = "greenhouse"

    def handles(self, url: str, vendor: str | None) -> bool:
        low = (url or "").lower()
        return vendor == "greenhouse" or "greenhouse.io" in low

    async def prepare(self, page, ctx: ApplyContext, result: ApplyResult) -> None:
        await dismiss_overlays(page)
        clicked = await click_button_by_text(page, ["Apply", "Apply for this job", "Apply now"])
        if clicked:
            result.steps.append("greenhouse: opened application form")
            await human_pause(page, 1.0, 2.0)

        # Older embeds put the whole form in #grnhse_iframe. Navigating straight
        # to the iframe source gives the generic engine a normal page to work on.
        try:
            frame_el = await page.query_selector("iframe#grnhse_iframe, iframe[src*='greenhouse']")
            if frame_el:
                src = await frame_el.get_attribute("src")
                if src:
                    await page.goto(src, wait_until="domcontentloaded")
                    result.steps.append("greenhouse: followed embedded iframe")
                    await human_pause(page, 1.0, 2.0)
        except Exception:  # noqa: BLE001
            pass


register(GreenhouseApplier())
