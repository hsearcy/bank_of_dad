# Anonymous signup lifecycle correction

## Changes and regression evidence

- Guest login startup no longer revokes the SID/CSRF used by the still-open dashboard. The verified callback still rotates both, revokes the guest capability, and consumes browser-bound OAuth state once. Ordinary authenticated login keeps its existing reset behavior.
- A stale HTTP 401 hides the old document and rechecks the current browser cookie before initiating OIDC. Successful recovery reloads the document and CSRF; it never replays a mutation.
- Guest capability and server-session recovery have no application deadline. Persistent cookies remain subject to browser retention. Retained legacy guest sessions are upgraded before expired-session cleanup; authenticated session expiry remains unchanged.
- 31-day trial timestamps are not reset by signup. Expired access remains blocked until a subscription or operator grant applies.

Before the fix, the new Chromium regression observed four authorization starts during the deliberately held startup, rather than one. After the fix it verifies one real Authlib/OIDC flow, an existing WSS connection with polling disabled, a committed gift and pushed refresh during startup, ordinary tab reload, session rotation, a genuine stale revoked-SID 401, and exact preservation of all bank tables and ID sequences. It runs at 390px and 1280px.

HTTP coverage claims retained guest tokens at ages 31, 365 and 3650 days, including legacy session storage across restart/cleanup. It checks expired read/write denial, unchanged trial timestamps, exact bank preservation and restored access after an operator free-access grant. Chromium additionally pauses a bank aged 45 days, claims it without resetting expiry, and exercises the existing subscription route.

## Commands and final results

From the isolated feature worktree:

```sh
.venv/bin/python -m pytest tests_hosted -q --disable-warnings
# 82 passed

for run in 1 2 3; do
  .venv/bin/python -m pytest -q --disable-warnings tests_hosted/test_guest_browser.py -k delayed_auth || exit
done
# 2 passed each run (phone and desktop); all three runs passed

BANK_OF_DAD_BROWSER_TESTS=1 BOD_TEST_CHROMIUM=/usr/bin/chromium \
  .venv/bin/python -m unittest discover -s tests -q
# 59 tests, OK, including all four self-host Chromium tests

# BASE_PYTHON points to a separate disposable environment without Authlib,
# Flask-Session, Stripe or Playwright. Install the declared test requirements:
uv pip install --python "$BASE_PYTHON" -r requirements-test.txt
"$BASE_PYTHON" -m unittest discover -s tests -v
# 59 tests, OK, 4 optional browser tests skipped

git diff --check
node --check static/app.js
```

Verification caught and corrected an unintended change to ordinary authenticated login reset behavior. A repeated-browser run also caught a test synchronization error: stale-response injection could intercept recovery's own preflight; it now starts from a settled mirror document. The base-only environment initially lacked websocket-client and passed after installing the repository's declared test requirements. Remaining warnings originate from Flask-Session's deprecated UTC datetime calls.

## Boundaries

Only disposable loopback servers, synthetic provider identities and isolated bank storage were used. No deployment, push, live data, active preview or service changes. These tests exercise real Authlib discovery/code/PKCE/JWKS and the application frontend, not real Google/Stripe account acceptance.

Anonymous recovery still requires the browser token and retained server session/bank data. Cookie clearing/eviction, sign-out, device switching or server storage loss can prevent recovery. There is no new retention deadline or automated bank deletion policy; operators must provide storage capacity, ingress protection, backups and an explicitly reviewed retention/deletion policy.
