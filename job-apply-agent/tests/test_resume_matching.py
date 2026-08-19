"""The matcher must pick the right resume — this is the core of the product."""

from __future__ import annotations

import pytest

from jobapply.resumes import (
    ResumeRegistry,
    detect_seniority,
    keyword_score,
    rank_resumes,
    select_resume,
    seniority_fit,
)

CLOUD_JD = """
Own AWS and Kubernetes security across a multi-account Organization. Design IAM
least-privilege policies, roll out CSPM, write Terraform policy-as-code with OPA,
and harden EKS clusters. GuardDuty and Security Hub experience required.
"""

APPSEC_JD = """
Threat modelling for new products, secure design review, ownership of the
SAST/DAST/SCA pipeline, manual code review across Python and Java, OWASP ASVS.
"""

DETECTION_JD = """
Build SIEM content in Splunk and Microsoft Sentinel. Author Sigma rules, map
coverage to MITRE ATT&CK, tune CrowdStrike EDR alerts, automate triage with SOAR.
"""

PENTEST_JD = """
Authorised network, web and Active Directory penetration testing. Red team
operations with Cobalt Strike, adversary emulation, exploit development. OSCP.
"""

DEVSECOPS_JD = """
Shared CI templates across GitHub Actions, SBOM generation and Sigstore signing,
secrets management with Vault, supply chain security, hardened Docker images.
"""


@pytest.mark.parametrize(
    ("title", "jd", "expected"),
    [
        ("Senior Cloud Security Engineer", CLOUD_JD, "cloud-security"),
        ("Application Security Engineer", APPSEC_JD, "appsec"),
        ("Detection Engineer", DETECTION_JD, "detection-engineering"),
        ("Penetration Tester", PENTEST_JD, "offensive-security"),
        ("DevSecOps Engineer", DEVSECOPS_JD, "devsecops"),
    ],
)
def test_picks_the_right_variant(registry, title, jd, expected):
    result = select_resume(title, jd, registry=registry, use_llm=False)
    assert result.resume_key == expected, f"ranking was {result.ranked}"
    assert result.score > 50


def test_score_is_bounded_and_explained(registry):
    result = select_resume("Cloud Security Engineer", CLOUD_JD, registry=registry, use_llm=False)
    assert 0 <= result.score <= 100
    assert result.reasons and isinstance(result.reasons[0], str)
    assert len(result.ranked) == len(registry)


def test_title_hits_outweigh_description_hits(registry):
    """A keyword in the title should beat the same keyword buried in the body."""
    resume = registry.get("cloud-security")
    in_title, _ = keyword_score(resume, "Cloud Security Engineer", "generic role")
    in_body, _ = keyword_score(resume, "Engineer", "we do cloud security here")
    assert in_title > in_body


def test_unrelated_job_scores_low(registry):
    result = select_resume(
        "Pastry Chef",
        "Prepare laminated doughs, manage the morning bake, and run the dessert menu.",
        registry=registry, use_llm=False,
    )
    assert result.score < 30


def test_empty_registry_is_handled():
    result = select_resume("Anything", "Any description", registry=ResumeRegistry([]), use_llm=False)
    assert result.resume is None
    assert result.score == 0


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Security Engineer", "senior"),
        ("Sr. Cloud Engineer", "senior"),
        ("Staff Security Engineer", "staff"),
        ("Principal Architect", "principal"),
        ("Junior Analyst", "junior"),
        ("Security Engineering Intern", "intern"),
        ("Security Engineer", "mid"),
    ],
)
def test_seniority_detection(title, expected):
    assert detect_seniority(title) == expected


def test_seniority_penalty_direction(registry):
    lead = registry.get("security-architect")     # seniority: lead
    assert seniority_fit(lead, "lead")[0] == "match"
    assert seniority_fit(lead, "junior")[0] == "over"
    assert seniority_fit(lead, "director")[0] == "under"
    # under-qualification is penalised harder than over-qualification
    assert seniority_fit(lead, "director")[1] < seniority_fit(lead, "junior")[1]


def test_ranking_is_sorted(registry):
    ranked = rank_resumes(registry, "Cloud Security Engineer", CLOUD_JD)
    scores = [s for _, s, _ in ranked]
    assert scores == sorted(scores, reverse=True)


def test_llm_rerank_degrades_gracefully_without_key(registry):
    """No API key must mean keyword results, not an exception."""
    result = select_resume("Cloud Security Engineer", CLOUD_JD, registry=registry, use_llm=True)
    assert result.source == "keyword"
    assert result.resume_key == "cloud-security"
