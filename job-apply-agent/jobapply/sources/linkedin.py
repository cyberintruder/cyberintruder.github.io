"""LinkedIn job discovery.

Scrapes the authenticated jobs search, then opens each result to capture the full
description and work out how the job is applied to (Easy Apply vs. an external
ATS). Selectors are grouped at the top because LinkedIn changes class names
regularly — when discovery breaks, this is the only block that needs editing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from ..browser import BrowserOptions, browser_context, human_pause
from ..settings import SearchSpec

log = logging.getLogger(__name__)

SEARCH_BASE = "https://www.linkedin.com/jobs/search/"
JOB_VIEW = "https://www.linkedin.com/jobs/view/{job_id}/"

# f_E experience level codes used by LinkedIn's search
EXPERIENCE_CODES = {
    "internship": "1", "entry": "2", "associate": "3",
    "mid": "4", "senior": "4", "director": "5", "executive": "6",
}

SEL = {
    "results_list": "div.jobs-search-results-list, div.scaffold-layout__list",
    "card": "li.jobs-search-results__list-item, li[data-occludable-job-id], div.job-card-container",
    "card_title": "a.job-card-list__title, a.job-card-container__link, a[class*='job-card-list__title']",
    "card_company": "span.job-card-container__primary-description, div.artdeco-entity-lockup__subtitle",
    "card_location": "li.job-card-container__metadata-item, div.artdeco-entity-lockup__caption",
    "detail_title": "h1.job-details-jobs-unified-top-card__job-title, h1.t-24",
    "detail_company": "div.job-details-jobs-unified-top-card__company-name a, div.job-details-jobs-unified-top-card__company-name",
    "detail_meta": "div.job-details-jobs-unified-top-card__primary-description-container, div.job-details-jobs-unified-top-card__tertiary-description-container",
    "detail_desc": "div.jobs-description__content, div#job-details, article.jobs-description__container",
    "detail_insights": "li.job-details-jobs-unified-top-card__job-insight",
    "see_more": "button.jobs-description__footer-button, button[aria-label*='see more']",
    "easy_apply_btn": "button.jobs-apply-button",
    "apply_btn_any": "button.jobs-apply-button, a.jobs-apply-button",
    "pagination_next": "button[aria-label='Next']",
}

_POSTED_RE = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>minute|hour|day|week|month)s?\s*ago", re.I
)
_JOBID_RE = re.compile(r"(?:/jobs/view/|currentJobId=)(\d+)")

ATS_HOSTS = {
    "myworkdayjobs.com": "workday",
    "workday.com": "workday",
    "wd1.myworkdayjobs.com": "workday",
    "greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "jobs.lever.co": "lever",
    "ashbyhq.com": "ashby",
    "jobs.ashbyhq.com": "ashby",
    "smartrecruiters.com": "smartrecruiters",
    "icims.com": "icims",
    "taleo.net": "taleo",
    "successfactors.com": "successfactors",
    "workable.com": "workable",
    "bamboohr.com": "bamboohr",
    "recruitee.com": "recruitee",
    "teamtailor.com": "teamtailor",
}


def detect_ats(url: str | None) -> str | None:
    if not url:
        return None
    low = url.lower()
    for host, vendor in ATS_HOSTS.items():
        if host in low:
            return vendor
    return "generic"


def parse_posted_ago(text: str, now: datetime | None = None) -> datetime | None:
    """'3 days ago' / 'Posted 5 hours ago' -> an absolute UTC timestamp."""
    now = now or datetime.now(UTC)
    m = _POSTED_RE.search(text or "")
    if not m:
        return None
    num = int(m.group("num"))
    unit = m.group("unit").lower()
    delta = {
        "minute": timedelta(minutes=num),
        "hour": timedelta(hours=num),
        "day": timedelta(days=num),
        "week": timedelta(weeks=num),
        "month": timedelta(days=30 * num),
    }[unit]
    return now - delta


def extract_job_id(url: str) -> str | None:
    m = _JOBID_RE.search(url or "")
    return m.group(1) if m else None


def build_search_url(spec: SearchSpec, start: int = 0) -> str:
    """Translate a SearchSpec into a LinkedIn jobs search URL."""
    params: dict[str, Any] = {"keywords": spec.keywords, "sortBy": "DD"}
    if spec.location:
        params["location"] = spec.location
    if spec.remote:
        params["f_WT"] = "2"
    if spec.posted_within_hours:
        params["f_TPR"] = f"r{int(spec.posted_within_hours) * 3600}"
    if spec.easy_apply_only:
        params["f_AL"] = "true"
    codes = sorted({EXPERIENCE_CODES[lvl] for lvl in spec.experience_levels if lvl in EXPERIENCE_CODES})
    if codes:
        params["f_E"] = ",".join(codes)
    if start:
        params["start"] = start
    return f"{SEARCH_BASE}?{urlencode(params)}"


@dataclass
class ScrapedJob:
    external_id: str
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    workplace_type: str | None = None
    seniority: str | None = None
    salary_text: str | None = None
    posted_at: datetime | None = None
    easy_apply: bool = False
    apply_url: str | None = None
    ats_vendor: str | None = None
    search_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def apply_type(self) -> str:
        if self.easy_apply:
            return "easy_apply"
        if self.apply_url:
            return "external"
        return "unknown"


class LinkedInSource:
    """Discovery against the authenticated LinkedIn jobs search."""

    def __init__(self, headless: bool = True, page_size: int = 25) -> None:
        self.headless = headless
        self.page_size = page_size

    async def search(self, specs: list[SearchSpec]) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        async with browser_context(BrowserOptions(headless=self.headless)) as page:
            for spec in specs:
                log.info("searching: %s", spec.name)
                try:
                    found = await self._search_one(page, spec, seen)
                except Exception as exc:  # noqa: BLE001 — one bad search must not kill the run
                    log.error("search %r failed: %s", spec.name, exc)
                    continue
                jobs.extend(found)
        return jobs

    async def _search_one(self, page, spec: SearchSpec, seen: set[str]) -> list[ScrapedJob]:
        results: list[ScrapedJob] = []
        start = 0
        while len(results) < spec.max_results:
            await page.goto(build_search_url(spec, start), wait_until="domcontentloaded")
            await human_pause(page, 1.5, 2.8)

            if "/authwall" in page.url or "/login" in page.url:
                raise RuntimeError("LinkedIn session expired — run `jobapply login`")

            await self._scroll_result_list(page)
            ids = await self._collect_job_ids(page)
            if not ids:
                break

            for job_id in ids:
                if job_id in seen or len(results) >= spec.max_results:
                    continue
                seen.add(job_id)
                try:
                    job = await self.fetch_detail(page, job_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("job %s detail failed: %s", job_id, exc)
                    continue
                job.search_name = spec.name
                results.append(job)
                await human_pause(page, 0.8, 2.0)

            start += self.page_size
            if start > spec.max_results * 2:
                break
        return results

    async def _scroll_result_list(self, page) -> None:
        """LinkedIn lazy-loads cards; scroll the pane so all of them render."""
        try:
            for _ in range(8):
                await page.mouse.wheel(0, 1400)
                await page.wait_for_timeout(450)
        except Exception:  # noqa: BLE001
            pass

    async def _collect_job_ids(self, page) -> list[str]:
        ids: list[str] = []
        for sel in ("li[data-occludable-job-id]", "div[data-job-id]"):
            for el in await page.query_selector_all(sel):
                val = await el.get_attribute("data-occludable-job-id") or await el.get_attribute("data-job-id")
                if val and val.isdigit() and val not in ids:
                    ids.append(val)
        if not ids:
            for a in await page.query_selector_all("a[href*='/jobs/view/']"):
                href = await a.get_attribute("href") or ""
                jid = extract_job_id(href)
                if jid and jid not in ids:
                    ids.append(jid)
        return ids

    async def fetch_detail(self, page, job_id: str) -> ScrapedJob:
        """Open a job page and pull everything needed to decide and apply."""
        url = JOB_VIEW.format(job_id=job_id)
        await page.goto(url, wait_until="domcontentloaded")
        await human_pause(page, 1.0, 2.0)

        # Expand the truncated description — matching on a partial JD produces
        # bad resume choices.
        try:
            more = await page.query_selector(SEL["see_more"])
            if more:
                await more.click()
                await page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass

        title = await _text(page, SEL["detail_title"])
        company = await _text(page, SEL["detail_company"])
        meta = await _text(page, SEL["detail_meta"])
        description = await _text(page, SEL["detail_desc"])
        insights = [await _el_text(el) for el in await page.query_selector_all(SEL["detail_insights"])]
        insight_blob = " · ".join(i for i in insights if i)

        job = ScrapedJob(
            external_id=job_id,
            url=url,
            title=title,
            company=company,
            location=_first_location(meta),
            description=description,
            posted_at=parse_posted_ago(meta) or parse_posted_ago(insight_blob),
            workplace_type=_workplace_type(f"{meta} {insight_blob}"),
            salary_text=_salary(insight_blob),
            raw={"meta": meta, "insights": insights},
        )

        btn = await page.query_selector(SEL["apply_btn_any"])
        btn_text = (await _el_text(btn) or "").lower() if btn else ""
        job.easy_apply = "easy apply" in btn_text

        if not job.easy_apply and btn:
            job.apply_url = await self._resolve_external_url(page, btn) or url
            job.ats_vendor = detect_ats(job.apply_url)

        return job

    async def _resolve_external_url(self, page, button) -> str | None:
        """External 'Apply' opens a new tab. Capture its URL without applying."""
        href = await button.get_attribute("href")
        if href and href.startswith("http") and "linkedin.com" not in href:
            return href
        try:
            async with page.context.expect_page(timeout=12000) as new_page_info:
                await button.click()
            new_page = await new_page_info.value
            await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
            target = new_page.url
            await new_page.close()
            return target
        except Exception as exc:  # noqa: BLE001
            log.debug("could not resolve external apply url: %s", exc)
            return None


# ------------------------------------------------------------------- helpers


async def _el_text(el) -> str:
    if el is None:
        return ""
    try:
        return (await el.inner_text()).strip()
    except Exception:  # noqa: BLE001
        return ""


async def _text(page, selector: str) -> str:
    for sel in selector.split(", "):
        el = await page.query_selector(sel.strip())
        text = await _el_text(el)
        if text:
            return text
    return ""


def _first_location(meta: str) -> str:
    parts = [p.strip() for p in re.split(r"[·|]", meta or "") if p.strip()]
    for p in parts:
        if not _POSTED_RE.search(p) and "applicant" not in p.lower():
            return p
    return parts[0] if parts else ""


def _workplace_type(blob: str) -> str | None:
    low = (blob or "").lower()
    for kind in ("remote", "hybrid", "on-site", "onsite"):
        if kind in low:
            return "onsite" if kind in ("on-site", "onsite") else kind
    return None


def _salary(blob: str) -> str | None:
    m = re.search(r"[₹$€£]\s?[\d,]+(?:\.\d+)?\s?(?:-|to|–)\s?[₹$€£]?\s?[\d,]+[KkMm]?(?:/\w+)?", blob or "")
    return m.group(0) if m else None
