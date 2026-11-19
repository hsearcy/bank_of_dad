# Combined dashboard refresh verification

Candidate branch: `feat/bod-dashboard-refresh`, based on immutable public master
`db5f899674902c0000f08f74b355122ee0ed489a`. `git ls-remote origin refs/heads/master`
confirmed that exact public ref during integration; no deployed Worker version was queried.

Integrated, in order:
- `2dae625b9c46235fe5a974fe3d7a9d754d5a1a8c`: family-local parent labels and restored PIN.
- `8cc7a136cad4571050160ef65085f5ef9560ea1c`: explicit wallet versus total and money wording.
- `e6a2d87b5896a970ebe07772154b3f6279e3ad0c`: tour preservation, graph tools and final information.

## Integration resolutions

- Investment heading retains the configured parent name and uses money wording.
- Tour retains actual `.btn-save` / `[data-act="save"]` actions, money wording,
  configured parent dashboard name, four original graph controls and final card.
- Wallet Spend remains a labeled shortcut. The original graph Spend is preserved;
  **both** buttons receive the same real handler. Tour highlights only the graph's
  `.wallet-actions` group, not the wallet shortcut.
- Existing authorized API fixtures establish a PIN through the actual setup route
  and submit its header. Raw request helpers and dedicated missing/wrong-PIN,
  family-isolation, CSRF, Origin and logout negatives remain real and unbypassed.
- Browser fixtures use actual setup/confirmation or verification and explicit PIN
  submission. Pagehide tests reopen controls after credentials are cleared.
- Scoped duplicate Save name buttons to the child-name dialog; scoped spend tests
  to the wallet shortcut. Renamed the hosted parent-control test module to avoid
  collision with the self-hosted module during combined collection.

## Acceptance added

`cloudflare/test_tour_followup.py` now combines a new guest, first-use PIN setup,
Mom rename, reload persistence, Mom tooltip, distinct $5 wallet / $7 total,
real saving during the open tour (cancel and transport-retry included), all four
actual graph tools, final informational step, logout/revoked capability and an
unchanged unrelated family's full SQLite snapshot, at phone and desktop widths.
`cloudflare/test_parent_identity.py` also checks missing/wrong PIN on a returning
saved family, correct prior PIN and unchanged persisted settings/hash.

## Execution

All tests use disposable synthetic state and loopback providers only.
No production data, active app checkout, live Google/Stripe credentials, deployment,
push or merge was used.

Verified so far:
- JavaScript syntax checks for app and experience scripts; `git diff --check` clean.
- Hosted Flask focus: **63 passed** (`test_hosted`, `test_guest`,
  `test_hosted_realtime`, `test_legacy_upgrade`), using legitimate restored PIN auth.
- Final focused self-hosted Chromium run: **15 passed, 2 subtests passed**. This
  includes the real-save tour, wallet clarity, parent label/PIN, editable rates,
  child management and wake-lock regression coverage.
- Initial browser fixture failures were fixed (pagehide legitimately hid the
  protected panel; duplicate Save name selector); the final run above is green.
- Initial broad Flask run was interrupted after exposing old pre-migration fixtures;
  final rerun and focused browser rerun have separate logs below.
- Real local workerd run passed its first eleven tests: domain/oracle and auth/CSRF,
  rollback/restart, two-browser sync/logout, numeric boundary, idle expiry,
  parent controls/isolation/throttle, saved identity, wallet clarity, and combined tour.

Final result logs (scratch, not repository artifacts):
- `operator-local evidence: bod-dashboard-workerd.log`
- `operator-local evidence: bod-dashboard-flask-rerun.log`
- `operator-local evidence: bod-dashboard-browser-final.log`
- `operator-local evidence: bod-dashboard-hosted-focus.log`

The workerd invocation includes `test_runtime.py test_parent_controls.py
 test_parent_identity.py test_wallet_clarity.py test_tour_followup.py
 test_rate_periods_runtime.py test_kid_management.py test_oauth.py`.
Remaining real-runtime cases and broad Flask/browser totals must be read from their
completed processes; progress dots are not a claim that those full suites passed.
At the timebox handoff the workerd run showed fourteen passing tests followed by
one failure in the kid-management section; the traceback was not yet emitted.
The suite was collected before the signed-client PIN fixture migration was edited,
so rerun `test_kid_management.py test_oauth.py test_parent_identity.py` against the
final commit after examining that failure. This is an unresolved test result, not
a full-suite success. The broader Flask rerun was also still running without a
reported failure at handoff.
