# Owner panel — local verification

Implemented as a hosted-only extension to the existing application, based on
`68f908cb2e569b39918293cfd2233f21f2424e50`. No deployment or provider-account
configuration is part of this change. Enablement is documented in
[HOSTED.md](../HOSTED.md#private-owner-panel).

## Executed checks

Using an isolated virtualenv with `requirements-hosted.txt`,
`requirements-test.txt`, pytest and Playwright installed:

```sh
BOD_TEST_CHROMIUM=/usr/bin/chromium python -m pytest tests_hosted -q
# 111 passed (includes actual Chromium owner flows at 390px and 1280px)
```

Using a separate base-only virtualenv with `requirements-test.txt`, without
Authlib, Stripe, Flask-Session or Flask-SQLAlchemy:

```sh
BANK_OF_DAD_MODE=self-hosted BANK_OF_DAD_DB=/absolute/disposable/free.sqlite \
  python -m unittest discover -s tests -v
# Ran 65 tests: OK (10 opt-in browser tests skipped)
```

Both commands used disposable SQLite files and scratch storage. Hosted tests
exercise the real Authlib code/PKCE/JWKS flow and Stripe SDK against local,
explicitly synthetic provider endpoints. They do not contact Google or Stripe
accounts. Flask-Session emits upstream `datetime.utcnow()` deprecation warnings;
there were no failing tests.

## Covered owner behavior

- Signed synthetic Google login, including the documented issuer alias, enters
  the actual page. Saved parent and device-only family existence is visible.
- Empty/unset allowlist disables the surface. Invalid config is rejected. Exact
  issuer/subject matches are required; client roles and email claims confer no
  authority. An expired owner family can still administer access.
- Anonymous visitors, normal parents, guests, forged writes and owner API probes
  cannot read the list or change access. Logout revokes a replayed owner cookie.
- CSRF and exact Origin are both required for actions; missing, foreign and null
  Origin, missing/incorrect/non-ASCII tokens are denied without directory changes.
- Full-ID/prefix search, 50-family pagination, invalid search input and paid/trial
  expiry boundaries are checked. Reads leave the directory snapshot unchanged.
- Browser grant/revoke reloads the exact family after commit and reads back the
  flag. Revocation requires confirmation. Audit rows contain the action, family,
  required reason, verified owner identity and timestamp.
- Expired target access restores on grant and stops on revoke. Paid access,
  trial dates and bank bytes are preserved. Reconciliation cannot erase a grant.
  Owner reads/actions make no Stripe calls and never cancel subscriptions.
- Audit failure rolls back the entire directory transaction. The nullable
  reconciliation-date migration is additive, rollback-safe and idempotent.
- Actual mobile/desktop renders were inspected; no horizontal overflow or browser
  page errors were observed. Set `BOD_TEST_ARTIFACTS` to retain screenshots.
- Free self-hosting does not register the routes, parse owner configuration or
  require optional hosted dependencies.

These checks are local implementation evidence, not a real Google login, live
Stripe acceptance test, deployed proxy verification or production security audit.
No real family data, service files or credentials were used or changed.
