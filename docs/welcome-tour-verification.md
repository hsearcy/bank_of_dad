# Welcome, quick tour and screen-awake verification

## Scope

Built from `480868ecc6952733a9b07528af9a193acfa4b000` in an isolated worktree. No deployment, push, billing-policy changes or real-family writes. All test banks are disposable SQLite fixtures; identity and billing tests use synthetic loopback providers. The existing preview checkout was left clean at its original commit; neither the live family service nor the running preview was exercised or restarted.

## Behavior

- Welcome retains the seven-day limit, no automatic charge, optional save/sync, device-loss warning, configured free-access contact and existing free self-hosting GitHub link. Adds the founder's reason for making the app, friendlier subscription wording and an optional-tablet note.
- After welcome, a small nonmodal offer starts a four-pointer tour of the actual Dad, interest collection, saving and investing controls. Skip or Escape closes it; Dad offers replay. It never clicks a control or changes bank data. Highlight descriptions are restored on close and rebound after real state refreshes. Instant scrolling and reduced-motion styling avoid tour animation. Space is reserved so the guide does not cover its target.
- Only a newly provisioned anonymous sample is aged: its creation, savings accrual, transaction/history and both bond timestamps move together. At the default 4% weekly savings rate, its $1 deposit has accrued one cent. Rates and bank clock are unchanged; allowance starts today. Existing family databases are never reseeded. Zero/very low custom rates are respected; age is capped at six days rather than manufacturing a reward or mature investment.
- Dad's **Keep screen awake** is available in both modes, per-page and off by default/reload. The switch records intent separately from actual granted/paused/released/declined status. Hiding or leaving releases the lock; returning can reacquire for the continuing opt-in. Pending requests are generation-guarded, stale grants released, and denial/release do not cause retry loops. No unrelated permission request or browser workaround is used. Unsupported browsers and insecure contexts explain why the control is unavailable.

## Executed checks

Installed requirements-hosted.txt, requirements-test.txt, pytest and playwright in a task-owned scratch virtualenv. Used actual `/usr/bin/chromium`, with test servers on automatically selected loopback ports, not either existing application port. Temporary directories use the scratch TMPDIR. Equivalent commands with that virtualenv's Python:

```sh
BANK_OF_DAD_MODE=self-hosted BANK_OF_DAD_BROWSER_TESTS=1 BOD_TEST_CHROMIUM=/usr/bin/chromium \
  python -m pytest tests -q
# 62 passed; 32 subtests passed

BOD_TEST_CHROMIUM=/usr/bin/chromium \
  python -m pytest tests_hosted/test_welcome_experience.py tests_hosted/test_guest_browser.py -q -s
# 15 passed

python -m pytest tests_hosted \
  --ignore=tests_hosted/test_browser.py \
  --ignore=tests_hosted/test_guest_browser.py \
  --ignore=tests_hosted/test_hosted_realtime_browser.py \
  --ignore=tests_hosted/test_trial_browser.py \
  --ignore=tests_hosted/test_welcome_experience.py -q
# 71 passed

BOD_TEST_CHROMIUM=/usr/bin/chromium python -m pytest tests_hosted/test_browser.py -q
# 1 passed
BOD_TEST_CHROMIUM=/usr/bin/chromium python -m pytest tests_hosted/test_trial_browser.py -q
# 3 passed
BOD_TEST_CHROMIUM=/usr/bin/chromium python -m pytest tests_hosted/test_hosted_realtime_browser.py -q
# 1 passed

node --check static/experience.js
node --check static/app.js
node --check static/sw.js
git diff --check
# All clean
```

Every final JUnit report has zero failures, errors or skips (the self-host report includes its 32 subtests). The explicitly forbidden default test database remained absent.

### Direct acceptance evidence

- Actual hosted welcome/tour at 390×844 and 1280×844, reduced motion, keyboard Next/Done and Escape, skip-offer/reload, no horizontal overflow, and target bounds above the guide at every step. Real API refreshes rebuild the cards during the tour and retain the highlight. Saved screenshots were visually inspected.
- The actual interest button changes owned money from `$7.00` to `$7.01`; after reload, savings is `$1.01` and no interest button remains. SQLite confirms exactly one one-cent interest transaction and a 701-cent history total. A separate existing family's full bank-table snapshot is unchanged, and repeated loading never reseeds the sample.
- Controlled Wake Lock API inside real Chromium/UI covers delayed grants after off, off/on during an outstanding request, hide/show during acquisition, pagehide/pageshow, explicit release, platform release, denial without retries, unsupported/insecure conditions, and reload default-off. Hosted tests use keyboard Space on the accessible switch and verify the lock survives closing Dad without a bank-data mutation.
- Unmodified native Chromium Screen Wake Lock requests, in both hosted HTTPS and self-hosted localhost contexts, reported `Active — keeping this visible screen awake.` at both phone and desktop viewport sizes. Turning off returned `Off`.
- Existing signup/bank-claim, no-card access, expiry, billing fixtures, isolation, WebSocket sync, self-hosted PIN/money controls and stored-HTML injection tests pass. One old browser expectation was adjusted from `$8.00` to `$8.01`: the existing save operation legitimately capitalizes the newly accrued sample cent.

## Focused welcome/tour corrections

Follow-up from `94798b6410df27189d706ecf7ef4dedf631140c0`, still local-only with disposable banks and no live/preview changes:

- At 390×667, Chromium originally opened the welcome with `scrollTop=318`, hiding its heading. Autofocus now belongs to the heading (`tabindex="-1"`), not the lower CTA. The regression verifies `scrollTop=0`, visible heading and icon, heading focus, Tab into the dialog's links, and keyboard activation of the CTA.
- A two-child bank with interest ready only for the second child previously highlighted the first child's Save button. Interest lookup now explicitly precedes the Save fallback; copy follows the actual target and is refreshed along with the highlight. Regressions cover both the second-child reward and the no-interest Save explanation at 390×667, with unchanged full SQLite snapshots.
- A genuinely empty self-hosted bank now offers only its available Dad step (`1 of 1`, then Done), explains how to add the first kid and replay, and never offers missing saving/investing controls. Chromium verifies replay, real refresh, keyboard completion, working Dad/add-child controls, and an unchanged full SQLite snapshot. The shared server fixture can opt out of creating its usual synthetic child.

New regression assertions failed against the original behavior before the fixes. Final commands used `/home/ares/.hermes/cache/scratch/bod-welcome-venv/bin/python` and actual `/usr/bin/chromium`:

```sh
BANK_OF_DAD_MODE=self-hosted BANK_OF_DAD_BROWSER_TESTS=1 python -m pytest tests/test_experience_browser.py -q -s
# 6 passed; native wake lock Active at 390px and 1280px
python -m pytest tests_hosted/test_welcome_experience.py -q
# 10 passed
python -m pytest tests_hosted/test_guest_browser.py -q
# 6 passed
BANK_OF_DAD_MODE=self-hosted python -m pytest tests/test_realtime.py tests/test_html_injection.py -q
# 5 passed, 17 subtests passed
node --check static/experience.js
node --check static/app.js
node --check static/sw.js
git diff --check
# All clean
```

Only existing Flask-Session datetime deprecations and the TestSocket collection warning appeared in these successful runs. The initial failing hosted regression also logged a TLS/WebSocket teardown BrokenPipe/EOF; it did not recur as a test failure in the successful focused runs. No push or deployment was performed.

## Limits and encountered issues

- Native API success in headless Chromium does not prove a physical tablet will keep its display on, behavior when its screen is locked, other browser implementations, or battery/policy behavior. The UI explicitly makes no such promise. Visibility/lifecycle race coverage uses controlled browser events; it is not a physical background/lock-screen test.
- A monolithic hosted-suite attempt aborted inside Python 3.14's threaded SSL/WebSocket fixture activity. All hosted groups were then successfully exercised in separate processes as listed above. This is not presented as a successful monolithic run. Existing Flask-Session datetime deprecation warnings and the existing TestSocket collection warning remain.
- Tests do not use real Google or Stripe accounts. No live service, runtime credentials or preview process was changed.
