"""Pipeline glue: filters, screenshot parsing, and email composition fallbacks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobapply.mailer.compose import compose_application_email
from jobapply.manual.extract import parse_relative_age
from jobapply.pipeline import passes_hard_filters
from jobapply.settings import MatchingConfig

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def test_excluded_titles_are_filtered():
    cfg = MatchingConfig(exclude_title_terms=["intern", "sales"])
    ok, why = passes_hard_filters("Security Intern", "Acme", "jd", cfg)
    assert not ok and "intern" in why
    assert passes_hard_filters("Security Engineer", "Acme", "jd", cfg)[0]


def test_excluded_companies_are_filtered():
    cfg = MatchingConfig(exclude_companies=["Bad Corp"])
    assert not passes_hard_filters("Engineer", "Bad Corp India", "jd", cfg)[0]
    assert passes_hard_filters("Engineer", "Good Corp", "jd", cfg)[0]


def test_required_terms_gate_the_posting():
    cfg = MatchingConfig(require_terms=["kubernetes", "aws"])
    assert passes_hard_filters("Engineer", "Acme", "We run on AWS", cfg)[0]
    ok, why = passes_hard_filters("Engineer", "Acme", "We run on bare metal", cfg)
    assert not ok and "required terms" in why


def test_no_filters_lets_everything_through():
    assert passes_hard_filters("Anything", "Anyone", "any text", MatchingConfig())[0]


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("3d", timedelta(days=3)),
        ("5h", timedelta(hours=5)),
        ("2w", timedelta(weeks=2)),
        ("45m", timedelta(minutes=45)),
        ("2 days ago", timedelta(days=2)),
        ("1 week ago", timedelta(weeks=1)),
    ],
)
def test_relative_age_parsing(text, delta):
    assert parse_relative_age(text, NOW) == NOW - delta


def test_relative_age_returns_none_for_noise():
    assert parse_relative_age("", NOW) is None
    assert parse_relative_age("Promoted", NOW) is None


def test_email_falls_back_to_a_real_template_without_a_key(settings):
    email = compose_application_email(
        job_title="Cloud Security Engineer",
        company="Nimbus Systems",
        job_description="AWS, Kubernetes, IAM.",
        resume_role="Cloud Security Engineer",
        resume_summary="Cloud security engineering across AWS and Kubernetes.",
        contact_name="Priya Raman",
    )
    assert email.source == "template"
    assert "Cloud Security Engineer" in email.subject
    assert "Nimbus Systems" in email.body
    assert email.body.startswith("Hi Priya,")


def test_signature_is_appended_once(settings):
    email = compose_application_email(
        job_title="Engineer", company="Acme", job_description="jd",
        resume_role="Engineer", resume_summary="summary",
    ).with_signature("Regards,\nVenkatesh")
    assert email.body.rstrip().endswith("Venkatesh")
    assert email.body.count("Regards,") == 1


def test_empty_signature_is_a_noop(settings):
    base = compose_application_email(
        job_title="Engineer", company="Acme", job_description="jd",
        resume_role="Engineer", resume_summary="s",
    )
    assert base.with_signature("   ").body == base.body
