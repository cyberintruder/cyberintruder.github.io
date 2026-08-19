# The manual workflow: screenshots → Gmail drafts

Not every job worth applying to is scrapeable. A lot of the best openings are
plain LinkedIn posts — a hiring manager writing "we're looking for a detection
engineer, DM me or send your CV to careers@…". Those have no job ID, no Apply
button, and no structure to scrape.

So you screenshot them, and the agent reads the image.

## How to use it

1. Screenshot the post. Phone or desktop, whole screen or a crop — anything
   legible works. Include the poster's name and the "3d" timestamp if visible;
   both get used.
2. Drop the image in `inbox/screenshots/` (syncing that folder from your phone
   works well).
3. Run the watcher:

   ```bash
   jobapply watch
   ```

   or a one-shot pass:

   ```bash
   jobapply ingest
   jobapply ingest --folder ~/Pictures/linkedin-posts
   ```

## What happens

```
image → Claude vision → structured job → resume match → email draft → Gmail
```

Claude extracts the role, company, location, workplace type, seniority, the
requirements as written, the poster's name, any email address, any apply URL,
the apply instruction ("DM me", "send your CV to…", "comment interested"), the
relative age of the post, and a confidence score for how legible it was.

The extraction prompt forbids inference: if the company is not visible in the
image, the field comes back empty rather than guessed.

Then the normal pipeline runs — the same resume scoring, the same timing engine.
The difference is the channel: instead of driving a form, the agent composes an
email and creates a **Gmail draft** with the chosen resume attached.

## What lands in review

- **No email address in the post.** The application waits in the review queue
  with "the post named no email address". Add a recipient on the detail page, or
  set `manual.fallback_recipient` in `config.yaml`.
- **Not a job post.** Hiring announcements with no role, articles, screenshots
  of profiles — these are marked `ignored`, not queued.
- **Gmail not authorised.** The draft is stored in the database so nothing is
  lost; run `jobapply gmail-auth` and retry.

## Deduplication

Screenshots are hashed with SHA-256. Re-dropping the same image — or the same
post screenshotted twice at different crops of identical bytes — never creates a
second application. Processed images are moved to `processed/`.

## Editing the draft

Open the application in the dashboard. The email card shows the recipient,
subject and body in editable fields, plus the attachment name. Edits are saved
back to the database and reflected in the Gmail draft the next time it is
written.

## Cost

One vision call per screenshot, plus one composition call. Screenshots that turn
out not to be job posts cost a single extraction call and stop there.
