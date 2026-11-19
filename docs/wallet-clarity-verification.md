# Wallet clarity verification

Based on public `origin/master` at `db5f899674902c0000f08f74b355122ee0ed489a`.
No deployment, production data, provider calls, schema or financial computation changes.

## UX

- The top-right green **All my money** figure is still `owned_cents`, at 40px.
- A blue **Wallet / Available to spend** panel above the graph shows only
  `wallet_cents`, at 30px. Savings and investments are explicitly separate.
- Move the existing Spend control into that panel, give it a visible text label,
  a child-specific accessible name, a 44px minimum target and keyboard focus style.
  There is still exactly one Spend control per child.
- Remove the secondary invented coin count and coin wording in controls, empty
  states, dialogs, tour copy and server errors displayed by those controls.
- Preserve existing graph, actions, reward sound/confetti, total-number spin,
  HTTP APIs, render/invalidation/reconnect behavior and money calculations.
- Phone layout wraps long names without horizontal overflow.

## Local verification

All runtime state was synthetic and disposable. Real Chromium screenshots were
inspected at 390px and 1440px widths for both self-hosted Flask and local workerd.
The test fixture uses real mutations to create wallet **$9.34**, savings **$1.00**,
investments **$2.00**, total **$12.34**, not fabricated API responses.

Commands (from the repository root unless noted):

```sh
python -m unittest discover -s tests -v
# 77 tests, OK, 16 opt-in browser tests skipped

BANK_OF_DAD_BROWSER_TESTS=1 python -m unittest discover -s tests -p 'test_wallet_clarity_browser.py' -v
# 2 passed
BANK_OF_DAD_BROWSER_TESTS=1 python -m unittest discover -s tests -p 'test_realtime_browser.py' -v
# 4 passed: includes graph refresh, polling fallback, real controls/animations, reconnect
BANK_OF_DAD_BROWSER_TESTS=1 python -m unittest discover -s tests -p 'test_experience_browser.py' -v
# 7 passed: includes tour target/rebinding, empty-bank fallback, wake lock and footer

cd cloudflare
uv run pytest -q test_wallet_clarity.py test_runtime.py
# 7 passed: wallet UI, independent SQLite oracle, CSRF/isolation, rollback/restart,
# unsafe-number protection, two-browser push/reconnect and idle-expiry authorization
```

Also passed `node --check` on both changed JS files and `git diff --check`.

The shared wallet UI regression covers save/spend/withdraw, two independent browser
processes with 30-second polling disabled, range/render changes, remote rename,
missed-write/replacement-WebSocket resync, pending allowance excluded until
collection, reward animation, negative requests, the existing capped-overspend
behavior, zero wallet with nonzero owned balance, last-child removal and no
phantom wallet. A held real initial response verifies loading does not display an
invented zero balance. Assertions cover accessible names, touch-target height,
font hierarchy and horizontal overflow.

Set `BOD_WALLET_SCREENSHOTS` to an output directory to regenerate:
`self-hosted-phone.png`, `self-hosted-desktop.png`, `workerd-phone.png`,
`workerd-desktop.png`.

## Existing self-hosted disconnect diagnostic

Ordinary wallet flows are console- and page-error-clean. The local workerd wallet
flow, including deliberate reconnect, is console- and page-error-clean.

The Werkzeug/simple-websocket fixture emits one Chromium console diagnostic,
`WebSocket ... failed: Invalid frame header`, when the test deliberately calls
`WebSocket.close(1000)`. This was reproduced with the unmodified `origin/master`
frontend (clean before the forced close; diagnostic immediately afterward).
Reconnect and exact balances still pass. The self-hosted regression explicitly
records and allows only that precise diagnostic during the forced-close window;
it does not filter ordinary UI errors. This pre-existing transport issue is not
fixed or claimed clean by this UI-only change.

## Tour integration

`static/experience.js` changes only three copy lines (`Save coins` → `Save money`,
`Invest coins` → `Invest money`). No target selection, step order, event or layout
logic changes. Preserve the other tour branch's semantic fixes when combining;
apply these copy replacements to its final text rather than replacing its file.
