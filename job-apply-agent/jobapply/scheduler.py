"""Background scheduler.

Three jobs run on a cadence:

  * **discover** — LinkedIn search, on a slow cadence (searching constantly gets
    you rate-limited and finds nothing new).
  * **apply** — checks for applications whose send time has arrived. Runs
    frequently and cheaply; the timing engine decides what is actually due.
  * **screenshots** — ingests anything dropped in the inbox.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import pipeline
from . import settings as settings_mod
from .db import init_db
from .manual.watcher import ScreenshotWatcher

log = logging.getLogger(__name__)


def _run_async(coro_factory) -> None:
    try:
        asyncio.run(coro_factory())
    except Exception:  # noqa: BLE001
        log.exception("scheduled job failed")


def job_discover(headless: bool = True) -> None:
    log.info("scheduled discovery starting")
    _run_async(lambda: pipeline.discover(headless=headless))


def job_apply(headless: bool = True, batch: int = 3) -> None:
    _run_async(lambda: pipeline.run_due(limit=batch, headless=headless))


def job_screenshots() -> None:
    watcher = ScreenshotWatcher()
    for res in watcher.poll_once():
        log.info("screenshot -> %s", res)


def build_scheduler(
    *,
    discover_minutes: int = 90,
    apply_minutes: int = 5,
    screenshot_seconds: int = 30,
    headless: bool = True,
    batch: int = 3,
) -> BackgroundScheduler:
    init_db()
    settings_mod.load()
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        job_discover, IntervalTrigger(minutes=discover_minutes),
        args=[headless], id="discover", max_instances=1, coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    sched.add_job(
        job_apply, IntervalTrigger(minutes=apply_minutes),
        args=[headless, batch], id="apply", max_instances=1, coalesce=True,
    )
    sched.add_job(
        job_screenshots, IntervalTrigger(seconds=screenshot_seconds),
        id="screenshots", max_instances=1, coalesce=True,
    )
    return sched


def run_forever(**kwargs) -> None:
    import time

    sched = build_scheduler(**kwargs)
    sched.start()
    log.info("scheduler running — discovery, applications and screenshot ingest are live")
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        log.info("shutting down")
        sched.shutdown(wait=False)
