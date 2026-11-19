# Hosted trial policy upgrade

Both hosted runtimes use `31 * 24 * 60 * 60` seconds, not calendar months.
New device banks and directly created saved families persist the original
`trial_started` once and `trial_until = trial_started + 31 days`. Claiming,
logging in, refresh and restart never restart this clock. No card, customer,
Checkout or automatic charge is created by this policy. Self-hosted mode remains
free and does not consult hosted entitlement.

## Existing records

Neither existing family schema has a `created_at` field suitable for inference.
Both do persist `trial_started` and `trial_until`; use those, never a visit time,
ledger date, session creation time or identity claim time.

An additive `trial_policy INTEGER NOT NULL DEFAULT 0` marks old records. New
families explicitly insert policy 1. On a family read, one atomic conditional
UPDATE processes policy 0:

- A valid positive start and expiry **exactly** equal to start + seven days
  becomes start + 31 days. This also applies to claimed, currently expired,
  paid and operator-free families, without modifying payment/grant fields.
- Older-than-31-day families remain expired unless independently paid or free.
- Zero/invalid starts, zero expiries, shortened trials and custom durations
  remain unchanged, including all grants longer than the new default.
- Every considered record becomes policy 1, even when no date changes. Later
  custom edits cannot retrigger the upgrade. Unknown policy values are untouched.

The existing schema cannot distinguish a manually assigned **exact seven-day**
trial from the former default. Both receive the longer total duration; no access
is shortened. Other custom durations are deliberately not inferred or extended.
There is no inference when the stored start is missing or invalid.

Flask installs the additive column under the existing startup writer lock and
upgrades individual records lazily on retained-session HTTP, login/claim, owner
projection and idle socket authorization reads. The conditional UPDATE computes
from current database values and commits its date and marker together; concurrent
connections cannot add repeated extensions. No family bank files are scanned.

Cloudflare upgrades the family-local schema and row inside `transactionSync` via
the shared family entitlement projection, covering HTTP, billing/owner reads,
WebSocket handshakes, publishing and alarms. An old alarm may wake early; it
re-reads the upgraded entitlement and rearms for its real remaining deadline.
Session expiry remains a separate authorization boundary (saved sessions are
still seven days); extending a trial never renews an expired/revoked session.

Pre-trial **Flask schemas without either trial date column** retain the existing
explicit one-time migration-time grace policy, now 31 days rather than seven.
That is not an inferred signup date. Additive DDL, grace dates and marker install
commit/rollback together; paid dates and bank files remain intact. Cloudflare
unversioned banks still fail closed and require explicit migration.

## Verification

- Native SQLite tests: exact legacy match, expired/current/paid/free/custom and
  invalid records, no false-positive tolerance match, durable marker, rollback,
  eight concurrent connections, retained guest claim and no billing creation.
- Hosted regression suites: guest retention, trial/operator HTTP and real socket
  expiry, owner projection, pre-trial schema rollback and retained legacy banks.
- Genuine local workerd: fresh 31-day dates; multiple genuinely old schemas
  (column removed while stopped); concurrent first access; expired/current/
  paid/free/custom/zero-date cases; repeated requests and graceful restart;
  independent family data; real idle expiry alarm; synthetic signed OAuth claim,
  expired guest claim and signed-session idle expiry/logout isolation.

All fixtures are disposable. No production state or real identity/Stripe account
is used. Synthetic provider tests do not establish real-provider acceptance.
