"""The timing engine decides when an application lands. It has to be predictable."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jobapply.settings import TimingConfig
from jobapply.timing import (
    bucket_keys,
    freshness_score,
    next_window_start,
    plan_send_time,
    resolve_timezone,
)

CFG = TimingConfig()
RNG = random.Random(7)


def at(y, m, d, hh, mm=0, tz="UTC"):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(UTC)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Bengaluru, India", "Asia/Kolkata"),
        ("Hyderabad, Telangana", "Asia/Kolkata"),
        ("San Francisco, CA", "America/Los_Angeles"),
        ("London, United Kingdom", "Europe/London"),
        ("Berlin, Germany", "Europe/Berlin"),
        ("Singapore", "Asia/Singapore"),
        ("Somewhere Unmapped", "Asia/Kolkata"),  # falls back to the default
    ],
)
def test_timezone_resolution(location, expected):
    assert resolve_timezone(location, CFG.timezone_default) == expected


def test_freshness_decays_to_zero():
    now = datetime.now(UTC)
    assert freshness_score(now - timedelta(hours=1), CFG, now) == 1.0
    assert freshness_score(now - timedelta(hours=CFG.ideal_freshness_hours), CFG, now) == 1.0
    assert freshness_score(now - timedelta(hours=CFG.max_freshness_hours + 1), CFG, now) == 0.0
    mid = freshness_score(now - timedelta(hours=72), CFG, now)
    assert 0.0 < mid < 1.0
    assert freshness_score(None, CFG, now) == 0.5  # unknown age is neutral


def test_stale_posts_are_skipped():
    now = at(2026, 8, 19, 9)
    plan = plan_send_time(
        posted_at=now - timedelta(hours=CFG.max_freshness_hours + 5),
        location="India", cfg=CFG, now=now, rng=RNG,
    )
    assert plan.skip is True


def test_very_fresh_post_goes_immediately():
    now = at(2026, 8, 19, 22)  # outside every send window, on purpose
    plan = plan_send_time(
        posted_at=now - timedelta(hours=1), location="India", cfg=CFG,
        match_score=90, now=now, rng=RNG,
    )
    assert plan.skip is False
    assert plan.send_at - now < timedelta(minutes=CFG.jitter_minutes)
    assert "first-mover" in plan.reason


def test_older_post_waits_for_a_send_window():
    now = at(2026, 8, 19, 2, tz="Asia/Kolkata")  # 02:00 local — nobody is reading
    plan = plan_send_time(
        posted_at=now - timedelta(hours=40), location="Bengaluru, India",
        cfg=CFG, match_score=80, now=now, rng=RNG,
    )
    local = plan.send_at.astimezone(ZoneInfo("Asia/Kolkata"))
    assert 8 <= local.hour <= 17, f"scheduled at {local}"
    assert local.weekday() in CFG.allowed_weekdays


def test_window_never_lands_on_a_weekend():
    # Saturday 09:00 IST
    now = at(2026, 8, 22, 9, tz="Asia/Kolkata")
    start, reason = next_window_start(now, CFG, "Asia/Kolkata")
    local = start.astimezone(ZoneInfo("Asia/Kolkata"))
    assert local.weekday() in CFG.allowed_weekdays
    assert local.weekday() not in (5, 6), reason


def test_daily_cap_pushes_to_tomorrow():
    now = at(2026, 8, 19, 9, tz="Asia/Kolkata")
    plan = plan_send_time(
        posted_at=now - timedelta(hours=30), location="India", cfg=CFG,
        now=now, sent_today=CFG.max_applications_per_day, rng=RNG,
    )
    assert plan.send_at > now + timedelta(hours=12)
    assert "daily cap" in plan.reason


def test_hourly_cap_delays_by_an_hour():
    now = at(2026, 8, 19, 9, tz="Asia/Kolkata")
    plan = plan_send_time(
        posted_at=now - timedelta(hours=2), location="India", cfg=CFG,
        now=now, sent_this_hour=CFG.max_applications_per_hour, rng=RNG,
    )
    assert plan.send_at >= now + timedelta(minutes=55)
    assert "hourly cap" in plan.reason


def test_minimum_spacing_between_sends():
    now = at(2026, 8, 19, 9, tz="Asia/Kolkata")
    last = now - timedelta(minutes=2)
    plan = plan_send_time(
        posted_at=now - timedelta(hours=1), location="India", cfg=CFG,
        now=now, last_sent_at=last, rng=RNG,
    )
    gap = (plan.send_at - last).total_seconds() / 60
    assert gap >= CFG.min_minutes_between_applications - 0.1


def test_priority_favours_fresh_high_scoring_jobs():
    now = at(2026, 8, 19, 9, tz="Asia/Kolkata")
    strong = plan_send_time(posted_at=now - timedelta(hours=30), location="India",
                            cfg=CFG, match_score=95, now=now, rng=random.Random(1))
    weak = plan_send_time(posted_at=now - timedelta(hours=140), location="India",
                          cfg=CFG, match_score=63, now=now, rng=random.Random(1))
    assert strong.priority > weak.priority


def test_bucket_keys_are_stable():
    when = at(2026, 8, 19, 14, 30, tz="Asia/Kolkata")
    day, hour = bucket_keys(when, "Asia/Kolkata")
    assert day == "day:2026-08-19"
    assert hour == "hour:2026-08-19T14"
