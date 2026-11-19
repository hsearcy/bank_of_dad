# Footer and compact screen controls

The dashboard ends with a small About / Privacy footer. Native dialogs keep the
background inert, open at their headings, support Escape and Close, wrap keyboard
focus and return focus to the opener. They introduce no network or bank writes.
About explains the founder's financial-literacy motivation; Privacy describes
stored family data and separates hosted identity/billing from free self-hosting.

Privacy copy is grounded in the existing `db.py` bank schema,
`cloudflare_auth.py` verified issuer/subject and session storage,
`cloudflare_billing.py` Stripe references, and the existing hosted implementation.
Cloudflare hosting is mentioned only when its template context is present.
Self-hosted copies describe their own server/PIN boundary rather than implying
that they use Google, Stripe or Cloudflare. No retention, deletion, compliance or
recovery promises were added. Project contact: support@thebankofdad.app.

Dad's device section now has the wake switch and Quick tour in one row, live
status, and “While this page is visible. Resets on reload.” The oversized tinted
card, visible section title and lengthy explanation are gone. Existing wake-lock
and tour JavaScript is unchanged, including unsupported/insecure status,
acquisition guards, lifecycle release/reacquisition and reduced-motion behavior.
At 390px and 1280px the new default section measured 99.30px high; the old section
measured 308.28px and 266.69px respectively in self-hosted Chromium.

## Executed verification

All runtime/browser state was disposable, on dynamically allocated loopback
ports. No production database, provider, secrets, remote, or deployed checkout
was changed.

From the repository root (with test/hosted requirements, pytest and Playwright
installed in a local virtualenv):

```sh
BANK_OF_DAD_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
# 66 passed, including native Chromium wake lock at 390px and 1280px.
.venv/bin/python -m pytest -q tests_hosted/test_welcome_experience.py
# 10 passed, including desktop/mobile hosted welcome, tour and wake controls.
BANK_OF_DAD_BROWSER_TESTS=1 PYTHONPATH=tests .venv/bin/python -m unittest test_experience_browser -v
# Final focused rerun: 7 passed.
```

From `cloudflare/`, after `uv sync --frozen`:

```sh
uv run pytest -q -s test_footer.py
# 2 passed: real workerd + Chromium at 390px and 1280px.
```

The Worker checks exercise initial scroll/heading focus, full Tab and Shift+Tab
wrapping, Escape, Close, focus restoration, mode-correct copy, mail link,
responsive bounds, compact controls, quick-tour replay, zero page errors and an
unchanged complete family SQLite dump. Optional `BOD_TEST_ARTIFACTS` saves dashboard,
About, Privacy and Dad screenshots; both desktop and mobile were visually reviewed.

The existing self-hosted wake test exposed a reload-readiness race (its predicate
dereferenced a not-yet-rendered balance). Its two waits now use optional chaining;
the same exact expected balance is retained. Existing SQLite ResourceWarnings and
Flask-Session datetime deprecation warnings remain non-failing. Headless wake-lock
acquisition does not establish physical device display behavior.
