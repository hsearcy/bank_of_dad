# Owner controls and minimal public-launch handoff

> **Publication status:** this is the public source snapshot of deployed application
> commit `574d423bd1a50fbc523055e08ea238379f502517`. Production account settings,
> keys and owner identities are not included. The implementation/test reports below
> are historical, scoped local evidence, not the current deployment inventory.
> In particular, historical “not deployed”, login-unverified and launch-gate wording
> must not be read as a current production status claim. See
> [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for setup and verification boundaries.

## Implemented on the integrated Worker

`GET /owner` reuses the existing Flask owner template and responsive CSS. Access
requires a live signed-in family session whose server-owned Google issuer/subject
is in `BOD_OWNER_IDENTITIES`. The owner's trial/payment can be expired. Guests,
ordinary parents, body/header role claims and local Dad controls cannot authorize
owner operations. Missing allowlist returns 404; malformed config fails closed.
The ordinary free self-hosted app does not acquire an owner requirement.

Privately configure the actual verified identity, never an email or guessed ID:

```json
[{"issuer":"https://accounts.google.com","subject":"ACTUAL_VERIFIED_GOOGLE_SUBJECT"}]
```

Put this JSON in the `BOD_OWNER_IDENTITIES` secret binding, not source. This example
is a placeholder, not the owner's identity. Obtain the subject from the trusted
server-owned association after a real successful Google login. Do not print tokens
or the complete session/identity directory to obtain it.

The panel lists 50 families per page, supports exact/prefix Family ID search and
shows trial/paid/free/expired status with dates. It distinguishes current clock-
derived access from last-reconciled Stripe data. Its projection excludes parent
identities, capability hashes, customer IDs and all child/ledger data. Only saved
families associated with a verified sign-in in the identity directory are listed;
this does not require an active browser session for each family. Device-only guest
banks are hidden from list, prefix and exact-ID searches, and cannot be targeted
by owner grant/revoke. Guest banks, trials, capabilities and the historical registry
are retained unchanged. Filtering and DISTINCT happen before pagination and RPCs,
using the existing family index; at most five family RPCs run concurrently.

`POST /owner/access` requires exact Origin, session CSRF, exact existing Family ID,
a nonempty reason of at most 300 characters and explicit confirmation for revoke.
The family DO commits the free flag and `_bod_access_audit` row in one synchronous
transaction. Audit retains family, action, actor issuer/subject, reason, timestamp
and previous/current flag. Twenty recent nonidentity audit entries are visible per
family; the durable table retains the full history. HTML escapes audit notes.
After commit the directory reads the exact target again; the redirect GET reads it
afresh. A native alarm reauthorizes sockets and rearms paid/trial/session expiry.
An audit-insert failure rolls back the flag. A post-commit transport/alarm/readback
failure can return 503 despite a committed change: reload that Family ID before
retrying; successful delivery is not claimed for an ambiguous response.

Free grants never modify trial dates, paid entitlement, Stripe customers or
subscriptions. **Grant/revoke does not cancel Stripe renewal.** Cancellation stays
in the existing billing portal. Reasons should not contain personal information.

`BOD_SUPPORT_URL` is configured as `mailto:support@thebankofdad.app` in the local
Wrangler config and validated before rendering welcome, Dad controls and billing.
The user reports forwarding is configured; no outbound mail service, new inbox,
SMTP credentials or test email was created here.

## Local verification

```sh
cd cloudflare
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q test_owner.py test_owner_selection.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q test_runtime.py test_oauth.py test_billing.py
```

Owner tests exercise actual workerd, real DO SQLite, HTTP, signed disposable OIDC
fixtures and Chromium. They verify owner/guest/ordinary-parent denial, malformed
and absent allowlists, CSRF and foreign/null/missing Origin, bypass attempts,
expired owner/saved target, atomic audit failure/full database rollback, paid entitlement
preservation, audit/restart readback, and actual phone/tablet/desktop grant/revoke.
Idle browser sockets redirect to billing on revoked access with polling disabled.
The actual expired page exposes the configured support mailto link. Tests do not
perform a real Google login, contact Stripe or change live family data. Selection
tests use disposable SQLite with 10,000 registry entries and duplicate identity
associations to check pagination, search, indexed selection, zero guest RPCs and
bounded parallel waits. Their delayed RPC timing is synthetic, not hosted latency.

Verified results on this change:
- Full integrated command `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
  test_owner.py test_runtime.py test_oauth.py test_billing.py`: **40 passed** in
  869.73 seconds; one existing Authlib deprecation warning.
- Disposable separate environment installed from `requirements-hosted.txt`,
  `requirements-test.txt`, pytest and Playwright: `python -m pytest -q tests
  tests_hosted/test_owner.py tests_hosted/test_trial_access.py
  tests_hosted/test_owner_browser.py`: **88 passed, 10 skipped, 32 subtests passed**.
  The ten opt-in browser tests remain skipped, not represented as passes.
- A second disposable environment containing only `requirements.txt` ran
  `python -m unittest discover -s tests -p test_onboarding.py -v`: **1 passed**.
  Authlib, Stripe, Flask-Session and SQLAlchemy were explicitly confirmed absent.
- `git diff --check` passed; no default worktree `data/` directory was created.

## Saved-family listing regression verification

- `test_owner.py test_oauth.py test_runtime.py test_preview_banner.py test_public.py`:
  **63 passed** in 547.64 seconds, including genuine local Chromium owner
  grant/revoke and phone/tablet/desktop rendering. One existing Authlib warning.
- `test_owner_selection.py`: **3 passed**. The original guest-list regression was
  first observed failing in real workerd, then passed with list/prefix/exact-ID
  hiding and guest grant/revoke rejection; the complete guest snapshot stayed intact.
- Controlled delayed-RPC comparison (50 saved families, 10,000 registry entries,
  20 ms fixture delay): serial baseline **1050.5 ms**, bounded parallel **267.6 ms**,
  both 50 reads; concurrency changed from one to five. These are synthetic local
  adapter measurements, not production latency. Real local owner list/search
  requests measured 53.0–68.3 ms on the small runtime fixture.
- No signed-in production browser/session or live Stripe customer was accessed.
  Hosted owner-list latency still requires the owner's own browser verification.

## Public HTTPS configuration and remaining launch gates

The prior local-only ingress/cookie blockers are now addressed by explicit
HTTPS runtime configuration. Public hosting remains disabled by default and
public payments remain unavailable. See [Public hosting](PUBLIC-HOSTING.md)
for the authoritative configuration, abuse limits, test boundaries and remaining
real-provider/privacy/recovery gates. No deployment or live-data change was made.
