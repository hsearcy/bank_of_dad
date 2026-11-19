# Combined 31-day trial and subscription-copy verification

## Checkout-fix integration

The final publication applies the original integration delta to public master
`e4d09308dd94fa440d342d0530d8cb7a3163cda2` (PR #26), preserving its
Managed Payments opt-out in both runtimes: `managed_payments[enabled]=false`
for the Worker REST request and `managed_payments.enabled = False` for the
Flask SDK request. The incident-specific customer-preserving retry regression
remains present. Portable provider configuration is unchanged. This is a source
publication only, not a deployment or authorization to modify real family or
Stripe data. The original integration evidence below predates this final rebase.

### Final native-hosted rerun

On the checkout-fix base, the full native hosted suite and copy contract ran in
an isolated environment with Chromium enabled:

```sh
BANK_OF_DAD_BROWSER_TESTS=1 BOD_TEST_CHROMIUM=/usr/bin/chromium \
  python -m pytest -q tests/test_subscription_copy.py tests_hosted
```

Result: **171 passed** in 389.08 seconds. This includes the native Checkout
Managed Payments assertion, duplicate-subscription protection, retained guest
claims, trial migration/rollback, idle authorization and responsive browser
coverage. There were 1423 third-party deprecation warnings; no failures or skips.

### Final workerd rerun

From `cloudflare/` in the locked development environment:

```sh
.venv/bin/python -m pytest -v --tb=short \
  test_trial_31.py test_subscription_copy.py test_trial31_combined.py \
  test_public.py test_billing.py test_runtime.py test_oauth.py test_owner.py
```

Result: **68 passed, 1 Authlib deprecation warning** in 923.05 seconds.
The incident-specific `test_managed_payments_retry_preserves_existing_customer`
passed, along with real local Checkout/portal forms, cancellation invalidation,
idle expiry alarms, public-pilot ingress/copy, genuinely old trial schemas,
claim/reload/restart, no customer creation on trial/claim, same-family push and
cross-family isolation. Staged runtime modules and generated templates matched
source byte-for-byte. An earlier foreground invocation was interrupted by the
execution tool's timeout; this complete logged rerun exited successfully.

## Original integration scope

Integrated, in order, duration commit `6fbf2241208b1e18892dd501b9686b1b7768f491` and copy commit `03eef9113292ff020cadb147c45b4f7d439b7377`, rooted at public master `657b99ad2b065225ba86841487af40ff663a786b`. They cherry-picked without conflicts. The integration additionally corrects the Cloudflare payment-unavailable response, updates the Flask browser badge assertion, expands the stale-copy contract to server source, and adds cross-feature runtime coverage.

The public-pilot account template already correctly says **31-day trial** after the copy commit. The founder paragraph, free-access sentence, Family ID instruction and linked “run it yourself for free” phrase are preserved exactly. The removed sentence stays absent from the subscription section; onboarding still discloses no card and no automatic charge. Existing payment-confirmation language remains.

No provider configuration was changed. No production data, credentials, provider account, deployment, remote push or merge was used. All test identities and databases are disposable; OIDC is a signed loopback fixture, not real Google acceptance. The independently active Managed Payments repair is not included.

## Executed checks

From the repository root (test dependencies in the local environment):

```sh
TMPDIR="${TMPDIR:?Set a disposable scratch directory}" \
PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" \
BOD_TEST_CHROMIUM=/usr/bin/chromium BANK_OF_DAD_BROWSER_TESTS=1 \
cloudflare/.venv/bin/python -m pytest -q \
  tests/test_subscription_copy.py tests_hosted/test_trial_31.py \
  tests_hosted/test_legacy_upgrade.py tests_hosted/test_guest.py \
  tests_hosted/test_trial_access.py tests_hosted/test_trial_browser.py \
  tests_hosted/test_guest_browser.py tests_hosted/test_owner.py
```

**118 passed**. Includes real Flask Chromium at phone/tablet/desktop sizes, 31-day badge, expiry and restore, guest claim, operator access and legacy migration. After adding explicit 21-days-left assertions before/after claim, reran `tests_hosted/test_trial_31.py tests/test_subscription_copy.py`: **47 passed**.

From `cloudflare/`, with the same scratch and browser-cache environment:

```sh
.venv/bin/python -m pytest -q \
  test_trial_31.py test_subscription_copy.py test_owner.py test_oauth.py test_runtime.py
```

**39 passed** in real workerd, including legacy-schema migration and concurrent first access, responsive guest/saved-owner/pilot copy, owner grant/revoke/readback, signed identity claims, same-family browser push, cross-family isolation, reconnect/logout, idle WebSocket expiry, SQL rollback and restart persistence. No actual identity or payment provider account was used.

Additional runs:

- `test_public.py`: **23 passed**, including real TLS public-pilot ingress and exact unavailable-payment copy. This was part of the initial invocation alongside the combined test.
- `test_trial31_combined.py`: **1 passed** on its final standalone run. The first invocation had a Playwright strict-locator ambiguity (two legitimate status paragraphs); the selector was narrowed, then the whole test reran successfully. This was a test defect, not an application failure.

The combined test uses genuine old persisted SQLite schema without `trial_policy`, seeded at original start + seven days. Browser access ten days later upgrades to original start + 31 days and shows **21d left**, rather than restarting. It checks repeated reads, reload, same-bank claim, restart, and a second signed-in client; exact timestamps and `customer IS NULL` persist. The sample investment still has its original **30-day** contract and matures before a fresh 31-day trial expires; advancing only the educational bank clock exercises the actual maturity projection without altering trial dates.

`git diff --check` passes. Runtime staging was compared byte-for-byte with the modified source modules. The full user-visible template/static/server source scan finds no stale seven-day/week trial copy. Historical migration references and unrelated seven-day session, allowance, savings and recovery policies remain intentionally unchanged.

## Limitations

These are real local workerd/SQLite/Chromium and Flask tests, not deployment, real-provider sign-in or payment acceptance. No card/customer/charge was created by trial/signup/claim verification. Existing third-party Flask-Session UTC and Authlib deprecation warnings remain. The duration migration cannot distinguish an old manually assigned exact seven-day duration from the former default; the policy document explains that schema limitation.
