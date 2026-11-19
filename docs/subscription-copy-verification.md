# Subscription copy and 31-day trial wording

This document records the original copy/layout-only change based on `657b99ad2b065225ba86841487af40ff663a786b`. Its original runs did not change trial logic, investment periods, provider configuration or production state; combined duration verification is linked below.

- Dad controls and the paused-access page share the exact founder contribution paragraph before the annual price, the requested free-access wording, an explicit request to include the displayed Family ID, and the existing configured support link.
- The open-source sentence links only “run it yourself for free” to the public GitHub project.
- The trial-end sentence no longer includes the removed no-card/automatic-charge sentence. Welcome retains the honest no-sign-up/no-card and no-automatic-charge disclosures. Checkout still explains immediate paid annual subscription and Stripe confirmation.
- Existing guest/save, signed-in, trial, paid, free, expired, pilot, existing-customer management and separate-device-bank distinctions remain. Pilot still has no purchase action or contribution/price pitch.
- Templates/onboarding and trial documentation use 31 days; unrelated seven-day authentication policies, Cloudflare recovery limits and default investment/savings periods remain unchanged.

## Executed verification

From the repository root, with a disposable environment containing only self-hosted runtime dependencies plus pytest/Playwright/websocket-client test tools:

```sh
TMPDIR="${TMPDIR:?Set a disposable scratch directory}" BANK_OF_DAD_BROWSER_TESTS=1 \
BOD_TEST_CHROMIUM=/usr/bin/chromium \
/path/to/disposable-venv/bin/python -m pytest -q \
  tests/test_subscription_copy.py tests/test_onboarding.py \
  tests/test_html_injection.py \
  tests/test_experience_browser.py::ExperienceBrowserTests::test_footer_dialogs_and_compact_screen_controls
```

Result: **38 passed, 2 subtests passed**. Includes 32 combinations of trial/expired/paid/free, guest/signed-in, pilot/non-pilot and existing-customer state; missing-support fallback; retained device-bank notice; no stale template/static trial copy; and real self-hosted Chromium at 390/1280px.

From `cloudflare/`:

```sh
TMPDIR="${TMPDIR:?Set a disposable scratch directory}" \
PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" \
BOD_TEST_ARTIFACTS="$TMPDIR/bod-trial-copy-artifacts" \
.venv/bin/python -m pytest -q test_subscription_copy.py
```

Result: **1 passed** (eight real Chromium contexts: guest and saved owner at 390/1280px, both local and public-pilot ingress on disposable local workerd). Exact copy, price/checkout distinctions, contact/GitHub links, displayed Family ID, no document/card/panel horizontal overflow and no page errors asserted. Public-mode requests are forwarded only to the owned loopback TLS listener, never the production host. Identity uses the existing signed loopback OIDC fixture, not Google. No payment provider is contacted. Screenshots include the pricing and help/sign-out portions; inspected phone and desktop images are readable without horizontal clipping.

## Integration boundary

The copy and duration changes are now combined on the same public-master base. The unavailable-billing response and Flask browser badge expectation are also aligned to 31 days. See [combined verification](trial31-integration-verification.md) for the real workerd/Flask results, legacy-trial persistence checks, and remaining limitations. No deployment is implied by these local tests.
