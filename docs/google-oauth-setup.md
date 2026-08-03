# Google OAuth Setup

One-time setup for the Google OAuth 2.0 client that authorizes the agent to read
Gmail and Google Calendar and to send the daily digest to your own inbox. The
refresh token this produces is stored in the OS keyring by
`python -m app.auth --setup` — see [SECURITY.md](../SECURITY.md) §2 for storage
rules.

Both Gmail and Calendar are authorized by a **single client** with three scopes.
There is no separate Calendar setup.

---

## 1. Create or select a Google Cloud project

- Go to https://console.cloud.google.com.
- Top bar → project dropdown → **New Project**.
- Name it anything (e.g. `chief-of-staff-agent`) and create.

Skip if you already have a project you want to use.

## 2. Enable the two APIs

APIs & Services → **Library**, then enable both:

- **Gmail API**
- **Google Calendar API**

The scopes the agent requests (`gmail.readonly`, `gmail.send`,
`calendar.readonly`) are declared in `app/auth/constants.py`. `gmail.send`
is used only by `render_and_deliver` to email the digest to
`identity.delivery_address`.

## 3. Configure the OAuth consent screen

APIs & Services → **OAuth consent screen**.

- **User type:** External (unless you have a Google Workspace org — then
  Internal is fine and skips the test-user step).
- **App name / support email / developer email:** minimum required fields.
- **Scopes:** leave empty. Desktop-app flow requests scopes at runtime, not
  from this screen.
- **Test users:** add your own Gmail address. Required while the app is in
  Testing mode, otherwise consent fails with `Error 403: access_denied`.

**Publishing status:** leave as **Testing** to start. Refresh tokens issued
in Testing mode expire after 7 days. For a single-user tool you can push
**Publish App** (no verification is required for a Desktop app used by
yourself); once published, refresh tokens don't expire until you revoke them.

## 4. Create the OAuth client — must be Desktop app

APIs & Services → **Credentials** → **+ Create Credentials** → **OAuth
client ID**.

- **Application type: Desktop app** ← required. The agent uses
  `InstalledAppFlow.run_local_server` (see `app/auth/google.py`), which needs
  the loopback redirect URI that only Desktop-app clients allow. Any other
  type (Web application, TVs and Limited Input, etc.) will complete the flow
  without issuing a refresh token, and `app.auth --setup` will exit with:

  > OAuth flow completed but returned no refresh token. Ensure the OAuth
  > client in Google Cloud Console is type 'Desktop app'.

- **Name:** anything; not user-visible.

The create dialog shows the **Client ID** and **Client secret**. Copy both.

## 5. Add credentials to `.env`

```
GOOGLE_OAUTH_CLIENT_ID=<client id from step 4>
GOOGLE_OAUTH_CLIENT_SECRET=<client secret from step 4>
```

`.env` is gitignored. Never commit it.

## 6. Run the OAuth flow

```bash
uv run python -m app.auth --setup
```

- Browser opens to Google's consent screen.
- Sign in with the account you added as a test user.
- You will see **"Google hasn't verified this app"** — click **Advanced** →
  **Go to chief-of-staff-agent (unsafe)**. This is expected for an unverified
  Desktop app; you are consenting to your own client.
- Grant **Read your email**, **Send email on your behalf**, and **View your
  calendars**.
- The tab shows "The authentication flow has completed" and closes.

The refresh token is now stored in the OS keyring under:

- service: `chief-of-staff-agent`
- account: `google_oauth_refresh_token`

## 7. Verify

```bash
uv run python -m app.run --dry-run
```

Then check the persisted per-sensor status:

```bash
sqlite3 data/runs.db "SELECT errors FROM runs ORDER BY started_at DESC LIMIT 1;"
```

- `{"gmail": null, "calendar": null, ...}` — both sensors authorized correctly.
- `"...insufficient authentication scopes..."` — the stored token predates a
  scope being added to `SCOPES` in `app/auth/constants.py`. Fix with
  `--reauth` (below).

---

## Rotating or re-consenting

Replace the stored refresh token (after adding a scope, revoking access in
Google Security Activity, or moving to a different account):

```bash
uv run python -m app.auth --reauth
```

The browser flow runs first; the stored token is only overwritten on success.
If you cancel in the browser, the old token stays intact.

## Common failure modes

| Symptom                                                         | Cause                                             | Fix                                                    |
|-----------------------------------------------------------------|---------------------------------------------------|--------------------------------------------------------|
| `OAuth flow completed but returned no refresh token`            | Client is not type Desktop app                    | Recreate the client in step 4 as Desktop app           |
| Browser shows `Error 403: access_denied`                        | Consent screen is in Testing and your address isn't a test user | Add yourself under OAuth consent screen → Test users   |
| `Already authenticated. Use --reauth to replace the stored token.` | A token already exists in the keyring          | Run `--reauth` to overwrite                            |
| `insufficient authentication scopes` in `runs.db.errors`        | Stored token was minted before a new scope was added | Run `--reauth`                                         |
| Refresh token stops working after ~7 days                       | Consent screen is still in Testing mode           | OAuth consent screen → **Publish App**                 |
