"""Read an arbitrary application form out of the DOM, and write answers back.

`introspect_form()` returns a vendor-neutral list of `FormField`s — label, type,
options, required flag — which is what makes one answering engine work across
Workday, Greenhouse, Lever, Ashby and forms nobody has seen before.

Label resolution is deliberately layered: real ATS markup is inconsistent, and a
field the agent cannot label is a field it must not answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Inputs that are never part of an application answer.
SKIP_TYPES = {"hidden", "submit", "button", "image", "reset"}
SKIP_NAME_HINTS = ("csrf", "token", "captcha", "utm_", "recaptcha", "honeypot")

FIELD_CONTAINER = (
    "div[data-automation-id], fieldset, div.application-question, "
    "div[class*='field'], div[class*='question'], li[class*='field'], p"
)


@dataclass
class FormField:
    """One answerable thing on a page."""

    field_id: str                      # stable handle used to write the answer back
    label: str
    kind: str                          # text|textarea|select|radio|checkbox|file|date|number|email|tel
    required: bool = False
    options: list[str] = field(default_factory=list)
    value: str = ""
    placeholder: str = ""
    selector: str = ""
    name: str = ""
    max_length: int | None = None
    group: str = ""                    # radio group name
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_choice(self) -> bool:
        return self.kind in {"select", "radio"} or (self.kind == "checkbox" and len(self.options) > 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id, "label": self.label, "kind": self.kind,
            "required": self.required, "options": self.options, "value": self.value,
            "placeholder": self.placeholder, "name": self.name,
        }

    def describe(self) -> str:
        bits = [f"[{self.field_id}] {self.label or self.placeholder or self.name or '(unlabelled)'}"]
        bits.append(f"type={self.kind}")
        if self.required:
            bits.append("REQUIRED")
        if self.options:
            bits.append("options=" + " | ".join(self.options[:24]))
        if self.max_length:
            bits.append(f"maxlen={self.max_length}")
        return "  ".join(bits)


_LABEL_JS = """
(el) => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').replace(/\\*$/, '').trim();

  // 1. aria-label / aria-labelledby
  const aria = el.getAttribute('aria-label');
  if (aria && aria.trim()) return clean(aria);
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const texts = labelledBy.split(/\\s+/)
      .map(id => document.getElementById(id))
      .filter(Boolean)
      .map(n => n.innerText);
    if (texts.join(' ').trim()) return clean(texts.join(' '));
  }

  // 2. <label for="id">
  if (el.id) {
    const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (lbl && lbl.innerText.trim()) return clean(lbl.innerText);
  }

  // 3. wrapping <label>
  const wrap = el.closest('label');
  if (wrap && wrap.innerText.trim()) return clean(wrap.innerText);

  // 4. legend of the enclosing fieldset (radio groups live here)
  const fs = el.closest('fieldset');
  if (fs) {
    const lg = fs.querySelector('legend');
    if (lg && lg.innerText.trim()) return clean(lg.innerText);
  }

  // 5. nearest preceding text node in the field container
  const container = el.closest("div[data-automation-id], div[class*='field'], div[class*='question'], li, p");
  if (container) {
    const own = Array.from(container.querySelectorAll('label, legend, span, div'))
      .map(n => n.innerText)
      .filter(t => t && t.trim().length > 1 && t.trim().length < 220);
    if (own.length) return clean(own[0]);
  }
  return '';
}
"""

_REQUIRED_JS = """
(el) => {
  if (el.required || el.getAttribute('aria-required') === 'true') return true;
  const c = el.closest("div[data-automation-id], div[class*='field'], div[class*='question'], fieldset, li, p");
  if (!c) return false;
  const t = (c.innerText || '');
  return /\\*\\s*$|\\*\\s|\\(required\\)|is required/i.test(t) || !!c.querySelector('[class*="required"]');
}
"""


async def introspect_form(page, root: str | None = None) -> list[FormField]:
    """Return every answerable field currently visible on the page."""
    fields: list[FormField] = []
    seen_groups: set[str] = set()
    counter = 0

    handles = await (page.query_selector_all(f"{root} input, {root} textarea, {root} select")
                     if root else page.query_selector_all("input, textarea, select"))

    for el in handles:
        try:
            if not await el.is_visible():
                continue
        except Exception:  # noqa: BLE001
            continue

        tag = (await el.evaluate("e => e.tagName.toLowerCase()")) or ""
        input_type = ((await el.get_attribute("type")) or "text").lower()
        name = (await el.get_attribute("name")) or ""
        el_id = (await el.get_attribute("id")) or ""

        if tag == "input" and input_type in SKIP_TYPES:
            continue
        haystack = f"{name} {el_id}".lower()
        if any(h in haystack for h in SKIP_NAME_HINTS):
            continue
        if await el.is_disabled():
            continue

        kind = _kind_for(tag, input_type)

        # Radios/checkbox sets collapse into one logical field.
        if input_type in {"radio", "checkbox"} and name:
            group_key = f"{kind}:{name}"
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            fld = await _build_group_field(page, el, name, kind, counter)
            if fld:
                fields.append(fld)
                counter += 1
            continue

        label = await el.evaluate(_LABEL_JS)
        required = await el.evaluate(_REQUIRED_JS)
        placeholder = (await el.get_attribute("placeholder")) or ""
        maxlen = await el.get_attribute("maxlength")

        options: list[str] = []
        if tag == "select":
            options = [
                (o or "").strip()
                for o in await el.eval_on_selector_all("option", "os => os.map(o => o.textContent)")
                if (o or "").strip()
            ]
            options = [o for o in options if o.lower() not in {"select one", "select...", "-", "--", "choose"}]

        counter += 1
        fields.append(
            FormField(
                field_id=f"f{counter}",
                label=_clean(label),
                kind=kind,
                required=bool(required),
                options=options,
                value=(await el.input_value()) if kind not in {"file"} else "",
                placeholder=placeholder,
                selector=_selector_for(el_id, name, tag, input_type),
                name=name,
                max_length=int(maxlen) if maxlen and maxlen.isdigit() else None,
                meta={"tag": tag, "input_type": input_type, "el_id": el_id},
            )
        )

    # Workday and Ashby render "selects" as button+listbox, not <select>.
    fields.extend(await _introspect_custom_dropdowns(page, start_index=counter))
    return fields


async def _build_group_field(page, el, name: str, kind: str, counter: int) -> FormField | None:
    siblings = await page.query_selector_all(f"input[name='{name}']")
    options: list[str] = []
    for sib in siblings:
        txt = await sib.evaluate(_LABEL_JS)
        txt = _clean(txt)
        if txt and txt not in options:
            options.append(txt)

    group_label = await el.evaluate(
        """(e) => {
             const fs = e.closest('fieldset');
             if (fs) { const lg = fs.querySelector('legend'); if (lg) return lg.innerText; }
             const c = e.closest("div[data-automation-id], div[class*='question'], div[class*='field']");
             if (c) { const l = c.querySelector('label, legend, .question-title'); if (l) return l.innerText; }
             return '';
           }"""
    )
    group_label = _clean(group_label)
    if not group_label and options:
        # Fall back to the shared prefix of the option labels, which for many
        # ATS radio groups is the question itself.
        group_label = name.replace("_", " ").strip()
    if not group_label:
        return None

    required = await el.evaluate(_REQUIRED_JS)
    return FormField(
        field_id=f"f{counter + 1}",
        label=group_label,
        kind=kind,
        required=bool(required),
        options=options,
        selector=f"input[name='{name}']",
        name=name,
        group=name,
        meta={"tag": "input", "input_type": kind},
    )


async def _introspect_custom_dropdowns(page, start_index: int) -> list[FormField]:
    """Pick up button-based comboboxes (Workday, Ashby, React-Select)."""
    out: list[FormField] = []
    selectors = [
        "button[aria-haspopup='listbox']",
        "div[role='combobox']",
        "button[data-automation-id*='Dropdown']",
        "div[class*='select__control']",
    ]
    idx = start_index
    for sel in selectors:
        for el in await page.query_selector_all(sel):
            try:
                if not await el.is_visible():
                    continue
            except Exception:  # noqa: BLE001
                continue
            label = _clean(await el.evaluate(_LABEL_JS))
            if not label:
                continue
            if any(f.label == label for f in out):
                continue
            idx += 1
            required = await el.evaluate(_REQUIRED_JS)
            out.append(
                FormField(
                    field_id=f"f{idx}",
                    label=label,
                    kind="select",
                    required=bool(required),
                    options=[],  # populated on open
                    selector=sel,
                    meta={"custom_dropdown": True, "selector_group": sel},
                )
            )
    return out


# ---------------------------------------------------------------------- write


async def fill_field(page, fld: FormField, value: str) -> bool:
    """Write `value` into `fld`. Returns True when the write is confirmed."""
    if not value:
        return False
    try:
        if fld.kind == "radio":
            return await _select_radio(page, fld, value)
        if fld.kind == "checkbox":
            return await _set_checkbox(page, fld, value)
        if fld.kind == "select":
            return await _select_option(page, fld, value)
        if fld.kind == "file":
            await page.set_input_files(_locator_str(fld), value)
            return True

        loc = page.locator(_locator_str(fld)).first
        await loc.scroll_into_view_if_needed(timeout=4000)
        await loc.fill("")
        await loc.type(value, delay=18)   # typing beats fill() on React forms
        await loc.evaluate("e => { e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); e.blur(); }")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not fill %s (%s): %s", fld.field_id, fld.label[:60], exc)
        return False


async def _select_radio(page, fld: FormField, value: str) -> bool:
    radios = await page.query_selector_all(f"input[name='{fld.name}']")
    target = _closest_option(value, fld.options)
    for radio in radios:
        text = _clean(await radio.evaluate(_LABEL_JS))
        if _norm(text) == _norm(target):
            await radio.evaluate("e => e.scrollIntoView({block:'center'})")
            await radio.click(force=True)
            return True
    return False


async def _set_checkbox(page, fld: FormField, value: str) -> bool:
    truthy = _norm(value) in {"yes", "true", "1", "on", "agree", "i agree", "accept", "checked"}
    if fld.options and len(fld.options) > 1:
        target = _closest_option(value, fld.options)
        for box in await page.query_selector_all(f"input[name='{fld.name}']"):
            if _norm(_clean(await box.evaluate(_LABEL_JS))) == _norm(target):
                await box.click(force=True)
                return True
        return False
    loc = page.locator(_locator_str(fld)).first
    if truthy:
        await loc.check(force=True)
    else:
        await loc.uncheck(force=True)
    return True


async def _select_option(page, fld: FormField, value: str) -> bool:
    if fld.meta.get("custom_dropdown"):
        return await _select_custom_dropdown(page, fld, value)
    loc = page.locator(_locator_str(fld)).first
    target = _closest_option(value, fld.options) if fld.options else value
    try:
        await loc.select_option(label=target)
        return True
    except Exception:  # noqa: BLE001
        try:
            await loc.select_option(value=target)
            return True
        except Exception:  # noqa: BLE001
            return False


async def _select_custom_dropdown(page, fld: FormField, value: str) -> bool:
    """Open a button/listbox combobox, read the options, click the best match."""
    try:
        trigger = page.locator(fld.selector).filter(has_text=re.compile(".")).first
        candidates = page.get_by_label(fld.label, exact=False)
        if await candidates.count():
            trigger = candidates.first
        await trigger.click()
        await page.wait_for_timeout(350)

        opts = page.locator("[role='option'], li[role='option'], div[role='option']")
        n = await opts.count()
        labels: list[str] = []
        for i in range(min(n, 200)):
            labels.append(_clean(await opts.nth(i).inner_text()))
        if not labels:
            await page.keyboard.press("Escape")
            return False
        target = _closest_option(value, labels)
        await opts.nth(labels.index(target)).click()
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("custom dropdown %s failed: %s", fld.label[:50], exc)
        try:
            await page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        return False


# -------------------------------------------------------------------- helpers


def _kind_for(tag: str, input_type: str) -> str:
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    return {
        "radio": "radio", "checkbox": "checkbox", "file": "file",
        "date": "date", "number": "number", "email": "email", "tel": "tel",
    }.get(input_type, "text")


def _selector_for(el_id: str, name: str, tag: str, input_type: str) -> str:
    if el_id:
        return f"#{_css_escape(el_id)}"
    if name:
        return f"{tag}[name='{name}']"
    return f"{tag}[type='{input_type}']"


def _locator_str(fld: FormField) -> str:
    return fld.selector or f"[name='{fld.name}']"


def _css_escape(value: str) -> str:
    return re.sub(r"([^\w-])", r"\\\1", value)


def _clean(text: str | None) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return re.sub(r"\s*\*$", "", text)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def _closest_option(value: str, options: list[str]) -> str:
    """Map a free-text answer onto one of the offered options.

    Exact match, then containment either way, then token overlap. Falls back to
    the raw value so plain text inputs still work.
    """
    if not options:
        return value
    nv = _norm(value)
    for opt in options:
        if _norm(opt) == nv:
            return opt
    for opt in options:
        no = _norm(opt)
        if no and (no in nv or nv in no):
            return opt
    vt = set(nv.split())
    best, best_overlap = value, 0
    for opt in options:
        overlap = len(vt & set(_norm(opt).split()))
        if overlap > best_overlap:
            best, best_overlap = opt, overlap
    return best if best_overlap else options[0]
