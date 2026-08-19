# Architecture

## Flow

```
                    ┌──────────────────┐        ┌────────────────────┐
  LinkedIn search → │  sources/        │        │  manual/           │ ← screenshots
                    │  linkedin.py     │        │  watcher + extract │
                    └────────┬─────────┘        └─────────┬──────────┘
                             │       ScrapedJob / ExtractedPost
                             └───────────┬────────────────┘
                                         ▼
                              ┌──────────────────────┐
                              │  pipeline.py         │
                              │  hard filters        │
                              │  → resumes.select    │  ← Claude re-rank
                              │  → tailor.tailor     │  ← Claude + validation
                              │  → timing.plan       │
                              └──────────┬───────────┘
                                         ▼
                                  Job + Application
                                    (SQLite)
                                         │
                     ┌───────────────────┼────────────────────┐
                     ▼                   ▼                    ▼
            appliers/easy_apply   appliers/{workday,     mailer/gmail
                     │             greenhouse,lever,…}         │
                     └────────────┬──────────┘                 │
                                  ▼                            ▼
                        forms/introspect + answer         Gmail draft
                                  │
                    submitted / needs_review / failed
                                  │
                                  ▼
                            web/ dashboard
```

## Modules

| Module | Responsibility |
|---|---|
| `settings.py` | Environment + YAML config, validated with Pydantic. Falls back to the committed `.example.yaml` files so a fresh clone runs. |
| `models.py` | `Job`, `Application`, `ResumeRecord`, `Event`, `ScreenshotItem`, `EmailDraft`, `RateCounter`. |
| `db.py` | Engine, session scope, `log_event` for the audit trail. WAL + foreign keys on. |
| `llm.py` | The only place that talks to Claude. Adaptive thinking, effort tiers, JSON-schema structured output, vision. `available()` is false without a key and every caller has a deterministic fallback. |
| `resumes.py` | Registry loading, weighted keyword scoring, seniority fit, Claude re-rank, blended final score. |
| `tailor.py` | Job-specific variants + `validate_tailored()`, which rejects invented certifications, dates or renamed employers. |
| `render.py` | Markdown → PDF via WeasyPrint, content-hash cached, HTML fallback if native deps are missing. |
| `timing.py` | Timezone resolution from the job's location, freshness decay, window selection, throughput caps, jitter, priority. |
| `browser.py` | Playwright context with persisted `storage_state`. Login is interactive and manual — no credentials are stored. |
| `sources/linkedin.py` | Search URL construction, lazy-load scrolling, detail scraping, external-apply URL resolution, ATS detection. |
| `forms/introspect.py` | DOM → `FormField[]`, five-strategy label resolution, radio grouping, custom combobox discovery, and the write-back path. |
| `forms/answer.py` | profile → answer bank → policy → Claude → unresolved, with the `never_auto_answer` and confidence gates. |
| `appliers/` | `base.py` defines the contract and dispatch; `generic_form.py` is the engine; the vendor modules are thin subclasses overriding `prepare()` and `after_resume_upload()`. |
| `manual/` | Content-hash-deduplicated inbox watcher and the vision extraction. |
| `mailer/` | Email composition (Claude, template fallback) and the Gmail API client. |
| `pipeline.py` | The orchestrator. Writes state as it goes so a crashed run resumes cleanly. |
| `scheduler.py` | APScheduler: slow discovery, fast due-check, screenshot polling. |
| `web/` | FastAPI + Jinja2 shell, JSON API, vanilla-JS front end. No build step. |

## State machine

```
        matched ──► scheduled ──► in_progress ──┬──► submitted
           │            ▲                       ├──► needs_review ──┐
           │            └───────────────────────┴──► failed         │
           ▼                        approve / retry / reschedule ◄──┘
        skipped
```

`skipped` and `submitted` are terminal. Everything else can be moved by the
dashboard.

## Design rules

**One place per concern.** Every Claude call goes through `llm.py`; every DB
write goes through `session_scope()`; every applier reuses one form engine.

**Deterministic first, model second.** Keyword scoring, the answer bank and the
email template all work without an API key. The model improves the result; it is
never the only path.

**Never guess on a required field.** `AnswerResolver` returns `unresolved`
rather than a plausible-looking answer, and the applier stops.

**Everything is explainable.** Match reasons, the timing reason, and each
answer's source and confidence are stored on the row and rendered in the UI.
