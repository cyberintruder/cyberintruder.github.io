"""Tailoring may re-emphasise. It may not invent facts."""

from __future__ import annotations

from jobapply.tailor import validate_tailored

ORIGINAL = """# Jane Doe

**Cloud Security Engineer**

## Summary
Cloud security engineer with 8 years of experience.

## Experience

### Acme Corp — Senior Cloud Security Engineer
*January 2021 – Present*

- Designed the IAM baseline for a 40-account AWS Organization.
- Rolled out CSPM across all accounts.

### Globex — Cloud Security Engineer
*June 2018 – December 2020*

- Automated evidence collection for SOC 2.

## Certifications
CISSP · OSCP · CEH
"""


def test_accepts_a_legitimate_rewrite():
    tailored = ORIGINAL.replace(
        "Cloud security engineer with 8 years of experience.",
        "Cloud security engineer with 8 years of experience, focused on Kubernetes "
        "admission control and multi-account AWS guardrails.",
    ).replace(
        "- Rolled out CSPM across all accounts.",
        "- Rolled out CSPM across all accounts, prioritising Kubernetes findings.",
    )
    ok, why = validate_tailored(ORIGINAL, tailored)
    assert ok, why


def test_rejects_invented_certifications():
    tailored = ORIGINAL.replace("CISSP · OSCP · CEH", "CISSP · OSCP · CEH · CKS")
    ok, why = validate_tailored(ORIGINAL, tailored)
    assert not ok and "invented" in why


def test_rejects_dropped_certifications():
    tailored = ORIGINAL.replace("CISSP · OSCP · CEH", "CISSP")
    ok, why = validate_tailored(ORIGINAL, tailored)
    assert not ok and "dropped" in why


def test_rejects_invented_dates():
    tailored = ORIGINAL.replace("*June 2018 – December 2020*", "*June 2016 – December 2020*")
    ok, why = validate_tailored(ORIGINAL, tailored)
    assert not ok and "dates invented" in why


def test_rejects_renamed_employer():
    tailored = ORIGINAL.replace(
        "### Globex — Cloud Security Engineer",
        "### Google — Cloud Security Engineer",
    )
    ok, why = validate_tailored(ORIGINAL, tailored)
    assert not ok and "headings altered" in why


def test_rejects_a_gutted_resume():
    ok, why = validate_tailored(ORIGINAL, "# Jane Doe\n\nCloud engineer.\n")
    assert not ok and "lost more than" in why
