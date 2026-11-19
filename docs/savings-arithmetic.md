# Savings arithmetic

Savings interest is calculated with arbitrary-precision rational arithmetic and
rounded **half to even** only when claiming integer cents. Automatic claims retain
the complete signed remainder; the historical manual-claim/deposit/withdrawal
policy still clears the remainder when it collects interest.

## Input meanings

- Principal is integer cents (automatic principal/credit remains bounded to the
  existing JavaScript-safe integer range).
- A saved percentage means the exact finite decimal spelling in settings. The
  numeric API first parses a JSON number to binary64, then saves its shortest
  round-trip decimal spelling, as before. Precision already lost in JSON parsing
  is not recoverable. We do not further quantize percentages or apply an epsilon
  near half-cent ties.
- A configured period is a whole number of days, each 86,400 seconds.
- Timestamps mean their actual persisted binary64 values. Subtraction and accrual
  use their exact rational values, including fractional seconds; no rounding to
  seconds/milliseconds is added. The existing clock itself still has binary64
  resolution.

## Persistence and existing families

`savings_interest_exact` is one nullable, additive TEXT column. It stores the
rational carry and its accrual-anchor fingerprint. The existing REAL carry stays
as a compatibility/debugging projection. The rational string crosses the Worker
SQL/JavaScript bridge without numeric conversion. All financial writes update
both representations inside the existing transaction.

Migration only adds the column; it does not rewrite balances, anchors, rates,
ledger/history, or old carry. A NULL exact carry imports the **exact binary value**
of the legacy REAL, including negative carry. We cannot infer which portion of
an old stored carry was legitimate earnings versus previous floating-point
error; we preserve it, rather than silently forgiving debt, deleting earnings,
replaying elapsed time, or retroactively adjusting ledger credits.

The stored rational is used only while its anchor and REAL projection agree
with the row. Legacy/direct-SQL edits to those values therefore invalidate stale
exact metadata. Running an older version is not a supported lossless downgrade:
it cannot maintain rational remainders. No fixed decimal precision budget or
near-tie tolerance is used for new accrual.

Regression coverage includes the independently reproduced 25-cent, 1%-daily,
20-period case (30 cents principal, +1/2 cent retained, five one-cent credits),
a 120-case independent Fraction sweep, both adjacent representable rates around
a half-cent boundary, fractional-second balance/term changes, legacy signed REAL
carry, restart, rollback, and actual local workerd SQLite.
