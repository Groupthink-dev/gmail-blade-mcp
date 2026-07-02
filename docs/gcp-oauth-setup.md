# Google Cloud Console setup — OAuth desktop client for Gmail Blade MCP

This guide walks you through creating the Google Cloud OAuth **desktop client**
that Gmail Blade MCP uses to access your mailbox. You only do this once. At the
end you will have three values:

```
GMAIL_OAUTH_CLIENT_ID=...
GMAIL_OAUTH_CLIENT_SECRET=...
GMAIL_OAUTH_REFRESH_TOKEN=...
```

which you can paste into any headless deployment (Stallari → Connections, a
systemd unit, a container env) — or skip the env vars entirely and let the
server use the token file at `~/.gmail-blade/token.json`.

**Time required:** ~10 minutes.
**Cost:** free — the Gmail API has no billing requirement for personal use.

> **Console UI note.** Google reorganised these screens in 2024–2025: the old
> "OAuth consent screen" page is now the **Google Auth Platform** section with
> separate **Branding**, **Audience**, **Clients**, and **Data Access** pages.
> This guide follows the current layout. If your console still shows the old
> single-page consent screen, the same settings exist — just on one page.

---

## Contents

1. [Create (or select) a Google Cloud project](#step-1--create-or-select-a-google-cloud-project)
2. [Enable the Gmail API](#step-2--enable-the-gmail-api)
3. [Configure the Google Auth Platform (consent screen)](#step-3--configure-the-google-auth-platform)
4. [Publish the app "In production" — avoid the 7-day token trap](#step-4--publish-the-app-in-production)
5. [Create the OAuth desktop client](#step-5--create-the-oauth-desktop-client)
6. [Save the client credentials locally](#step-6--save-the-client-credentials-locally)
7. [Mint the refresh token](#step-7--mint-the-refresh-token)
8. [Verify and deploy](#step-8--verify-and-deploy)
9. [Troubleshooting](#troubleshooting)

---

## Step 1 — Create (or select) a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and sign
   in with the Google account whose Gmail you want the server to access.
2. Click the **project picker** in the top bar (next to the "Google Cloud"
   logo) → **New project**.
3. Name it something recognisable, e.g. `gmail-blade-mcp`. The organisation /
   location fields can stay at their defaults for a personal account.
4. Click **Create**, then make sure the new project is *selected* in the
   picker (the top bar should show its name).

<!-- screenshot: images/01-new-project.png — "New project" dialog with name filled in -->

> **Which account?** Everything in this guide happens inside one Google
> account: the project owner, the consent screen, and the mailbox being
> accessed can all be the same personal account. No Workspace subscription is
> needed.

## Step 2 — Enable the Gmail API

1. In the left navigation, go to **APIs & Services → Library**
   (or open [the library directly](https://console.cloud.google.com/apis/library)).
2. Search for **Gmail API** and open it.
3. Click **Enable**. The page flips to the API dashboard once it is active.

<!-- screenshot: images/02-enable-gmail-api.png — Gmail API library page with the Enable button -->

Direct link (with your project selected):
<https://console.cloud.google.com/apis/library/gmail.googleapis.com>

> The optional Gemini AI tools (`gmail_classify`, `gmail_summarise`) do **not**
> use this project — they take a separate [Google AI Studio](https://aistudio.google.com/)
> API key via `GOOGLE_API_KEY`. Nothing to enable here for those.

## Step 3 — Configure the Google Auth Platform

1. Go to **APIs & Services → OAuth consent screen** — this now lands on the
   **Google Auth Platform** overview. First-time visitors see a **Get started**
   button; click it.
2. **App information (Branding):**
   - **App name:** `Gmail Blade MCP` (or anything — this is what the consent
     screen shows you when you authorise).
   - **User support email:** your own address.
3. **Audience:** choose **External** unless your account belongs to a Google
   Workspace organisation — in that case choose **Internal** (see the note
   below; Internal is strictly nicer if you have it).
4. **Contact information:** your email again.
5. Agree to the user-data policy checkbox and click **Create**.

<!-- screenshot: images/03-auth-platform-setup.png — Google Auth Platform "Get started" wizard, app information step -->

You do **not** need to add scopes on the **Data Access** page or upload a
logo. Scopes are requested at mint time by the `auth` CLI verb, and unverified
apps work fine for personal use (you will click through one warning in
Step 7).

> **External vs Internal.**
> - **Internal** (Workspace accounts only): only members of your organisation
>   can authorise the app. No verification, no "unverified app" warning, no
>   publishing-status concerns. Choose this if you can.
> - **External** (personal Gmail): anyone with a Google account *could*
>   authorise, but until the app is verified it is capped at 100 users and
>   shows a warning screen — irrelevant for a single-user personal tool.

## Step 4 — Publish the app "In production"

**This is the step people miss, and it breaks everything a week later.**

While an External app's publishing status is **Testing**, Google expires its
refresh tokens after **7 days**. Your server works perfectly at first, then
every credential dies the following week with `invalid_grant`, and you have to
re-mint. Publishing to production makes refresh tokens durable.

1. Go to **APIs & Services → OAuth consent screen → Audience**
   (direct: <https://console.cloud.google.com/auth/audience>).
2. Under **Publishing status**, click **Publish app** and confirm.
3. The status should now read **In production**.

<!-- screenshot: images/04-audience-publish.png — Audience page showing Publishing status "In production" -->

Notes:

- Publishing does **not** require verification. The console may list
  verification requirements when you confirm — for a personal-use app you can
  ignore them. The only consequence of staying unverified is the one-time
  "Google hasn't verified this app" click-through and the 100-user cap.
- You do **not** need to add yourself as a test user once the app is in
  production. (Test users are only relevant in Testing status.)
- **Internal** apps skip this entirely — there is no publishing status and no
  7-day expiry.

## Step 5 — Create the OAuth desktop client

1. Go to **APIs & Services → OAuth consent screen → Clients**
   (direct: <https://console.cloud.google.com/auth/clients>), or the legacy
   path **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Click **Create client**.
3. **Application type:** **Desktop app**. This is important — the desktop
   type is what allows the local `gmail-blade-mcp auth` flow to receive the
   authorisation code on a `localhost` redirect. Do not pick "Web application".
4. **Name:** `gmail-blade-mcp` (only visible in the console).
5. Click **Create**.

<!-- screenshot: images/05-create-client.png — Create OAuth client form with "Desktop app" selected -->

A dialog shows your **Client ID** and **Client secret**.

> ⚠️ **Download the JSON now.** Since mid-2025 Google only reveals the client
> secret **at creation time** — you cannot view it again later. Click
> **Download JSON** in the creation dialog. If you lose the secret, you don't
> have to start over: open the client on the Clients page and add a new secret
> (or just create a new client).

<!-- screenshot: images/06-client-created.png — "OAuth client created" dialog with the Download JSON button -->

For a desktop client the secret is not actually confidential in the OAuth
sense (the [RFC calls these "public clients"](https://datatracker.ietf.org/doc/html/rfc8252#section-8.5))
— but treat it as a secret anyway, because together with your refresh token it
grants mailbox access.

## Step 6 — Save the client credentials locally

Put the downloaded JSON where the `auth` verb finds it:

```bash
mkdir -p ~/.gmail-blade
mv ~/Downloads/client_secret_*.json ~/.gmail-blade/credentials.json
chmod 600 ~/.gmail-blade/credentials.json
```

Alternatives (checked in this order by the `auth` verb):

| Source | How |
|---|---|
| CLI flags | `gmail-blade-mcp auth --client-id ... --client-secret ...` |
| Environment | `GMAIL_OAUTH_CLIENT_ID` + `GMAIL_OAUTH_CLIENT_SECRET` |
| File | `~/.gmail-blade/credentials.json` (the downloaded JSON) |

## Step 7 — Mint the refresh token

Run the interactive auth flow **on a machine with a browser** (this is the
only part of Gmail Blade MCP that ever opens one):

```bash
uvx gmail-blade-mcp auth                 # modify + send scopes (default)
uvx gmail-blade-mcp auth --scopes read   # read-only grant
```

> **Scope choice is fixed at mint time.** If you ever plan to enable write
> tools (`GMAIL_WRITE_ENABLED=true`), mint with the default (write) scopes now
> — the runtime write-gate still keeps writes off until you flip the env var.
> A read-only token can only be upgraded by re-minting.

Your browser opens the Google consent screen:

1. Pick the Google account whose mailbox this server should use.
2. **"Google hasn't verified this app"** — expected for an unverified
   personal app. Click **Advanced → Go to Gmail Blade MCP (unsafe)**.
3. Review the requested Gmail permissions and click **Continue / Allow**.

<!-- screenshot: images/07-unverified-warning.png — "Google hasn't verified this app" screen with Advanced expanded -->
<!-- screenshot: images/08-consent-scopes.png — consent screen listing the requested Gmail scopes -->

Back in the terminal, the verb prints the paste-ready credential block:

```
GMAIL_OAUTH_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
GMAIL_OAUTH_CLIENT_SECRET=GOCSPX-...
GMAIL_OAUTH_REFRESH_TOKEN=1//0g...
```

and (unless you passed `--no-save-token`) also writes the refresh token to
`~/.gmail-blade/token.json` with mode `0600` for standalone local use.

## Step 8 — Verify and deploy

Check the credential works and see exactly what was granted:

```bash
uvx gmail-blade-mcp auth --check
# → account email + granted scopes, or exit 1 with a typed error
```

Then deploy:

- **Stallari:** open **Connections → gmail-blade-mcp** and paste the three
  `GMAIL_OAUTH_*` values. The blade builds credentials in memory at spawn and
  never writes them to disk.
- **Any other headless host:** set the same three env vars.
- **Local / Claude Desktop:** nothing more to do — the server finds
  `~/.gmail-blade/token.json` automatically.

The server itself never opens a browser: if credentials are missing or dead it
returns a typed error telling you to re-run `gmail-blade-mcp auth`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Everything worked, then `invalid_grant` / auth errors ~7 days later | Publishing status is still **Testing** | Step 4: publish **In production**, then re-mint with `gmail-blade-mcp auth` (the old token is already dead) |
| `invalid_grant` immediately | Refresh token revoked (you removed the app's access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions), changed your password with certain security events, or minted >50 tokens for the same client+account, which silently invalidates the oldest) | Re-mint with `gmail-blade-mcp auth` |
| `Error 403: access_denied` during consent | App is in **Testing** and your account isn't in the test-user list | Publish In production (Step 4), or add yourself under **Audience → Test users** |
| `ScopeError` / `invalid_scope` when write tools run | Token was minted `--scopes read` but the server started with `GMAIL_WRITE_ENABLED=true` | Re-mint: `gmail-blade-mcp auth --scopes write` |
| "Google hasn't verified this app" blocks with no **Advanced** link | Some browsers hide it; or your Workspace admin blocks unverified third-party apps | Try clicking the small **Advanced** text again / another browser; on Workspace, make the app **Internal** instead |
| Lost the client secret | Google only shows it at creation time | Clients page → open the client → add a new secret (or create a fresh client), then re-mint |
| `redirect_uri_mismatch` | Client was created as **Web application** instead of **Desktop app** | Create a new client with type **Desktop app** (Step 5) |
| Consent screen loops or shows the wrong account | Multiple Google accounts in the browser | Use a private window, or pick the account explicitly at the chooser |
| Want to start over cleanly | — | Revoke at [myaccount.google.com/permissions](https://myaccount.google.com/permissions), `rm ~/.gmail-blade/token.json`, re-mint |

## Reference

- [Google Auth Platform — get started](https://support.google.com/cloud/answer/15544987)
- [Manage app audience (publishing status, test users)](https://support.google.com/cloud/answer/15549945)
- [Manage OAuth clients](https://support.google.com/cloud/answer/15549257)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [OAuth 2.0 for native apps (RFC 8252)](https://datatracker.ietf.org/doc/html/rfc8252)

---

*Screenshots for this guide live in [`docs/images/`](images/README.md) — see
the capture checklist there. The commented `<!-- screenshot: ... -->` markers
above show where each image slots in.*
