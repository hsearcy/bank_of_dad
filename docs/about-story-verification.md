# About story and tour link

The existing About dialog now includes “How I use it”: the author's optional
allowance approach, the stuffed-animal motivation, and conversations with his
early elementary school kids about saving and repeated doubling. A 100% increase
doubles the amount; the high example rates are explicitly teaching examples,
not expected investment returns.

The final tour screen opens About at that heading. The tour becomes hidden and
inert while About is modal, then resumes at the same final step and returns
keyboard focus to the link. Normal footer opening still starts at About's top.
No additional footer or welcome control was added.

## Local verification

Run the self-hosted Chromium tests with the test dependencies and Playwright
installed:

```sh
BANK_OF_DAD_BROWSER_TESTS=1 python -m pytest -q tests/test_experience_browser.py
```

For the hosted Worker, install the locked dependencies first, then:

```sh
cd cloudflare
uv sync --locked
npm ci --ignore-scripts
uv run pytest -q test_about_story.py test_tour_followup.py test_welcome_polish.py
```

The new shared browser assertions run at 390px and 1280px, with a short 667px
viewport. They cover section focus and scrolling, Close and Escape, Tab and
Shift+Tab wrapping, one modal at a time, tour suspension/resumption, focus
surviving background rerenders, normal footer focus restoration, no horizontal
overflow, and no JavaScript errors. Complete disposable SQLite dumps are compared
before and after the About/tour flow. The self-hosted test also checks a renamed
“Mom” parent label without changing the author's first-person story.

The existing actual-save tour and welcome regression suites pass on local
workerd. No real family data or sign-in/payment providers are used.

An additional run of `tests/test_frontend.py` exposes an existing unrelated
failure: `WalletClarityFrontendTests.test_total_and_wallet_keep_separate_sources`
expects two Spend buttons after the compact-wallet update removed the duplicate.
The identical failure was reproduced with both the test and app JavaScript read
from the unchanged base commit. It is intentionally not repaired in this copy
change.

This verification is local only; it does not publish or deploy the feature.
