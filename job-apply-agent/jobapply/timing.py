"""When to send.

Applying the instant a job is scraped is not the same as applying at the right
time. Three forces are traded off here:

* **Freshness** — recruiters work the queue in arrival order. Being early is the
  single strongest lever, which is why a very fresh post bypasses the window.
* **Attention** — an application that lands at 03:00 local time sits under a
  night's worth of other applications by the time anyone looks. Weekday mornings
  in the *hiring team's* timezone are when queues get worked.
* **Throughput safety** — bursts look automated. Per-hour and per-day caps plus
  jitter keep the account's behaviour in a human envelope.

`plan_send_time()` returns the timestamp plus a plain-English reason, which is
what the dashboard shows next to every queued application.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import TimingConfig

# Coarse location -> timezone map. Anything unmatched falls back to the
# configured default, which is correct for the common case of applying from and
# to the same country.
LOCATION_TZ: list[tuple[str, str]] = [
    (r"\b(india|bengaluru|bangalore|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|kolkata)\b", "Asia/Kolkata"),
    (r"\b(singapore)\b", "Asia/Singapore"),
    (r"\b(dubai|uae|abu dhabi)\b", "Asia/Dubai"),
    (r"\b(london|united kingdom|uk|england|manchester|edinburgh)\b", "Europe/London"),
    (r"\b(ireland|dublin)\b", "Europe/Dublin"),
    (r"\b(germany|berlin|munich|frankfurt|hamburg)\b", "Europe/Berlin"),
    (r"\b(netherlands|amsterdam)\b", "Europe/Amsterdam"),
    (r"\b(france|paris)\b", "Europe/Paris"),
    (r"\b(spain|madrid|barcelona)\b", "Europe/Madrid"),
    (r"\b(poland|warsaw|krakow)\b", "Europe/Warsaw"),
    (r"\b(new york|nyc|boston|atlanta|miami|toronto|ottawa)\b", "America/New_York"),
    (r"\b(chicago|austin|dallas|houston|texas|minneapolis)\b", "America/Chicago"),
    (r"\b(denver|colorado|salt lake)\b", "America/Denver"),
    (r"\b(san francisco|seattle|los angeles|california|bay area|portland|vancouver)\b", "America/Los_Angeles"),
    (r"\b(australia|sydney|melbourne)\b", "Australia/Sydney"),
    (r"\b(japan|tokyo)\b", "Asia/Tokyo"),
    (r"\b(israel|tel aviv)\b", "Asia/Jerusalem"),
]


@dataclass
class SendPlan:
    send_at: datetime
    reason: str
    priority: float = 0.0       # higher = work this one first
    skip: bool = False

    def to_dict(self) -> dict:
        return {
            "send_at": self.send_at.isoformat(),
            "reason": self.reason,
            "priority": round(self.priority, 2),
            "skip": self.skip,
        }


def resolve_timezone(location: str | None, default: str = "Asia/Kolkata") -> str:
    low = (location or "").lower()
    for pattern, tz in LOCATION_TZ:
        if re.search(pattern, low):
            return tz
    return default


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _parse_hm(value: str) -> time:
    hh, _, mm = value.partition(":")
    return time(int(hh), int(mm or 0))


def freshness_score(posted_at: datetime | None, cfg: TimingConfig, now: datetime | None = None) -> float:
    """0-1. 1.0 for a brand new post, decaying to 0 at `max_freshness_hours`."""
    now = now or datetime.now(UTC)
    if posted_at is None:
        return 0.5  # unknown age: neither prioritise nor drop
    age_h = max(0.0, (now - posted_at).total_seconds() / 3600)
    if age_h <= cfg.ideal_freshness_hours:
        return 1.0
    if age_h >= cfg.max_freshness_hours:
        return 0.0
    span = cfg.max_freshness_hours - cfg.ideal_freshness_hours
    return round(1.0 - (age_h - cfg.ideal_freshness_hours) / span, 3)


def next_window_start(
    after: datetime,
    cfg: TimingConfig,
    tz_name: str,
    *,
    use_fallback_first: bool = False,
) -> tuple[datetime, str]:
    """First moment at/after `after` that falls inside an allowed send window."""
    tz = _zone(tz_name)
    local = after.astimezone(tz)

    primary = (_parse_hm(cfg.send_window_local[0]), _parse_hm(cfg.send_window_local[1]))
    fallback = (_parse_hm(cfg.fallback_window_local[0]), _parse_hm(cfg.fallback_window_local[1]))
    windows = [fallback, primary] if use_fallback_first else [primary, fallback]

    for day_offset in range(0, 8):
        day = (local + timedelta(days=day_offset)).date()
        weekday = day.weekday()
        if weekday not in cfg.allowed_weekdays:
            continue
        for idx, (start, end) in enumerate(windows):
            win_start = datetime.combine(day, start, tzinfo=tz)
            win_end = datetime.combine(day, end, tzinfo=tz)
            if win_end <= local:
                continue
            chosen = max(win_start, local)
            if chosen >= win_end:
                continue
            preferred = weekday in cfg.preferred_weekdays
            which = "primary" if (idx == 0) != use_fallback_first else "fallback"
            reason = (
                f"{which} window {start:%H:%M}-{end:%H:%M} {tz_name} on "
                f"{day:%a %d %b}" + ("" if preferred else " (not a preferred weekday)")
            )
            return chosen.astimezone(UTC), reason

    # Every window in the next week is unusable — send at the next allowed
    # weekday morning rather than dropping the application.
    fallback_dt = (local + timedelta(days=1)).replace(
        hour=primary[0].hour, minute=primary[0].minute, second=0, microsecond=0
    )
    return fallback_dt.astimezone(UTC), "no window available in 7 days; using next morning"


def plan_send_time(
    *,
    posted_at: datetime | None,
    location: str | None,
    cfg: TimingConfig,
    match_score: float = 0.0,
    now: datetime | None = None,
    last_sent_at: datetime | None = None,
    sent_today: int = 0,
    sent_this_hour: int = 0,
    rng: random.Random | None = None,
) -> SendPlan:
    """Work out when this application should actually go out."""
    now = now or datetime.now(UTC)
    rng = rng or random.Random()
    tz_name = resolve_timezone(location, cfg.timezone_default)

    fresh = freshness_score(posted_at, cfg, now)
    if posted_at is not None:
        age_h = (now - posted_at).total_seconds() / 3600
        if age_h > cfg.max_freshness_hours:
            return SendPlan(now, f"post is {age_h:.0f}h old (limit {cfg.max_freshness_hours}h)", 0.0, skip=True)
    else:
        age_h = None

    # Hard throughput guards come first — they can push everything to tomorrow.
    earliest = now
    guard_reason = ""
    if sent_today >= cfg.max_applications_per_day:
        tz = _zone(tz_name)
        tomorrow = (now.astimezone(tz) + timedelta(days=1)).replace(hour=0, minute=1)
        earliest = tomorrow.astimezone(UTC)
        guard_reason = f"daily cap of {cfg.max_applications_per_day} reached; "
    elif sent_this_hour >= cfg.max_applications_per_hour:
        earliest = now + timedelta(minutes=60)
        guard_reason = f"hourly cap of {cfg.max_applications_per_hour} reached; "

    if last_sent_at is not None:
        gap = last_sent_at + timedelta(minutes=cfg.min_minutes_between_applications)
        if gap > earliest:
            earliest = gap
            guard_reason += f"spacing {cfg.min_minutes_between_applications}m from the last send; "

    # A very fresh post is worth more than a well-timed one — go now if the
    # guards allow it. Being in the first handful of applicants beats being the
    # hundredth at a nicer hour.
    if age_h is not None and age_h <= cfg.immediate_if_fresher_than_hours and earliest <= now:
        send_at = now + timedelta(minutes=rng.uniform(0, max(1, cfg.jitter_minutes / 3)))
        return SendPlan(
            send_at,
            f"{guard_reason}posted {age_h:.1f}h ago — going out immediately for first-mover advantage",
            priority=_priority(fresh, match_score, True),
        )

    tz = _zone(tz_name)
    local_now = earliest.astimezone(tz)
    primary_end = _parse_hm(cfg.send_window_local[1])
    past_primary_today = local_now.time() > primary_end

    send_at, window_reason = next_window_start(
        earliest, cfg, tz_name, use_fallback_first=past_primary_today
    )
    jitter = rng.uniform(0, max(0, cfg.jitter_minutes))
    send_at = send_at + timedelta(minutes=jitter)

    age_text = f"posted {age_h:.0f}h ago" if age_h is not None else "post age unknown"
    reason = f"{guard_reason}{age_text}; scheduled for the {window_reason} (+{jitter:.0f}m jitter)"
    return SendPlan(send_at, reason, priority=_priority(fresh, match_score, False))


def _priority(freshness: float, match_score: float, immediate: bool) -> float:
    """Queue ordering: fit matters, but freshness decides between equals."""
    base = 0.6 * (match_score / 100.0) + 0.4 * freshness
    return round(base * 100 + (15 if immediate else 0), 2)


def bucket_keys(when: datetime, tz_name: str = "UTC") -> tuple[str, str]:
    """(day_bucket, hour_bucket) keys used by the rate counters."""
    local = when.astimezone(_zone(tz_name))
    return f"day:{local:%Y-%m-%d}", f"hour:{local:%Y-%m-%dT%H}"
