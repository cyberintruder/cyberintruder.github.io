"""Answer resolution: the safety-critical part. It must refuse to guess."""

from __future__ import annotations

from jobapply.forms.answer import AnswerResolver, _resolve_start_date
from jobapply.forms.introspect import FormField, _closest_option


def field(fid, label, kind="text", required=True, options=None, **kw):
    return FormField(field_id=fid, label=label, kind=kind, required=required,
                     options=options or [], **kw)


def test_profile_fields_are_filled_mechanically(settings):
    r = AnswerResolver(settings)
    out = {a.field_id: a for a in r.resolve_all([
        field("f1", "First name"),
        field("f2", "Email address"),
        field("f3", "Phone number"),
        field("f4", "LinkedIn Profile"),
        field("f5", "City"),
    ])}
    assert out["f1"].value == settings.config.profile.first_name
    assert out["f2"].value == settings.config.profile.email
    assert out["f3"].value == settings.config.profile.phone
    assert out["f4"].value == settings.config.profile.linkedin
    assert all(a.source == "profile" and a.confidence == 1.0 for a in out.values())


def test_answer_bank_beats_the_model(settings):
    r = AnswerResolver(settings)
    out = r.resolve_all([field("f1", "Are you legally authorized to work in India?")])
    assert out[0].value == "Yes"
    assert out[0].source == "answer_bank"


def test_never_auto_answer_is_absolute(settings):
    """Blocked questions stay unresolved even though they look answerable."""
    r = AnswerResolver(settings)
    out = r.resolve_all([
        field("f1", "What is your current salary?"),
        field("f2", "Have you ever been convicted of a felony?"),
        field("f3", "Do you consent to a background check?"),
    ])
    assert all(a.source == "unresolved" for a in out)
    assert all(not a.resolved for a in out)


def test_unanswerable_required_question_becomes_an_open_question(settings):
    r = AnswerResolver(settings)
    out = r.resolve_all([field("f1", "Describe a time you disagreed with your manager")])
    assert AnswerResolver.open_questions(out) == out
    assert out[0].source == "unresolved"


def test_optional_unanswerable_question_does_not_block(settings):
    r = AnswerResolver(settings)
    out = r.resolve_all([field("f1", "Anything else you'd like us to know?", required=False)])
    assert AnswerResolver.open_questions(out) == []


def test_sponsorship_follows_the_profile(settings):
    r = AnswerResolver(settings)
    out = r.resolve_all([field("f1", "Will you now or in the future require sponsorship?")])
    expected = "Yes" if settings.config.profile.requires_sponsorship else "No"
    assert out[0].value == expected


def test_file_inputs_are_left_to_the_applier(settings):
    r = AnswerResolver(settings)
    assert r.resolve_all([field("f1", "Upload resume", kind="file")]) == []


def test_choice_values_are_snapped_to_the_offered_options():
    options = ["Yes, I am authorized", "No, I require sponsorship", "Prefer not to say"]
    assert _closest_option("Yes", options) == "Yes, I am authorized"
    assert _closest_option("no", options) == "No, I require sponsorship"
    # exact match always wins over containment
    assert _closest_option("Prefer not to say", options) == "Prefer not to say"


def test_closest_option_passes_through_free_text():
    assert _closest_option("30 days", []) == "30 days"


def test_relative_start_dates_resolve():
    from datetime import date, timedelta

    assert _resolve_start_date("+30d") == (date.today() + timedelta(days=30)).isoformat()
    assert _resolve_start_date("+2w") == (date.today() + timedelta(weeks=2)).isoformat()
    assert _resolve_start_date("2026-12-01") == "2026-12-01"


def test_no_api_key_means_unresolved_not_crash(settings):
    """Without a key the resolver must park questions, never invent answers."""
    r = AnswerResolver(settings)
    out = r.resolve_all([field("f1", "Why do you want to join us?")])
    assert out[0].source == "unresolved"
    assert "no rule matched" in out[0].rationale or "API" in out[0].rationale
