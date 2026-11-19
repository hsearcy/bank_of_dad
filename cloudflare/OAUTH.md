# Google sign-in and guest-bank claiming

> **Publication status:** this is the public source snapshot of deployed application
> commit `574d423bd1a50fbc523055e08ea238379f502517`. Production account settings,
> keys and owner identities are not included. The implementation/test reports below
> are historical, scoped local evidence, not the current deployment inventory.
> In particular, historical “not deployed”, login-unverified and launch-gate wording
> must not be read as a current production status claim. See
> [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for setup and verification boundaries.

This is part of the existing Cloudflare target, not another prototype or identity
service. The current dashboard's optional Save / sync links and Dad account
controls use these routes directly:

- `GET /auth/login`: public Google authorization-code startup.
- `GET /auth/callback`: public, browser-bound, one-use callback.
- `GET /auth/choose-bank`, `POST /auth/choose-bank`: the existing explicit
  existing-family/device-trial choice screen.
- `POST /auth/device-bank`: return to the preserved anonymous device bank.
- `POST /auth/logout`: revoke this device's current authority.

First visit still opens the real device bank without a card or sign-in. Stripe
and owner operations are integrated; see [owner controls](OWNER-AND-LAUNCH.md).
The default local configuration accepts loopback only. Public HTTPS and explicit
live billing are supported with private operator configuration; see
[publication notes](PUBLIC-RELEASE.md). This does not approve another deployment.

## Operator configuration

Register a Google **Web application** OAuth client and its exact callback URI,
for example `http://127.0.0.1:8787/auth/callback` for the checked-in local origin.
If using `localhost` instead, configure `BOD_PUBLIC_ORIGIN` and the registered
callback to match it exactly; do not mix cookie origins or ports.

Set these bindings in the ignored local `cloudflare/.dev.vars` file for an
approved disposable local Google test:

```dotenv
BOD_OIDC_CLIENT_ID=your-google-web-client-id
BOD_OIDC_CLIENT_SECRET=your-google-web-client-secret
```

Never check credentials into Wrangler vars/source, paste them into logs, or use
real family data for acceptance. Missing credentials produce a generic 503 and
do not manufacture an identity. A future public release must use Cloudflare's
secret store and HTTPS/Secure cookie handling after its release gates pass.
Nothing here registers or changes a Google/Cloudflare account.

The issuer and discovery/token/JWKS trust roots are fixed to Google. The scope is
`openid profile`, not email, offline access or another Google API. Authorization
uses `prompt=select_account`, S256 PKCE, a random nonce and random state. A
separate HttpOnly/SameSite=Lax browser cookie binds the ten-minute durable state.
Wrong-browser attempts cannot consume it. Duplicate/malformed callback parameters
are rejected before lookup; a matching provider error consumes state without
exchanging a code. Matching callbacks atomically delete state **before** token
HTTP, including failure/replay/concurrent attempts.

Authlib 1.9.9 is pinned. Its actual OAuth client performs code exchange using a
Workers-fetch HTTPX transport; its RS256 verifier checks trusted JWKS signatures
and required issuer/audience/subject/iat/exp claims. Application checks additionally
enforce nonce, authorized party (including multi-audience tokens) and recent iat.
Google's two documented signed issuer spellings map to one canonical
`https://accounts.google.com` identity namespace. Email/profile/access/refresh
claims and provider tokens are not persisted. Every verification fetches current
Google signing keys; there is no stale cross-login JWKS cache. Provider redirects
are refused and fetch uses a 15-second abort deadline. Logs contain endpoint host
and HTTP status only, never callback query strings, tokens or profile claims.

## Durable ownership, sessions and failure recovery

`Directory` is a SQLite Durable Object, added by Wrangler migration `v2`. It owns
issuer/subject-to-family associations, browser-bound pending OAuth transactions,
session associations and explicit bank-choice/return intents. Only Worker/DO
bindings can invoke internal family authorization RPC; arbitrary public headers
or paths cannot invoke claim/session operations.

Each family keeps its existing **same** `FamilyBank` SQLite DO, ledger, children,
settings, history, welcome status and original trial dates. An additive transaction
adds issuer/subject and `_bod_sessions` to a prior version-1 guest bank. Nothing
copies/merges bank data or starts another trial when claiming or signing in. Even
an expired device trial can be saved while its capability remains valid, but its
API access stays expired. A genuinely new direct-login family starts its trial
once when its own DO is first initialized.

Directory transactions reserve an issuer/subject and unique family association
before an idempotent family-local ownership transition. Concurrent identities
cannot both claim the same guest. A crash between reservation and completion is
recovered by a subsequent verified login; failed stale-capability reservations
are released without deleting a newer association. Session installation precedes
cookie delivery. A session-write failure can leave only an unexposed random
session; the next verified login opens the same saved bank. Provider HTTP occurs
outside all synchronous SQLite transactions, and another state/bank request can
commit while token HTTP is waiting.

Claiming invalidates the guest capability and rotates CSRF; old guest HTTP and
sockets cannot inherit signed-in privilege. Signed-in sessions have independent
random capability hashes, CSRF values and thirty-day expiries in the directory
and family DO. Reauthentication rotates the initiating session. Logout revokes
only the current signed-in device; other signed-in devices remain valid. Family
SQL is authoritative, so a failed directory cleanup cannot resurrect access.
A native persisted alarm uses the earliest pending session/access expiry, not the
latest-connected device. Sockets are reauthorized before every invalidation.
Local proxy close handshakes can finish roughly ten seconds after revocation;
HTTP authority is already revoked and no further authorized updates are sent.

If the Google identity already owns another bank, the guest is **not** claimed,
overwritten, merged or deleted. The existing choice screen requires exact Origin
and guest CSRF for the explicit POST. Keeping the device trial leaves both banks
alone. Opening the saved family rotates the retained guest capability and offers
Return to this device's trial in Dad controls. Switching/returning revoke the old
session/socket authority. Durable ten-minute browser-bound transition intents
allow retry after a lost response/restart; these are protocol retry lifetimes,
**not a new anonymous-bank retention or recovery deadline**.

## Verification and limits

```sh
cd cloudflare
uv run pytest -q test_runtime.py test_oauth.py
BOD_TEST_GOOGLE_HTTP=1 uv run pytest -q test_google_http.py
```

The first command uses real local workerd, family/directory SQLite, HTTP, native
WebSockets and actual Chromium. `test_oauth.py` explicitly uses disposable
RS256-signed identities from a separate local HTTP fixture. The fixture validates
PKCE, client authentication and redirect URI; it is not a fake user accepted by
the production verifier. Its endpoint override `BOD_TEST_OIDC_ORIGIN` is an
operator-only binding restricted to HTTP loopback under the local-only guard;
never set it for real Google acceptance or a public release.

Tests cover rejection at the actual token/JWKS boundaries, concurrent/replayed/
wrong-browser/expired state, code substitution, issuer aliases, key rotation,
claim races, original and expired trial preservation, SQLite session-commit
failure/restart recovery, explicit bank choices and return-response loss,
independent visits/devices, idle logout/expiry and the responsive actual UI.

The opt-in probe contacts real Google discovery and token endpoints using
**deliberately invalid credentials/code**. Verified results: discovery HTTP 200,
Google authorization redirect construction, token endpoint HTTP 401, generic
callback HTTP 400, and no application session. Google's public JWKS was also
retrieved successfully. **No real Google user authorization/code exchange has
been completed.** That still requires the operator's registered client and an
approved browser login. No deployment, live data, billing plan or paid action was
performed. Public traffic limits/abuse handling, HTTPS cookies and real-provider
acceptance remain launch gates in [README](README.md). Owner access is integrated
but still needs the real verified owner allowlist configured privately.
