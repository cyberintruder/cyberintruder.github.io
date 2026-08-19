# job-apply-agent

Get **the right resume** to **the right job opening** at **the right time** — automatically.

A working job-application agent built around three real workflows:

1. **Automatic** — searches LinkedIn, scores every result against a folder of
   role-specific resumes, picks (or tailors) the best one, and applies through
   LinkedIn Easy Apply **or** the external ATS the job links out to
   (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, or an unknown form).
2. **Manual** — you screenshot LinkedIn posts worth applying to; the agent reads
   the images, extracts the job, picks the matching resume, and drafts the email
   in Gmail with that resume attached.
3. **Review** — anything the agent cannot answer honestly stops and asks you, in
   a web dashboard that shows every application, every answer it gave, and why.

---

## Why it is built this way

Most application bots optimise for volume. This one optimises for *arriving well*:

| Decision | Reason |
|---|---|
| Two-stage matching (keywords → Claude) | Keyword scoring is instant, free and explainable; the model re-ranks with real JD understanding. Without an API key you still get stage one. |
| Timing engine, not "apply immediately" | Recruiters work queues in arrival order and during working hours. A very fresh post goes out at once; anything older waits for a weekday morning **in the hiring team's timezone**. |
| Tailoring is validated, not trusted | The model may re-emphasise and reorder. It may not invent an employer, date, certification or metric — `validate_tailored()` checks and discards violations. |
| Unanswerable questions block the application | Guessing on "years of Kubernetes experience" or "current salary" is how you end up lying on an application. Those go to the review queue. |
| Dry run is the default | Nothing is submitted or emailed until you turn it off. |

---

## Quick start

```bash
git clone https://github.com/cyberintruder/job-apply-agent.git
cd job-apply-agent

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env                       # add your ANTHROPIC_API_KEY
cp config/config.example.yaml config/config.yaml
cp config/answers.example.yaml config/answers.yaml
$EDITOR config/config.yaml                 # profile, searches, timing

jobapply init                              # create the DB, check the setup
jobapply login                             # sign in to LinkedIn once, by hand
jobapply serve                             # dashboard on http://127.0.0.1:8000
```

Want to see the dashboard before wiring anything up:

```bash
python scripts/seed_demo.py --reset && jobapply serve
```

---

## The three workflows

### 1. Automatic — search and apply

```bash
jobapply discover          # search LinkedIn, score, queue with a send time
jobapply status            # what is queued and when it goes out
jobapply run-due           # apply to everything whose time has arrived
jobapply run               # all of the above, on a schedule, forever
```

`discover` walks each search in `config.yaml`, opens every result, captures the
full description, and works out whether it is Easy Apply or an external link
(following the external link once, without applying, to identify the ATS).

Each job then goes through:

```
hard filters → resume scoring → tailoring (if score is high) → send-time planning
```

and lands in the queue with a human-readable reason:

```
Staff Security Architect · Meridian Health
  resume   security-architect (score 79)
  send at  Wed 20 Aug 09:12 IST
  because  posted 52h ago; scheduled for the primary window 08:30-11:00
           Asia/Kolkata on Wed 20 Aug (+7m jitter)
```

### 2. Manual — screenshot to Gmail draft

Screenshot any LinkedIn post — including the text-only "DM me / send your CV to
…" posts that have no job ID and cannot be scraped — and drop the image in
`inbox/screenshots/`.

```bash
jobapply watch             # process drops as they land
jobapply ingest            # or a one-shot pass
```

Claude reads the image and extracts the role, company, location, requirements,
recruiter name, and any email address or apply instruction in the post. The
agent picks the matching resume, writes the email, and creates a **Gmail draft**
with the resume attached. You read it and hit send.

Set `JOBAPPLY_EMAIL_MODE=send` once you trust the drafts and it sends directly.

### 3. Review — the dashboard

```bash
jobapply serve
```

| Page | What it is for |
|---|---|
| **Dashboard** | KPIs, applications over time, resume usage, channel split, live activity |
| **Applications** | Every application, filterable by status/channel/text |
| **Detail** | The job, why that resume was chosen, every answer with its source and confidence, screenshots of each page submitted, full timeline, and the email draft |
| **Review** | Open questions the agent refused to guess — answer them and approve |
| **Send queue** | What goes out next, and the timing reason for each |
| **Resumes** | The registry, keyword coverage, usage counts, tailored variants |

---

## External ATS support

LinkedIn's "Apply" often leaves the site. The agent follows it and dispatches to
a vendor-specific driver, falling back to a generic engine that works on forms
nobody has written a driver for.

| Vendor | Handled |
|---|---|
| **LinkedIn Easy Apply** | Full modal wizard: contact info → resume → screening questions → review → submit |
| **Workday** | Apply → Autofill with Resume → the whole multi-page flow; waits for Workday's resume parser; drives its custom button/listbox dropdowns |
| **Greenhouse** | Single page, including the older `#grnhse_iframe` embed |
| **Lever** | Navigates to `/apply` and fills the form |
| **Ashby** | Waits for React hydration, then drives the custom dropdowns |
| **SmartRecruiters, iCIMS, Taleo, Workable, …** | Generic engine |
| **Anything else** | Generic engine — always stops before submit if a required question is unanswered |

All of them share one engine:

```
introspect the DOM → resolve every field → fill → upload resume → advance
```

**Form introspection** (`jobapply/forms/introspect.py`) turns arbitrary markup
into a vendor-neutral field list, resolving labels through five strategies
(`aria-label` → `aria-labelledby` → `<label for>` → wrapping `<label>` →
`<legend>` → nearest container text), collapsing radio groups, and picking up
button-based comboboxes that are not `<select>` elements.

**Answer resolution** (`jobapply/forms/answer.py`) then decides each value in a
strict order, recording the source so the dashboard can show its work:

| Order | Source | Example |
|---|---|---|
| 1 | `profile` | first name, email, phone, city, LinkedIn URL |
| 2 | `answer_bank` | your `config/answers.yaml`, fuzzy-matched on the question |
| 3 | `default` | policy rules — sponsorship, relocation, notice period |
| 4 | `claude` | everything else, with a confidence score |
| 5 | `unresolved` | → the review queue |

Two gates sit above all of it: phrases in `never_auto_answer` are **never**
auto-filled whatever the model says, and a required field answered below 0.72
confidence is downgraded to an open question.

### Workday accounts

Workday tenants require an account. The agent will **not** create one — that
sets a password on a third-party system and cannot be undone. Sign in once:

```bash
jobapply login --site workday --url https://acme.wd1.myworkdayjobs.com/careers
```

and the saved session is reused from then on.

---

## Resumes

```
resumes/
├── index.yaml                        # the registry — the agent picks from here
├── cloud-security-engineer.md
├── application-security-engineer.md
├── penetration-tester.md
├── detection-engineer.md
├── security-architect.md
└── devsecops-engineer.md
```

The shipped `.md` files are **templates with placeholder history**. Replace them
with your real experience before enabling automated sending. `.pdf` and `.docx`
work too — markdown is rendered to PDF automatically and cached by content hash.

Check what would be sent for any job, without applying:

```bash
jobapply resumes match "Senior Cloud Security Engineer" \
  --description "AWS, Kubernetes, IAM least privilege, CSPM, Terraform, OPA"

# cloud-security  score 100.0  (via keyword, seniority fit: match)
#   • keyword overlap: cloud security, aws, kubernetes, iam, terraform, cspm
```

---

## Timing

```bash
jobapply plan "Cloud Security Engineer" --location "San Francisco, CA" --posted-hours-ago 30
```

The engine trades off three things:

- **Freshness** — a post under `immediate_if_fresher_than_hours` (default 6)
  bypasses the window entirely; being early beats being well-timed. Past
  `max_freshness_hours` (default 168) the job is dropped.
- **Attention** — otherwise it targets `send_window_local` (08:30–11:00) on a
  preferred weekday, in the timezone derived from the *job's* location.
- **Safety** — `max_applications_per_day`, `max_applications_per_hour`,
  `min_minutes_between_applications` and jitter keep the account's behaviour in
  a human envelope.

Every scheduled application stores the reason, and the dashboard shows it.

---

## Configuration

| File | Contents |
|---|---|
| `.env` | secrets and safety switches |
| `config/config.yaml` | profile, searches, matching thresholds, timing, channels |
| `config/answers.yaml` | deterministic answers + `never_auto_answer` blocklist |
| `resumes/index.yaml` | the resume registry |

Safety switches worth knowing:

| Variable | Default | Effect |
|---|---|---|
| `JOBAPPLY_DRY_RUN` | `true` | Everything runs except the final Submit / send |
| `JOBAPPLY_REQUIRE_REVIEW` | `true` | Unanswerable required questions pause the application |
| `JOBAPPLY_EMAIL_MODE` | `draft` | `draft` creates a Gmail draft and stops; `send` sends |

Nothing leaves your machine except the API calls to Claude, LinkedIn, the ATS
you are applying to, and Gmail. The database is a local SQLite file.

---

## Gmail setup

See [`docs/gmail.md`](docs/gmail.md). In short: create a Desktop OAuth client in
Google Cloud Console, save the JSON to `secrets/credentials.json`, then:

```bash
jobapply gmail-auth
```

Scopes are `gmail.compose` and `gmail.send`. Nothing reads your mailbox.

---

## Development

```bash
pip install -e ".[dev]"
pytest -q                  # 123 tests
ruff check jobapply tests
```

The suite covers resume matching across every role, the timing engine's window
and cap logic, answer resolution including the refuse-to-guess paths, tailoring
validation, LinkedIn URL/date parsing, ATS dispatch, DOM introspection driven
against a real Chromium, and the full dashboard API.

## Project layout

```
jobapply/
├── settings.py          config loading
├── models.py  db.py     SQLAlchemy schema + session helpers
├── llm.py               the single Claude entry point
├── resumes.py           registry, scoring, selection
├── tailor.py            job-specific variants + fact validation
├── render.py            markdown → PDF
├── timing.py            when to send, and why
├── browser.py           Playwright session management
├── pipeline.py          the orchestrator
├── scheduler.py         background jobs
├── cli.py               command line interface
├── sources/linkedin.py  discovery
├── appliers/            easy apply, workday, greenhouse, lever, ashby, generic
├── forms/               DOM introspection + answer resolution
├── manual/              screenshot watcher + vision extraction
├── mailer/              email composition + Gmail API
└── web/                 FastAPI dashboard
```

## Caveats

- LinkedIn's markup changes. When discovery breaks, the selectors are all in one
  block at the top of `jobapply/sources/linkedin.py`.
- Automating LinkedIn is against its Terms of Service. You are responsible for
  how you use this. The throughput caps exist for a reason — raising them is how
  accounts get restricted.
- The agent applies on your behalf using facts you supply. Keep
  `config.yaml` accurate; it will not invent qualifications, but it will
  faithfully send whatever you put in it.

## License

MIT — see [LICENSE](LICENSE).
