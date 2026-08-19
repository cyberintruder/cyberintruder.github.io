"""Applier dispatch and the DOM introspection engine, driven against real HTML."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobapply.appliers.base import (
    ApplyResult,
    Outcome,
    is_submit_label,
    looks_confirmed,
    pick_applier,
)
from jobapply.forms.answer import AnswerResolution
from jobapply.forms.introspect import introspect_form

pytest.importorskip("playwright")

# Some sandboxes ship a chromium build that does not match the installed
# playwright package. Use it explicitly when present, otherwise let playwright
# resolve its own.
_CHROME_CANDIDATES = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
CHROME = str(_CHROME_CANDIDATES[-1]) if _CHROME_CANDIDATES else None

FORM_HTML = """
<!doctype html><html><body>
<form>
  <div class="field">
    <label for="fn">First Name *</label>
    <input id="fn" name="first_name" type="text" required>
  </div>
  <div class="field">
    <label for="em">Email</label>
    <input id="em" name="email" type="email" required>
  </div>
  <div class="field">
    <label for="yrs">Years of experience</label>
    <input id="yrs" name="years" type="number">
  </div>
  <div class="field">
    <label for="auth">Are you legally authorized to work in India?</label>
    <select id="auth" name="work_auth" required>
      <option>Select one</option>
      <option>Yes</option>
      <option>No</option>
    </select>
  </div>
  <fieldset>
    <legend>Will you now or in the future require sponsorship?</legend>
    <label><input type="radio" name="sponsor" value="y"> Yes</label>
    <label><input type="radio" name="sponsor" value="n"> No</label>
  </fieldset>
  <div class="field">
    <label for="cl">Cover letter</label>
    <textarea id="cl" name="cover_letter"></textarea>
  </div>
  <div class="field">
    <label for="cv">Resume</label>
    <input id="cv" name="resume" type="file">
  </div>
  <input type="hidden" name="csrf_token" value="secret">
  <button type="submit">Submit Application</button>
</form>
</body></html>
"""


@pytest.fixture()
async def page():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        launch_kwargs = {"headless": True, "args": ["--no-sandbox"]}
        if CHROME:
            launch_kwargs["executable_path"] = CHROME
        browser = await pw.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context()
        p = await ctx.new_page()
        yield p
        await ctx.close()
        await browser.close()


# ------------------------------------------------------------------ dispatch


@pytest.mark.parametrize(
    ("url", "vendor", "expected"),
    [
        ("https://acme.wd1.myworkdayjobs.com/careers/job/1", "workday", "workday"),
        ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse", "greenhouse"),
        ("https://jobs.lever.co/acme/uuid", "lever", "lever"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby", "ashby"),
        ("https://www.linkedin.com/jobs/view/123/", "linkedin_easy_apply", "linkedin_easy_apply"),
        ("https://careers.unknown-startup.com/apply", "generic", "generic"),
        ("https://something.entirely.new/apply", None, "generic"),
    ],
)
def test_applier_dispatch(url, vendor, expected):
    assert pick_applier(url, vendor).name == expected


def test_vendor_detection_works_from_the_url_alone():
    """Even with no vendor hint, the URL should route correctly."""
    assert pick_applier("https://acme.wd1.myworkdayjobs.com/x", None).name == "workday"
    assert pick_applier("https://jobs.lever.co/acme/x", None).name == "lever"


# ------------------------------------------------------------- introspection


async def test_introspects_a_realistic_form(page):
    await page.set_content(FORM_HTML)
    fields = await introspect_form(page)
    by_label = {f.label.lower(): f for f in fields}

    assert "first name" in by_label
    assert by_label["first name"].required is True
    assert by_label["first name"].kind == "text"

    assert by_label["email"].kind == "email"
    assert by_label["years of experience"].kind == "number"
    assert by_label["cover letter"].kind == "textarea"

    auth = by_label["are you legally authorized to work in india?"]
    assert auth.kind == "select"
    assert "Yes" in auth.options and "No" in auth.options
    assert "Select one" not in auth.options  # placeholder stripped

    sponsor = next(f for f in fields if f.kind == "radio")
    assert "sponsorship" in sponsor.label.lower()
    assert sorted(sponsor.options) == ["No", "Yes"]

    assert any(f.kind == "file" for f in fields)


async def test_hidden_and_csrf_fields_are_ignored(page):
    await page.set_content(FORM_HTML)
    fields = await introspect_form(page)
    assert not any("csrf" in (f.name or "").lower() for f in fields)


async def test_answers_are_written_back_to_the_dom(page):
    from jobapply.forms.introspect import fill_field

    await page.set_content(FORM_HTML)
    fields = {f.label.lower(): f for f in await introspect_form(page)}

    assert await fill_field(page, fields["first name"], "Venkatesh")
    assert await page.input_value("#fn") == "Venkatesh"

    assert await fill_field(page, fields["are you legally authorized to work in india?"], "Yes")
    assert await page.input_value("#auth") == "Yes"

    sponsor = next(f for f in fields.values() if f.kind == "radio")
    assert await fill_field(page, sponsor, "Yes")
    assert await page.is_checked("input[name='sponsor'][value='y']")


async def test_disabled_fields_are_skipped(page):
    await page.set_content(
        "<form><label for='a'>Locked</label><input id='a' name='a' disabled>"
        "<label for='b'>Open</label><input id='b' name='b'></form>"
    )
    labels = {f.label for f in await introspect_form(page)}
    assert "Open" in labels
    assert "Locked" not in labels


# ----------------------------------------------------------------- contract


def test_submit_label_detection():
    assert is_submit_label("Submit application")
    assert is_submit_label("Submit")
    assert not is_submit_label("Save and continue")
    assert not is_submit_label("Next")


def test_confirmation_detection():
    assert looks_confirmed("Thank you for applying to Acme Corp.")
    assert looks_confirmed("Your application has been received")
    assert not looks_confirmed("Please complete all required fields")


def test_result_routes_open_questions_separately():
    result = ApplyResult(outcome=Outcome.NEEDS_REVIEW)
    result.add_answers([
        AnswerResolution("f1", "First name", "Venkatesh", "profile", 1.0, "", True),
        AnswerResolution("f2", "Why us?", "", "unresolved", 0.0, "no rule", True),
        AnswerResolution("f3", "Anything else?", "", "unresolved", 0.0, "no rule", False),
    ])
    assert [a["field_id"] for a in result.answers] == ["f1", "f3"]
    assert [q["field_id"] for q in result.open_questions] == ["f2"]


def test_result_ok_only_for_terminal_success():
    assert ApplyResult(outcome=Outcome.SUBMITTED).ok
    assert ApplyResult(outcome=Outcome.DRY_RUN).ok
    assert not ApplyResult(outcome=Outcome.NEEDS_REVIEW).ok
    assert not ApplyResult(outcome=Outcome.FAILED).ok
