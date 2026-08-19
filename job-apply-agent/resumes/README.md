# Resumes

Every resume the agent can send lives here and is declared in `index.yaml`.

## These files are templates

The `.md` files ship with **placeholder employment history** (`<Current Employer>`,
`<Month YYYY>`, bracketed metrics). Replace them with your real history before you
let the agent send anything — the agent will happily attach whatever is here.

## Adding a resume

1. Drop the file in this folder. `.pdf`, `.docx` and `.md` are all supported.
2. Add an entry to `index.yaml`:

```yaml
  - key: ml-security            # stable id, used in the DB and the UI
    role: "ML Security Engineer"
    file: "ml-security-engineer.md"
    seniority: senior
    summary: >                  # what Claude reads when choosing
      One paragraph on what this variant emphasises.
    keywords: [ml security, llm, prompt injection, model supply chain]
```

3. `jobapply resumes sync` reloads the registry into the database.

## Markdown → PDF

Markdown resumes are rendered to PDF (`data/generated/`) the first time they're
needed, using the stylesheet in `jobapply/web/static/resume.css`. Rendered files
are cached by content hash, so editing the markdown produces a new PDF
automatically. `jobapply resumes render --all` pre-renders everything.

## Tailored variants

When a job scores above `matching.tailor_above_score`, the agent generates a
tailored variant: it reorders and rewrites bullet emphasis to match the job
description **without inventing experience** (the tailoring prompt forbids new
employers, dates, titles or metrics). Tailored resumes are saved to
`data/generated/tailored/` and recorded with `is_tailored=true` and a
`parent_key`, so the dashboard always shows which base resume it came from
and a diff against it.
