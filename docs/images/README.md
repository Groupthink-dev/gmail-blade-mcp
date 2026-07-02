# Screenshot capture checklist — gcp-oauth-setup.md

The setup guide has commented `<!-- screenshot: images/NN-name.png — caption -->`
markers at each slot. To add a screenshot: capture it, drop the PNG in this
directory with the listed filename, and uncomment the marker into a normal
image tag: `![caption](images/NN-name.png)`.

**Redaction — this repo is public.** Before committing, crop or blur:

- the account avatar / email in the console top bar
- the project picker if it shows other project names
- your real Client ID / Client secret in the creation dialog (capture *before*
  creating, or blur the values)

| File | What to capture |
|---|---|
| `01-new-project.png` | The **New project** dialog with a name filled in |
| `02-enable-gmail-api.png` | Gmail API page in the API Library, **Enable** button visible |
| `03-auth-platform-setup.png` | Google Auth Platform **Get started** wizard, app-information step |
| `04-audience-publish.png` | **Audience** page showing Publishing status **In production** |
| `05-create-client.png` | **Create OAuth client** form with application type **Desktop app** selected |
| `06-client-created.png` | The **OAuth client created** dialog with the **Download JSON** button (blur the ID/secret) |
| `07-unverified-warning.png` | "Google hasn't verified this app" screen with **Advanced** expanded |
| `08-consent-scopes.png` | Consent screen listing the requested Gmail permissions |

Capture at a browser width of ~1200 px, PNG, and keep each under ~300 KB
(`sips` or `pngquant` if needed).
