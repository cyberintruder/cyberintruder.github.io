"""LinkedIn parsing helpers — the bits that break when LinkedIn changes markup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobapply.settings import SearchSpec
from jobapply.sources.linkedin import (
    build_search_url,
    detect_ats,
    extract_job_id,
    parse_posted_ago,
)

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("3 days ago", timedelta(days=3)),
        ("Posted 5 hours ago", timedelta(hours=5)),
        ("1 week ago · 40 applicants", timedelta(weeks=1)),
        ("20 minutes ago", timedelta(minutes=20)),
        ("2 months ago", timedelta(days=60)),
    ],
)
def test_posted_ago_parsing(text, delta):
    assert parse_posted_ago(text, NOW) == NOW - delta


def test_posted_ago_returns_none_when_absent():
    assert parse_posted_ago("Bengaluru, Karnataka, India", NOW) is None
    assert parse_posted_ago("", NOW) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.linkedin.com/jobs/view/4012345678/", "4012345678"),
        ("/jobs/view/399/?refId=abc", "399"),
        ("https://www.linkedin.com/jobs/search/?currentJobId=4055&keywords=x", "4055"),
        ("https://example.com/nope", None),
    ],
)
def test_job_id_extraction(url, expected):
    assert extract_job_id(url) == expected


@pytest.mark.parametrize(
    ("url", "vendor"),
    [
        ("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/x", "workday"),
        ("https://boards.greenhouse.io/acme/jobs/1234", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/1", "greenhouse"),
        ("https://jobs.lever.co/acme/uuid", "lever"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby"),
        ("https://jobs.smartrecruiters.com/acme/1", "smartrecruiters"),
        ("https://careers.acme.com/apply", "generic"),
        (None, None),
    ],
)
def test_ats_detection(url, vendor):
    assert detect_ats(url) == vendor


def test_search_url_encodes_every_filter():
    spec = SearchSpec(
        name="t", keywords="Cloud Security Engineer", location="India",
        remote=True, posted_within_hours=48, experience_levels=["mid", "senior"],
        easy_apply_only=True, max_results=30,
    )
    url = build_search_url(spec)
    assert "keywords=Cloud+Security+Engineer" in url
    assert "location=India" in url
    assert "f_WT=2" in url                 # remote
    assert "f_TPR=r172800" in url          # 48h in seconds
    assert "f_AL=true" in url              # easy apply only
    assert "f_E=4" in url                  # mid+senior collapse to the same code
    assert "sortBy=DD" in url              # newest first — freshness matters


def test_search_url_omits_unset_filters():
    url = build_search_url(SearchSpec(name="t", keywords="Security"))
    assert "f_WT" not in url
    assert "f_AL" not in url
    assert "location" not in url


def test_search_url_paginates():
    spec = SearchSpec(name="t", keywords="Security")
    assert "start=25" in build_search_url(spec, start=25)
