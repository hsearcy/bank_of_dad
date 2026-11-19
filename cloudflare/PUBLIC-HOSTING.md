# Public HTTPS runtime and release gates

> **Publication status:** this is the public source snapshot of deployed application
> commit `574d423bd1a50fbc523055e08ea238379f502517`. Production account settings,
> keys and owner identities are not included. The implementation/test reports below
> are historical, scoped local evidence, not the current deployment inventory.
> In particular, historical “not deployed”, login-unverified and launch-gate wording
> must not be read as a current production status claim. See
> [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for setup and verification boundaries.

This is configuration support, **not a deployment or public-launch approval**.
No Cloudflare resources, DNS/MX/TXT records, provider settings or live family data
were changed. No paid plan is required or purchased by this change.

## Explicit modes

- `wrangler.jsonc` remains HTTP-loopback-only development. Tests use disposable
  persistence and synthetic providers only in this mode.
- `wrangler.production.jsonc` is a portable template with no account ID.
  Replace `bank.example.com`, its exact HTTPS origin and `support@example.com`
  with your own domain/support destination in a private operator configuration.
  Select your own Cloudflare account privately. The template disables workers.dev
  and preview URLs. Check the sample rate-limit namespace IDs for account conflicts.
- Production configuration is deliberately **disabled by default**:
  `BOD_PUBLIC_PILOT=false`. An approved pilot requires an explicit change to
  `true` and `BOD_BILLING_MODE=unavailable`. This is not permanent free access;
  the original 31-day trial expires normally, and only separately audited
  owner grants can extend access. Onboarding/Dad/billing disclose this before
  users start. This label does not provide access control: anyone with the URL
  can reach an enabled pilot. Use disposable/sample data until release gates pass.

The Worker, family DO and directory HTTP ingress all validate runtime config,
request URL scheme/authority and exact Host. Foreign or `null` Origin is rejected
on every route, including static assets, auth and owner routes. Writes require
an exact same Origin; the Stripe webhook may omit Origin but requires explicit
live configuration and its endpoint's raw-body signature.
Forwarded/CF visitor/proto/host headers cannot authorize HTTP as HTTPS. Configured
origins cannot have credentials, paths, query, fragments, trailing slashes or
public port overrides. All session, OAuth-browser and choice cookies share one
host-only `Path=/; HttpOnly; SameSite=Lax` policy, with `Secure` required for public
creation, rotation and deletion. Local loopback cookies remain HTTP-compatible.

Public mode always rejects test-provider URL overrides and sandbox billing.
The no-payments pilot continues to reject Stripe secrets. Explicit non-pilot live
configuration is now supported separately: see [LIVE-BILLING.md](LIVE-BILLING.md).
Neither enabling it nor fixture success establishes real-provider acceptance.
Keep Google and owner identity values in the provider's secret store, not source
or command lines. Do not install secrets/deploy while merely testing this config.
`wrangler secret put` can deploy immediately; it is not harmless preparation.
For an approved staged workflow use version-staged secrets and review the exact
version before separately authorized deployment.

## Anonymous abuse admission

Before any nonstatic public DO dispatch, a native Workers Rate Limiting binding
limits each Cloudflare-supplied `CF-Connecting-IP` to 60 requests/minute/location.
New anonymous first visits additionally have 3/IP/minute and 30 total/minute/location
budgets. Admission is before token generation, family SQL initialization or
directory registration. Missing/failed bindings fail closed; rejected bootstrap
returns no capability cookie and creates no family database. Existing sessions
are not charged to the new-family budget. X-Forwarded-For and cookies cannot
choose the IP budget. The edge overwrites CF-Connecting-IP; local tests model
that boundary, not arbitrary direct access to an origin server.

Native rate limit bindings do not add a paid resource purchase. Limits are
**approximate and per Cloudflare location**, not a strict global budget, durable
quota or complete distributed-DDoS defense. Anonymous attempts can still exhaust
free daily quotas and deny service; rotated networks/locations have independent
budgets. Confirm namespace IDs `194201`–`194203` do not conflict with another
Worker on this account before deployment. Confirm account Free-plan CPU/request/
storage quotas and Python runtime behavior on a deployed disposable pilot.

Official API/config/accuracy reference:
https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/

## Verification

```sh
cd cloudflare
.venv/bin/pytest -q test_public.py
.venv/bin/pytest -q test_runtime.py test_oauth.py test_billing.py test_owner.py
```

Public tests use a real local workerd HTTPS listener, explicit Host and a
self-signed local certificate. TLS verification is disabled **only in that local
test client**, never in provider HTTP. Do not use `--local-upstream` to test Host
rejection: it rewrites hostile Host headers and can hide ingress defects.
Local identity fixtures can establish disposable signed-in state, then tests
remove all provider overrides and restart that same state in public mode to
exercise real choice/session rotation, device return and cookie attributes.
This does not prove a real Google user login or real Stripe acceptance.

## Verification outcome and restart-cleanup regression

Public HTTPS Host/Origin/cookie HTTP acceptance passed in real workerd. The
focused corrected public regressions passed: 5 passed, 18 deselected (abuse,
forbidden configuration, secure choice/session rotation/device return, expired
trial preservation). The preceding run passed the other public checks; it had
harness failures from rewritten Host and unrelated Miniflare SQLite files, now
corrected. A single all-green public-suite rerun was not performed after those
focused fixes.

The earlier integrated suite returned **38 passed, 2 failed**: the cancellation
browser test timed out restarting workerd, then the next billing-form browser
test hit the stopped shared fixture. The two affected browser tests subsequently
passed together in isolation (**2 passed**); the complete billing file also
passed (**4 passed**), before the cleanup correction below. These results do not
prove the historical startup timeout's cause. Its original per-runtime logs were
removed by pytest retention before the follow-up investigation could read them.

A real process-lifecycle defect was reproduced: `Runtime.stop()` waited only for
pywrangler, returning after 3 ms while npm, Node/Wrangler and both workerd children
were still alive. The observed descendants exited within the next 500 ms, leaving
an unsafe overlap with immediate restarts and direct fixture SQLite edits. Stop
also skipped the whole process group if its leader had already exited. It now
signals and waits for all live members of its owned process group, with bounded
TERM/KILL escalation; Linux zombies are excluded because they hold no resources.
No application authorization, billing or readiness deadline was relaxed.

Two synchronized subprocess regressions failed before the correction and passed
afterward (**2 passed**), including an already-exited leader. A real-workerd probe
then returned from stop after 79 ms with **no surviving group members**, rather
than merely waiting for the wrapper. A serialized integrated verification of
`test_harness.py test_runtime.py test_oauth.py test_billing.py test_owner.py
--maxfail=1` completed with **42 passed**. The public suite passed independently
against commit `c5356a5` (**23 passed**). The opt-in real-Google HTTP probe was
not rerun in this follow-up. These results do not prove the original startup
timeout's exact cause or establish deployed-provider acceptance.

A concurrent dry-run also demonstrably captured an empty generated billing
module. Build staging now replaces generated files atomically and skips unchanged
writes. Twelve concurrent staging runs preserved all 15 source modules; a fresh
serialized production-config dry-run succeeded, and packaged module bytes were
compared to source. This fixes the observed packaging race, but does not prove it
caused the separate runtime-start timeout. The first empty artifact is invalid.

## Remaining blockers — do not claim public readiness

1. Real Google browser login/code exchange for the intended client, exact callback
   `https://bank.example.com/auth/callback` (replace with your domain), consent configuration and verified
   owner issuer/subject allowlist remain unaccepted. Discovery or an invalid-code
   request is not login acceptance. Confirm account recovery semantics.
2. Paid production launch still requires real live credentials/account/product/price,
   endpoint and portal configuration, plus actual provider Checkout/webhook delivery/
   cancellation/recovery acceptance. Explicit live/test separation is implemented
   locally; no real-provider acceptance is implied. Sandbox is not real billing.
3. Review [recovery and privacy](RECOVERY-AND-PRIVACY.md) for the native 30-day
   SQLite DO recovery boundary, exact family/directory state, targeted restore
   procedure and proposed truthful disclosure. Private recovery/deletion tooling,
   maintenance fencing, a disposable hosted restore/deletion drill and owner
   approval of privacy/support handling remain outstanding. No independent
   export or automatic abandoned-family purge is implemented; session expiry
   does not establish family-data deletion.
4. Account/domain route conflicts, actual certificate issuance, edge Host/Origin,
   Secure cookies in a real browser, request/subrequest/CPU budgets and deployed
   persistence must be verified on approved disposable state. Local workerd is
   not a hosted acceptance claim. The owner directory can fan out to 50 family
   reads and needs a real Free-plan subrequest-budget check.
5. Review the exact verified commit and public configuration, then obtain/confirm
   authorized deployment scope. Do not alter mail MX/TXT records. Support remains
   `mailto:support@thebankofdad.app`; email delivery was not retested here.
