"""Playwright session management.

Authentication model: **you log in once, by hand.** `jobapply login` opens a real
browser, you sign in to LinkedIn (including MFA), and the storage state is saved
to `data/browser/linkedin.json`. Every later run reuses that state. No password is
ever stored by this project, and no credential-stuffing path exists.
"""

from __future__ import annotations

import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from . import settings as settings_mod

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class BrowserOptions:
    headless: bool = True
    slow_mo_ms: int = 0
    state_name: str = "linkedin"
    viewport: tuple[int, int] = (1440, 900)
    timeout_ms: int = 30000


def state_path(name: str = "linkedin") -> Path:
    st = settings_mod.load()
    st.ensure_dirs()
    return st.browser_dir / f"{name}.json"


def has_session(name: str = "linkedin") -> bool:
    return state_path(name).exists()


@asynccontextmanager
async def browser_context(opts: BrowserOptions | None = None):
    """Yield a ready Playwright page with the saved session loaded."""
    from playwright.async_api import async_playwright

    opts = opts or BrowserOptions()
    sp = state_path(opts.state_name)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=opts.headless,
            slow_mo=opts.slow_mo_ms,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            storage_state=str(sp) if sp.exists() else None,
            user_agent=UA,
            viewport={"width": opts.viewport[0], "height": opts.viewport[1]},
            locale="en-US",
        )
        ctx.set_default_timeout(opts.timeout_ms)
        # Remove the most obvious automation tell.
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await ctx.new_page()
        try:
            yield page
        finally:
            try:
                if sp.exists():
                    await ctx.storage_state(path=str(sp))
            except Exception:  # noqa: BLE001
                pass
            await ctx.close()
            await browser.close()


async def interactive_login(state_name: str = "linkedin", url: str = "https://www.linkedin.com/login") -> Path:
    """Open a visible browser so the user can sign in; persist the session."""
    from playwright.async_api import async_playwright

    sp = state_path(state_name)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(
            user_agent=UA, viewport={"width": 1440, "height": 900}, locale="en-US"
        )
        page = await ctx.new_page()
        await page.goto(url)
        print("\n  A browser window is open.")
        print("  Sign in (complete MFA if prompted), then return here and press Enter.\n")
        try:
            input("  Press Enter once you are signed in... ")
        except EOFError:
            await page.wait_for_timeout(120_000)
        await ctx.storage_state(path=str(sp))
        await ctx.close()
        await browser.close()
    log.info("session saved to %s", sp)
    return sp


async def human_pause(page, lo: float = 0.4, hi: float = 1.4) -> None:
    """Small randomised delay. Automation that fires instantly looks like a bot
    and gets rate-limited; this keeps interactions in a human cadence."""
    await page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


async def screenshot(page, label: str, app_id: int | None = None) -> str:
    """Capture evidence into the artifacts dir and return the relative path."""
    st = settings_mod.load()
    sub = st.artifacts_dir / (f"app-{app_id}" if app_id else "misc")
    sub.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:60]
    dest = sub / f"{safe}-{random.randint(1000, 9999)}.png"
    try:
        await page.screenshot(path=str(dest), full_page=False)
    except Exception as exc:  # noqa: BLE001
        log.debug("screenshot failed: %s", exc)
        return ""
    return str(dest.relative_to(st.env.data_dir))
