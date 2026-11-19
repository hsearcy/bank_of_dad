# Tour follow-up verification

## Cause and behavior

The old `experience.js` document click listener explicitly called `closeTour(false)` for **every** `#kids button` (and the parent button). Clicking the genuine `.btn-save` opened its normal picker, then the bubbling click immediately closed the tour. It was not a form POST or a detached-target failure.

The regression was run before the fix using real Chromium and a disposable Flask bank. It failed immediately after clicking `.btn-save`, because `#productTour.hidden` was true (`1 failed, 7 deselected`). The fix removes that blanket exit. While any actual panel or native dialog is open, only the guide is suspended (inert, hidden from assistive technology, visually hidden); its step remains active. After the last panel closes, the guide resumes at that step and rebinds its target. Dashboard rerenders do not clear forms or advance the tour. No clicks are intercepted and no simulated success is presented.

Added one graph-tools step anchored to the existing `.wallet-actions` group, with meaningful accessible names on the icon buttons. The final nonmodal, skippable information step explains the tracker/bank distinction, founder’s own use, optional shared tablet, current manual interest claiming, and intentionally high adjustable rates. Empty banks offer the parent-dashboard step followed by the information step. New copy avoids hardcoded parent names.

## Executed acceptance

- Shared Chromium flow at **390×667 and 1280×667**: actual savings picker, background refresh without losing entered amount, cancel/resume at the same step, injected transport failure, real successful `$1` save, highlight rebinding, all four real graph tools opened/closed, reduced-motion preference changes, skip/reopen, final information and Done.
- Real local workerd: `cloudflare/.venv/bin/python -m pytest -q cloudflare/test_tour_followup.py` (run from `cloudflare/` as `.venv/bin/python -m pytest -q test_tour_followup.py`): **1 passed** in 54.69s. A fresh synthetic family per viewport; exact wallet/savings/ownership and one additional save transaction verified. Saving legitimately capitalizes the sample’s already-accrued 1¢. A separate family’s full SQLite dump remains unchanged.
- Focused self-host regression: `BANK_OF_DAD_BROWSER_TESTS=1 cloudflare/.venv/bin/python -m pytest -q tests/test_experience_browser.py -k real_save`: **1 passed, 7 deselected**, including the final screenshot additions.
- Full self-host suite with Chromium enabled: `BANK_OF_DAD_BROWSER_TESTS=1 cloudflare/.venv/bin/python -m pytest -q tests`: **164 passed, 34 subtests passed**, one existing `TestSocket` collection warning, in 216.01s. Includes full-bank unchanged assertions for tour-only exploration, interest fallback, and empty-bank behavior.
- Optional Flask-hosted welcome suite: `cloudflare/.venv/bin/python -m pytest -q tests_hosted/test_welcome_experience.py`: **10 passed**, eight existing Flask-Session UTC deprecation warnings.

Transport failure is deliberately injected with Playwright `route.abort`, not a fabricated API result. The existing action handler rejects its fetch; the test checks that the entered amount and tour survive and the retry reaches the real backend. This patch does not redesign the app’s existing action error UI.

Screenshots were inspected at both requested viewport sizes; the 390px guide, four targets, and information buttons fit without horizontal overflow. Test artifacts (outside the repository): `bod-tour-artifacts/tour-{controls,info,save-panel}-{390,1280}.png` under the scratch directory. Logs: `bod-tour-{selfhost,workerd,hosted}.log` there.

## Integration boundaries

No backend, schema, real account, production data, remote provider, deployment or other worktree was changed. Local tests use disposable synthetic banks. The selectors `.btn-save` and `[data-act="save"]` are unchanged, allowing the wallet-copy branch to rename visible button text. Reconcile the small `kidCard` accessible-label patch if that branch also edits the graph controls. The interest-option branch must update the final manual-claim sentence to reflect its actual enabled/default behavior; this branch truthfully describes current master and does not claim that an automatic option already exists.
