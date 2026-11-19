# Automatic interest + dashboard integration verification

Branch: `feat/bod-dashboard-auto-integration` in a new isolated worktree.
Base: `0d8e12237f2bc6b0c9848986d8ca29817601c6b6`.
Automatic-interest input: `be57f945be76c6f3981c5ffaa1f604c54fd6f807`, cherry-picked as `7843857`.
Application source was frozen at `0c359a660e5bebe01d12afd989d52945f70c521d` throughout acceptance. `384c678ddc0d320d0e1634d8326113d45d291cc4` only updates test fixtures/comments; it does not change that application source.

## Integration decisions

- The two textual conflicts were in `bank.py`: retain the atomic gift wrapper **and** the configured-parent default gift note; retain mandatory, non-removable, ASCII-digit PIN protection and durable authorization throttling, not the automatic branch's older removable-PIN behavior.
- Clean merges in `app.py`, `static/app.js` and styles retain all dashboard work. Automatic rates use the existing `/api/admin/rates` PIN gate in addition to hosted identity/capability, exact Origin and CSRF. Ordinary child saving never requires the parent PIN.
- The tour's interest step and final information reflect the actual family mode and current parent label. Both modes explain manual collection is the default and automatic collection is optional. Automatic mode describes initial pending credit followed by configured-period collection; real child saving remains usable during the tour, without an extra automatic interest claim.
- No template, Wallet/Total, parent-label or tour-panel functionality was dropped.

## Confirmed execution

All state is disposable and synthetic. Real local workerd/SQLite and real Chromium were used. Identity and payment fixtures use loopback HTTP and signed synthetic identities; this is not real Google/Stripe acceptance. No deployment, push, merge, live credentials or real bank/payment data was used.

- `python -m pytest -q tests tests_hosted`: **322 passed, 17 skipped, 32 subtests passed**, exit 0. Includes retained native hosted HTTPS browser onboarding, subscription fixture, actual child management/saving and sign-out. Existing collection/deprecation warnings remain.
- `cloudflare/test_tour_followup.py`: final rerun **2 passed**, exit 0; each mode runs at 390 and 1280 widths. Actual first-use PIN setup, Mom rename/reload, exact $5 Wallet/$7 Total, automatic pending credit when chosen, real tour save/cancel/transport retry, panel input preservation, all four actual graph tools, mode-aware final information, configured-period credit, reload, logout and foreign-family full snapshot isolation are covered.
- `cloudflare/test_wallet_clarity.py test_parent_controls.py test_rate_periods_runtime.py`: **6 passed**, exit 0; exact money labels, parent setup/throttle/restart/push/isolation and rate-period regressions.
- `cloudflare/test_auto_interest_authorization.py::test_auto_interest_does_not_postpone_signed_session_expiry`: **1 passed**, exit 0. The real idle durable alarm first credits $100 savings to $104 once, then closes the signed session with code 1008; HTTP reads and settings writes subsequently return 401. The unrelated family's full snapshot is unchanged.
- `cloudflare/test_auto_interest_authorization.py -k setting_requires`: **1 passed, 1 deselected**, exit 0. Valid-CSRF requests without an established PIN, missing/wrong PIN, missing CSRF, foreign Origin and a different family cannot enable automatic interest; authorized enabling and PIN-free child saving work.
- Hosted closure/browser focus: **20 passed**, exit 0 (`test_trial_access.py test_hosted_realtime.py test_browser.py`).
- JavaScript syntax checks for `static/app.js` and `static/experience.js`; `git diff --check` clean.

The broader workerd command is `test_auto_interest_runtime.py test_tour_followup.py test_kid_management.py test_oauth.py test_parent_identity.py test_runtime.py`. Its completed result is **40 passed, 2 failed**, exit 1: both failures were the tour fixtures collected before their final corrections. This must not be represented as one green broad run. The exact corrected tour rerun above is **2 passed**, exit 0, against unchanged application source. Every other case passed, including all five previously reported kid-management/OAuth failures, parent identity, independent SQL oracle, rollback/restart, two-browser sync/reconnect/logout, unsafe-number rollback and real idle trial-expiry alarm. There are no remaining failing cases in the final focused verification, but the broad command was not rerun wholesale after the two fixture corrections.

## Failures diagnosed rather than hidden

1. Initial workerd startup exhausted its harness deadline while npx downloaded missing local Wrangler dependencies; workerd had not started. Linking the existing pinned local dependency installation made the real runtime path work. This was dependency preparation, not an application fix.
2. The first combined tour assertion expected only a save ledger row in manual mode. Automatic-interest code now separately audits the genuinely capitalized pending interest. The fixture now asserts both exact rows/amounts and exact balances, not a weakened count.
3. Automatic-tour reload briefly starts with `STATE.config === null`. The fixture's polling predicate now tolerates that initial state using optional chaining; application behavior was already correct.
4. Hosted trial/socket revocation initially reproduced the reported queued `state_changed` failure. The authorized helper now performs a legitimate first-use PIN write as well as child creation, so two notifications can precede Close. The corrected fixture accepts only exact content-free invalidations while independently requiring actual socket closure within one fixed three-second monotonic deadline; HTTP 402 denial, foreign-family continuity and later grant/revoke are still asserted. Focus and broad native reruns pass.
5. The hosted browser `#newKidName` hidden report was a stale PIN fixture: a bare Dad click opens the PIN dialog, not protected controls. The integrated real setup/confirmation/verification helper passes the complete native browser flow. No authorization bypass or product hiding change was needed.

## Logs

Scratch evidence, outside the repository:
- `operator-local evidence: bod-auto-integrated-full.log`
- `operator-local evidence: bod-auto-integrated-workerd-final.log`
- `operator-local evidence: bod-auto-integrated-tour-verified.log`
- `operator-local evidence: bod-auto-integrated-controls.log`
- `operator-local evidence: bod-auto-integrated-session-alarm.log`
- `operator-local evidence: bod-auto-integrated-auto-pin.log`
- `operator-local evidence: bod-auto-integrated-hosted-final.log`

Initial failing logs are retained under the same scratch prefix (`flask`, `workerd`, `tour-debug`, `tour-final`); they are not the final acceptance results.
