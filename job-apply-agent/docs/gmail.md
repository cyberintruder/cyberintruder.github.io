# Gmail setup

The manual workflow creates real Gmail drafts with your resume attached. That
needs an OAuth client you own — there is no shared credential.

## 1. Create an OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create
   a project (or pick an existing one).
2. **APIs & Services → Library** → enable the **Gmail API**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External** is fine for a personal account.
   - Add your own address under **Test users** — an app in testing mode only
     works for listed test users, which is exactly what you want here.
   - Scopes: you do not need to declare them on the consent screen for a
     testing-mode app; the flow requests them directly.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app**.
   - Download the JSON.

## 2. Save the client file

```bash
mkdir -p secrets
mv ~/Downloads/client_secret_*.json secrets/credentials.json
chmod 600 secrets/credentials.json
```

`secrets/` is gitignored. Never commit either file.

## 3. Authorise

```bash
jobapply gmail-auth
```

A browser opens. Sign in, grant access, and the token is written to
`secrets/token.json` (mode 600). It refreshes itself from then on.

On a headless machine:

```bash
jobapply gmail-auth --headless
```

## Scopes

| Scope | Why |
|---|---|
| `gmail.compose` | create, read and update drafts |
| `gmail.send` | send a draft — only used when `JOBAPPLY_EMAIL_MODE=send` |

Neither scope grants read access to your inbox. The agent never lists, reads or
searches your mail.

## Draft vs. send

`JOBAPPLY_EMAIL_MODE` controls the last step:

- `draft` (default) — the agent creates the draft, attaches the chosen resume,
  and stops. The application sits in the review queue with the draft rendered in
  the dashboard, where you can edit the recipient, subject and body before
  sending from Gmail.
- `send` — the draft is created and sent immediately.

`JOBAPPLY_DRY_RUN=true` overrides both: no draft is ever sent while dry run is on.

## Troubleshooting

**`no Gmail token at secrets/token.json`** — run `jobapply gmail-auth`.

**`Gmail credentials are invalid`** — the refresh token was revoked (this happens
if the consent screen is still in testing mode after 7 days, or you changed your
password). Delete `secrets/token.json` and re-run `jobapply gmail-auth`.

**`access_denied` during the browser flow** — your address is not in the
**Test users** list on the OAuth consent screen.

**`OAuth client file not found`** — the downloaded JSON is not at
`secrets/credentials.json`, or `JOBAPPLY_GMAIL_CREDENTIALS` points elsewhere.
