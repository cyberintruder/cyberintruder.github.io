"""Resume registry, scoring and selection.

Selection is two-stage:

1. **Deterministic keyword scoring** — free, instant, and explainable. Produces a
   ranked shortlist and a 0-100 base score for every resume variant.
2. **Claude re-rank** (optional) — reads the real job description and the resume
   summaries, and either confirms or overrides the shortlist with reasons.

Stage 1 always runs, so the agent keeps working without an API key.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import llm as llm_mod
from . import settings as settings_mod

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9+#&./-]+")

SENIORITY_RANK = {
    "intern": 0, "entry": 1, "junior": 1, "associate": 2, "mid": 3,
    "senior": 4, "staff": 5, "lead": 5, "principal": 6, "director": 7,
}

SENIORITY_HINTS = [
    (r"\bprincipal\b", "principal"), (r"\bstaff\b", "staff"),
    (r"\b(lead|leader)\b", "lead"), (r"\bsenior\b|\bsr\.?\b", "senior"),
    (r"\bdirector\b|\bhead of\b", "director"),
    (r"\b(junior|jr\.?|entry.level|graduate|fresher)\b", "junior"),
    (r"\bintern(ship)?\b", "intern"),
]


@dataclass
class Resume:
    key: str
    role: str
    file: str
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    seniority: str | None = None
    is_tailored: bool = False
    parent_key: str | None = None
    base_dir: Path = Path(".")

    @property
    def path(self) -> Path:
        p = Path(self.file)
        return p if p.is_absolute() else self.base_dir / p

    @property
    def fmt(self) -> str:
        return self.path.suffix.lstrip(".").lower() or "md"

    def exists(self) -> bool:
        return self.path.exists()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "role": self.role, "file": self.file,
            "summary": self.summary.strip(), "keywords": self.keywords,
            "seniority": self.seniority, "is_tailored": self.is_tailored,
            "parent_key": self.parent_key, "path": str(self.path),
            "exists": self.exists(),
        }


@dataclass
class MatchResult:
    resume: Resume | None
    score: float
    reasons: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    seniority_fit: str = "match"
    ranked: list[tuple[str, float]] = field(default_factory=list)
    source: str = "keyword"  # keyword | llm

    @property
    def resume_key(self) -> str | None:
        return self.resume.key if self.resume else None


class ResumeRegistry:
    def __init__(self, resumes: list[Resume], default_key: str | None = None) -> None:
        self._by_key = {r.key: r for r in resumes}
        self.default_key = default_key or (resumes[0].key if resumes else None)

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self):
        return iter(self._by_key.values())

    def get(self, key: str | None) -> Resume | None:
        return self._by_key.get(key) if key else None

    @property
    def default(self) -> Resume | None:
        return self.get(self.default_key)

    def all(self) -> list[Resume]:
        return list(self._by_key.values())

    def add(self, resume: Resume) -> None:
        self._by_key[resume.key] = resume

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, directory: Path | None = None) -> ResumeRegistry:
        st = settings_mod.load()
        directory = Path(directory or st.env.resume_dir)
        index = directory / "index.yaml"
        if not index.exists():
            log.warning("no resume index at %s", index)
            return cls([], None)
        data = yaml.safe_load(index.read_text(encoding="utf-8")) or {}
        resumes = [
            Resume(
                key=item["key"],
                role=item.get("role", ""),
                file=item["file"],
                summary=item.get("summary", ""),
                keywords=[k.lower().strip() for k in item.get("keywords", [])],
                seniority=item.get("seniority"),
                base_dir=directory,
            )
            for item in data.get("resumes", [])
        ]
        return cls(resumes, data.get("default"))


# ------------------------------------------------------------------- scoring


def tokenize(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def detect_seniority(title: str, description: str = "") -> str:
    blob = f"{title} {description[:600]}".lower()
    for pattern, level in SENIORITY_HINTS:
        if re.search(pattern, blob):
            return level
    return "mid"


def keyword_score(resume: Resume, title: str, description: str) -> tuple[float, list[str]]:
    """Score a resume against a job on 0-100 using weighted keyword coverage.

    Title hits weigh 3x description hits — a keyword in the job title is a far
    stronger signal than one buried in a benefits paragraph.
    """
    if not resume.keywords:
        return 0.0, []

    title_l = (title or "").lower()
    desc_l = (description or "").lower()

    hits: list[str] = []
    weighted = 0.0
    for kw in resume.keywords:
        in_title = kw in title_l
        in_desc = kw in desc_l
        if not (in_title or in_desc):
            continue
        hits.append(kw)
        weighted += 3.0 if in_title else 1.0

    if not hits:
        return 0.0, []

    # Normalise against a realistic ceiling instead of "every keyword matched",
    # which never happens: saturating at ~40% coverage keeps scores comparable
    # between a 10-keyword and a 25-keyword resume.
    ceiling = max(4.0, len(resume.keywords) * 0.4 * 1.6)
    raw = min(1.0, weighted / ceiling)
    # Slight concavity so the first few strong hits move the score most.
    score = 100.0 * math.sqrt(raw)
    return round(score, 1), hits


def seniority_fit(resume: Resume, job_seniority: str) -> tuple[str, float]:
    """Returns (fit, multiplier). Over-qualified is penalised less than under."""
    r = SENIORITY_RANK.get((resume.seniority or "senior").lower(), 4)
    j = SENIORITY_RANK.get((job_seniority or "mid").lower(), 3)
    delta = r - j
    if delta == 0:
        return "match", 1.0
    if delta > 0:
        return ("over", 0.95 if delta == 1 else 0.82)
    return ("under", 0.88 if delta == -1 else 0.65)


def rank_resumes(registry: ResumeRegistry, title: str, description: str) -> list[tuple[Resume, float, list[str]]]:
    job_sen = detect_seniority(title, description)
    scored: list[tuple[Resume, float, list[str]]] = []
    for resume in registry:
        base, hits = keyword_score(resume, title, description)
        _, mult = seniority_fit(resume, job_sen)
        scored.append((resume, round(base * mult, 1), hits))
    scored.sort(key=lambda row: row[1], reverse=True)
    return scored


# ------------------------------------------------------------------ selection

_SELECT_SYSTEM = """You choose which resume variant a candidate should send for a specific job.

Rules:
- Choose from the provided resume keys only. Never invent a key.
- `score` is how well this candidate fits THIS job, 0-100. Be honest and calibrated:
  70+ means a recruiter would plausibly shortlist them, 85+ means a strong match.
- `reasons` are short, concrete, and reference actual overlap — not flattery.
- `missing_skills` are requirements from the job description the resume does not evidence.
- `recommend_apply` is false when the role is a poor fit, wrong seniority band, or
  requires a hard qualification the candidate lacks."""


def select_resume(
    title: str,
    description: str,
    *,
    registry: ResumeRegistry | None = None,
    use_llm: bool | None = None,
    company: str = "",
    location: str = "",
) -> MatchResult:
    """Pick the best resume for a job and explain why."""
    st = settings_mod.load()
    registry = ResumeRegistry.load() if registry is None else registry
    if len(registry) == 0:
        return MatchResult(resume=None, score=0.0, reasons=["no resumes in registry"])

    ranked = rank_resumes(registry, title, description)
    best, best_score, best_hits = ranked[0]
    job_sen = detect_seniority(title, description)
    fit, _ = seniority_fit(best, job_sen)

    result = MatchResult(
        resume=best,
        score=best_score,
        reasons=[f"keyword overlap: {', '.join(best_hits[:6])}"] if best_hits else ["no keyword overlap"],
        seniority_fit=fit,
        ranked=[(r.key, s) for r, s, _ in ranked],
        source="keyword",
    )

    want_llm = st.config.matching.use_llm_rerank if use_llm is None else use_llm
    if not want_llm:
        return result

    client = llm_mod.get_client()
    if not client.available():
        log.debug("LLM re-rank skipped: no API key")
        return result

    # Only send the top candidates — cheaper and reduces the chance of a
    # low-relevance variant being picked on a stray keyword.
    shortlist = [r for r, _, _ in ranked[:4]]
    catalog = "\n".join(
        f"- key: {r.key}\n  role: {r.role}\n  seniority: {r.seniority or 'senior'}\n"
        f"  summary: {' '.join(r.summary.split())}"
        for r in shortlist
    )
    prompt = (
        f"JOB\ntitle: {title}\ncompany: {company}\nlocation: {location}\n\n"
        f"description:\n{description[:12000]}\n\n"
        f"RESUME VARIANTS\n{catalog}\n\n"
        "Pick the single best variant and score the fit."
    )
    try:
        data = client.structured(
            prompt,
            llm_mod.JOB_MATCH_SCHEMA,
            system=_SELECT_SYSTEM,
            effort=llm_mod.EFFORT_STANDARD,
            max_tokens=4000,
        )
    except Exception as exc:  # noqa: BLE001 - never let a model error stop the pipeline
        log.warning("LLM re-rank failed, keeping keyword pick: %s", exc)
        result.reasons.append(f"llm re-rank unavailable ({type(exc).__name__})")
        return result

    chosen = registry.get(data.get("resume_key")) or best
    llm_score = float(data.get("score", best_score))
    # Blend: the LLM understands the JD, keyword scoring is stable. 70/30.
    blended = round(llm_score * 0.7 + best_score * 0.3, 1)
    if not data.get("recommend_apply", True):
        blended = min(blended, 45.0)

    return MatchResult(
        resume=chosen,
        score=blended,
        reasons=list(data.get("reasons", []))[:6],
        missing_skills=list(data.get("missing_skills", []))[:8],
        seniority_fit=data.get("seniority_fit", fit),
        ranked=result.ranked,
        source="llm",
    )
