# Editable savings and investment periods

Dad controls → **Rates** now edits a percentage and a whole-day duration for
savings and each investment offer. Periods accept **1–3650 days**; percentages
must be finite, numeric and nonnegative (zero is supported). The editor uses
accessible number fields, browser validation and an inline save/error status.

## Financial behavior

- Defaults remain 4% per 7 days, +25% after 30 days, and +100% after 90 days.
  A day is 86,400 seconds; existing month labels mean fixed 30-day units, not
  calendar months. Investment identifiers `1mo` / `3mo` remain stable even if
  their durations change.
- Opening Rates writes nothing. Saving unchanged values writes no settings,
  balances, accrual anchors, transactions or history. Config responses no longer
  round the stored savings percentage to two decimal places, preventing an
  unchanged UI save from truncating a previously configured rate.
- Savings still earn simple, continuous interest on the saved principal and use
  the existing nearest-cent rounding when collected or included in a savings
  transfer. Editing the percentage **or** period preserves unrounded interest
  accrued under the previous terms, including fractional cents, in
  `kids.savings_interest_carry`. Only subsequent elapsed time uses the new terms.
  **There is no automatic collection, capitalization, compounding, balance change
  or invented ledger/history entry when terms change.** Repeated edits preserve
  that carry; changing an investment offer alone does not touch savings accrual.
- For example, $100 at 4% per 7 days earns $2 over 3.5 days. Changing to 4% per
  day retains that $2 pending, without adding it to principal. One additional
  day earns another $4, not $4.08. Collection follows the existing rounding rule.
  A future accrual anchor (e.g. after moving the demo clock backwards) never
  moves backwards during a term change, interest collection or savings transfer.
  Consuming carry while the clock is behind its anchor does not reopen that
  already-earned interval; new accrual resumes only beyond the retained anchor.
- Existing investments keep their stored `buy_ts`, `mature_ts`, principal,
  payout and collected status. Their displayed term comes from those contract
  timestamps, not today's offer. Only newly purchased investments use the new
  duration/return. Purchases still store a fixed maturity and payout.
- Savings badges, live growth, savings explanations, chart/what-if projections
  and new-investment previews use the chosen period. Forecasts remain estimates
  assuming periodic collection/reinvestment; actual savings do not compound
  automatically. Existing-contract chart reinvestment retains its original
  same-contract assumption.

## Storage, API and safety

The shared additive migration adds `savings_interest_carry REAL NOT NULL DEFAULT
0`; it does not reset balances, timestamps or existing investments. Self-hosted
SQLite initializes/migrates as before. Cloudflare now invokes the same additive
migration inside the existing family `transactionSync`, including for already
persisted banks. Flask hosted banks migrate transactionally on an authorized
request's first database open, including retained guest and saved-parent sessions;
no re-login is needed. Startup does not scan or rewrite family files. Current
schemas need only metadata checks, with no writes. Hosted request opens require
an existing family file and never initialize or reseed one. No remote migration
was run.

`POST /api/admin/rates` accepts partial updates:

```json
{
  "savings_weekly_pct": 4,
  "savings_period_days": 14,
  "returns": {"1mo": 25, "3mo": 100},
  "bond_period_days": {"1mo": 45, "3mo": 120}
}
```

The historical `savings_weekly_pct` key is retained for compatibility; pair it
with `savings_period_days` (default 7) to interpret the percentage. State config
returns both fields and each offer's effective `days` / `label` / `return_pct`.
Legacy rate-only requests remain valid. Invalid periods, percentages, maps or
unknown investment keys reject the entire update, rather than clamping inputs.

Dad PIN authorization on self-hosting and family capability/Origin/CSRF checks
on hosted targets are unchanged. SQLite HTTP requests now acquire their existing
hosted-style `BEGIN IMMEDIATE` lock on the free self-hosted target too: a concurrent
collection cannot read an old accrual anchor and then a new savings rate.
The domain helper respects caller-owned native transactions; the HTTP owner
explicitly commits. Workers retain the real outer family-local transaction,
no-op inner commit, and post-commit WebSocket invalidation path.

## Local verification

All data used below is synthetic and disposable; no real family database,
provider, Stripe account or remote deployment is accessed.

```sh
# From repository root, after installing requirements-test.txt into a test env:
BANK_OF_DAD_BROWSER_TESTS=1 cloudflare/.venv/bin/python -m pytest -q tests
# 111 passed, 32 subtests passed; one existing TestSocket collection warning.

cd cloudflare
.venv/bin/python -m pytest -q test_rate_periods_runtime.py test_runtime.py test_harness.py
# 10 passed, including actual local workerd, Durable Object SQLite and Chromium.
```

Coverage includes default and high-precision unchanged saves, additive migration
of existing persisted bank rows, zero/negative/nonfinite/fractional/out-of-range
inputs, 1/3650-day boundaries, round trips, fractional accrual continuity,
collect/save/withdraw carry consumption, no double collection, old/new investment
maturity and payouts, future anchors, native writer contention, request-owned
commits, real SQL-fault rollback and restart persistence, Dad authorization,
family isolation and Origin/CSRF denial. Browser tests use the actual template
and controls, 390px phone layout, a 320px-wide Rates editor, two same-family
clients and a separate family with polling disabled to prove WebSocket delivery.

The pre-existing dashboard grid itself overflows a 320px viewport; the Rates
editor does not. This change does not redesign that unrelated dashboard layout.

No push, merge or deployment is part of this change. Integration must preserve
the carry read/reset on collection and savings transfers, shared migration on
both storage targets, explicit request commit, and contract-derived labels.
