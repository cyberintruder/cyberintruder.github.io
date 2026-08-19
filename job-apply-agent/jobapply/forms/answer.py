"""Decide what to put in each form field.

Resolution order — first hit wins, and the source is recorded so the dashboard
can show exactly why every answer was given:

1. **profile**      — name/email/phone/address/links: mechanical, never guessed.
2. **answer bank**  — your `config/answers.yaml`, fuzzy-matched on the question.
3. **claude**       — everything else, with a confidence score.
4. **unresolved**   — parked for human review.

Two hard gates sit above all of that:
  * `never_auto_answer` phrases are *never* auto-filled, whatever the LLM says.
  * a required field resolved below `min_confidence` becomes an open question,
    which puts the whole application into the review queue instead of guessing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .. import llm as llm_mod
from .. import settings as settings_mod
from .introspect import FormField

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.72


@dataclass
class AnswerResolution:
    field_id: str
    label: str
    value: str
    source: str                 # profile | answer_bank | claude | default | unresolved
    confidence: float = 1.0
    rationale: str = ""
    required: bool = False

    @property
    def resolved(self) -> bool:
        return self.source != "unresolved" and bool(self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id, "label": self.label, "value": self.value,
            "source": self.source, "confidence": round(self.confidence, 2),
            "rationale": self.rationale, "required": self.required,
        }


# Profile mapping: (regex over the normalised label) -> profile attribute
PROFILE_RULES: list[tuple[str, str]] = [
    (r"\bfirst name\b|\bgiven name\b|\bforename\b", "first_name"),
    (r"\blast name\b|\bsurname\b|\bfamily name\b", "last_name"),
    (r"\b(full|legal)? ?name\b", "full_name"),
    (r"\be-?mail\b", "email"),
    (r"\b(phone|mobile|telephone|contact number)\b", "phone"),
    (r"\blinkedin\b", "linkedin"),
    (r"\b(github|git hub)\b", "github"),
    (r"\b(website|portfolio|personal site)\b", "website"),
    (r"\b(address line 1|street address|address)\b", "address_line1"),
    (r"\bcity\b|\btown\b", "city"),
    (r"\b(state|province|region)\b", "state"),
    (r"\b(zip|postal code|postcode|pin code)\b", "postal_code"),
    (r"\bcountry\b", "country"),
    (r"\bcurrent (company|employer)\b", "current_company"),
    (r"\bcurrent (title|role|position|job title)\b", "current_title"),
    (r"\bheadline\b", "headline"),
    (r"\bgender\b", "gender"),
    (r"\b(race|ethnicity|hispanic)\b", "ethnicity"),
    (r"\bveteran\b", "veteran_status"),
    (r"\bdisabilit", "disability_status"),
]

YES_NO_HINTS: list[tuple[str, str]] = [
    (r"legally (authori[sz]ed|entitled) to work", "Yes"),
    (r"(require|need).{0,20}sponsorship", "SPONSORSHIP"),
    (r"willing to relocate|open to relocat", "RELOCATE"),
    (r"(agree|accept|acknowledge|consent).{0,40}(terms|policy|privacy|processing)", "Yes"),
    (r"previously (worked|been employed)", "No"),
    (r"currently employed", "Yes"),
]


class AnswerResolver:
    def __init__(self, settings=None, client: llm_mod.Claude | None = None) -> None:
        self.settings = settings or settings_mod.load()
        self.profile = self.settings.config.profile
        self.bank = self.settings.answers
        self.client = client or llm_mod.get_client()
        self._never = [p.lower() for p in self.bank.never_auto_answer]

    # ------------------------------------------------------------------ api

    def resolve_all(
        self,
        fields: list[FormField],
        *,
        job_title: str = "",
        company: str = "",
        job_description: str = "",
        resume_summary: str = "",
    ) -> list[AnswerResolution]:
        results: list[AnswerResolution] = []
        deferred: list[FormField] = []

        for fld in fields:
            if fld.kind == "file":
                continue  # resume/cover-letter uploads are handled by the applier
            if self._is_blocked(fld.label):
                results.append(AnswerResolution(
                    fld.field_id, fld.label, "", "unresolved", 0.0,
                    "matches a never_auto_answer rule", fld.required,
                ))
                continue

            res = self._from_profile(fld) or self._from_bank(fld) or self._from_heuristics(fld)
            if res:
                results.append(res)
            else:
                deferred.append(fld)

        if deferred:
            results.extend(self._from_claude(
                deferred, job_title=job_title, company=company,
                job_description=job_description, resume_summary=resume_summary,
            ))

        # Enforce the confidence gate on required fields.
        for res in results:
            if res.required and res.resolved and res.confidence < MIN_CONFIDENCE:
                res.source = "unresolved"
                res.rationale = f"confidence {res.confidence:.2f} below {MIN_CONFIDENCE} on a required field"
        return results

    @staticmethod
    def open_questions(resolutions: list[AnswerResolution]) -> list[AnswerResolution]:
        return [r for r in resolutions if r.required and not r.resolved]

    # -------------------------------------------------------------- strategies

    def _is_blocked(self, label: str) -> bool:
        low = (label or "").lower()
        return any(phrase in low for phrase in self._never)

    def _from_profile(self, fld: FormField) -> AnswerResolution | None:
        label = _norm(fld.label or fld.placeholder or fld.name)
        if not label:
            return None
        for pattern, attr in PROFILE_RULES:
            if re.search(pattern, label):
                value = getattr(self.profile, attr, "")
                if value in (None, ""):
                    return None
                return AnswerResolution(
                    fld.field_id, fld.label, str(value), "profile", 1.0,
                    f"profile.{attr}", fld.required,
                )
        return None

    def _from_bank(self, fld: FormField) -> AnswerResolution | None:
        label = _norm(fld.label or fld.placeholder)
        if not label:
            return None
        for rule in self.bank.answers:
            for phrase in rule.match:
                if _norm(phrase) in label:
                    return AnswerResolution(
                        fld.field_id, fld.label, rule.value, "answer_bank", 1.0,
                        f"answer bank rule: {phrase!r}", fld.required,
                    )
        return None

    def _from_heuristics(self, fld: FormField) -> AnswerResolution | None:
        label = _norm(fld.label or fld.placeholder)
        if not label:
            return None

        for pattern, answer in YES_NO_HINTS:
            if re.search(pattern, label):
                if answer == "SPONSORSHIP":
                    answer = "Yes" if self.profile.requires_sponsorship else "No"
                elif answer == "RELOCATE":
                    answer = "Yes" if self.profile.willing_to_relocate else "No"
                return AnswerResolution(
                    fld.field_id, fld.label, answer, "default", 0.95,
                    f"policy rule: {pattern}", fld.required,
                )

        if re.search(r"\b(years|yrs)\b.{0,25}\bexperience\b", label) and fld.kind in {"text", "number"}:
            return AnswerResolution(
                fld.field_id, fld.label, str(self.profile.years_experience), "profile", 0.95,
                "profile.years_experience", fld.required,
            )

        if re.search(r"(start date|available from|earliest.*(start|join)|joining date)", label):
            return AnswerResolution(
                fld.field_id, fld.label, _resolve_start_date(self.profile.earliest_start_date),
                "profile", 0.9, "profile.earliest_start_date", fld.required,
            )

        if re.search(r"(salary|compensation|ctc|remuneration)", label):
            numeric = fld.kind == "number" or "number" in (fld.placeholder or "").lower()
            value = (
                str(self.profile.desired_salary_number)
                if numeric and self.profile.desired_salary_number
                else self.profile.desired_salary
            )
            if value:
                return AnswerResolution(
                    fld.field_id, fld.label, str(value), "profile", 0.85,
                    "profile salary expectation", fld.required,
                )
        return None

    def _from_claude(
        self,
        fields: list[FormField],
        *,
        job_title: str,
        company: str,
        job_description: str,
        resume_summary: str,
    ) -> list[AnswerResolution]:
        unresolved = [
            AnswerResolution(f.field_id, f.label, "", "unresolved", 0.0,
                             "no deterministic rule matched", f.required)
            for f in fields
        ]
        if not self.client.available():
            for r in unresolved:
                r.rationale = "no rule matched and no ANTHROPIC_API_KEY configured"
            return unresolved

        catalog = "\n".join(f.describe() for f in fields)
        prompt = (
            f"CANDIDATE\n{_profile_block(self.profile)}\n\n"
            f"RESUME EMPHASIS\n{resume_summary or '(not provided)'}\n\n"
            f"JOB\ntitle: {job_title}\ncompany: {company}\n"
            f"description:\n{(job_description or '')[:6000]}\n\n"
            f"FORM FIELDS\n{catalog}\n\n"
            "Answer each field. If a field offers options, your value MUST be one of "
            "them, copied exactly. If you cannot answer truthfully from the candidate "
            "facts above, return an empty value with confidence 0."
        )
        try:
            data = self.client.structured(
                prompt,
                llm_mod.FORM_ANSWERS_SCHEMA,
                system=CLAUDE_SYSTEM,
                effort=llm_mod.EFFORT_STANDARD,
                max_tokens=6000,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude answering failed: %s", exc)
            for r in unresolved:
                r.rationale = f"model call failed: {type(exc).__name__}"
            return unresolved

        answered = {a["field_id"]: a for a in data.get("answers", [])}
        out: list[AnswerResolution] = []
        for fld in fields:
            a = answered.get(fld.field_id)
            if not a or not a.get("value") or float(a.get("confidence", 0)) <= 0:
                out.append(AnswerResolution(
                    fld.field_id, fld.label, "", "unresolved", 0.0,
                    (a or {}).get("rationale", "model declined to answer"), fld.required,
                ))
                continue
            value = str(a["value"])
            if fld.options:
                from .introspect import _closest_option

                value = _closest_option(value, fld.options)
            out.append(AnswerResolution(
                fld.field_id, fld.label, value, "claude",
                float(a.get("confidence", 0.5)), a.get("rationale", ""), fld.required,
            ))
        return out


CLAUDE_SYSTEM = """You fill in job application forms on behalf of a candidate.

Absolute rules:
- Only state facts present in the candidate profile. Never invent experience,
  employers, degrees, certifications, salary history or eligibility.
- If the honest answer is not derivable from the profile, return an empty value
  with confidence 0. An empty answer is always better than a fabricated one.
- When a field lists options, return one of those options copied character for
  character.
- For free-text motivation questions ("why do you want to work here"), write in
  the candidate's voice, 60-140 words, concrete and specific to the job. Do not
  use superlatives or filler.
- confidence reflects how certain you are the answer is both correct and
  well-formed for the field: 1.0 for a direct profile lookup, 0.5 for a
  reasonable inference, 0 when you are guessing."""


def _profile_block(profile) -> str:
    fields = [
        "full_name", "email", "phone", "city", "state", "country", "linkedin",
        "github", "headline", "years_experience", "current_company", "current_title",
        "notice_period_days", "requires_sponsorship", "willing_to_relocate",
        "desired_salary", "earliest_start_date",
    ]
    lines = [f"{f}: {getattr(profile, f, '')}" for f in fields]
    if getattr(profile, "work_authorization", None):
        lines.append(f"work_authorization: {profile.work_authorization}")
    if getattr(profile, "pitch", ""):
        lines.append(f"pitch: {' '.join(profile.pitch.split())}")
    return "\n".join(lines)


def _resolve_start_date(spec: str) -> str:
    spec = (spec or "").strip()
    m = re.fullmatch(r"\+(\d+)([dwm])", spec)
    if m:
        n = int(m.group(1))
        delta = {"d": timedelta(days=n), "w": timedelta(weeks=n), "m": timedelta(days=30 * n)}[m.group(2)]
        return (date.today() + delta).isoformat()
    return spec or (date.today() + timedelta(days=30)).isoformat()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).strip()

