# Hosted storage foundation — not deployable

This is internal storage code, not a hosted product. For the separate internal
identity and membership directory foundation, its trust contract and unresolved
integration gaps, see [IDENTITY.md](IDENTITY.md). The separate experimental
co-located authorization slice and its critical bypass and integration limits are
in [FAMILY_AUTH.md](FAMILY_AUTH.md); it does not protect `FamilyBank`.
`hosted.object.FamilyBank`
has no HTTP handler. Each instance must be bound to exactly one family's DO
storage. A future trusted caller must authenticate the principal, authorize the
requested family and operation, and resolve the family DO server-side. DO IDs,
client-supplied family names, the self-host PIN, and knowledge of an operation
ID are **not authorization**. Do not wire this to a public Worker yet.

`FamilyService` uses the original root `bank.py`, `db.py`, and `config.py`.
All access runs in a synchronous DO transaction; the adapter rejects SQL or
`commit()` outside it. The bank's inner commits are no-ops and exceptions roll
back the whole outer transaction. Only the explicitly listed read and mutation
operations are supported. A nonempty unversioned DB, any persisted version 0,
and any unknown schema version are rejected; no import or reviewed migration of
existing family data is offered, including marker-only version 0 databases.
The version marker, original schema/migrations and operation table are created
atomically. Future schema versions need explicit, transactional upgrade code.

**Legacy accounting caveat:** saving or withdrawing capitalizes accrued savings
interest without a separate interest ledger transaction. This inherited bank
behavior is intentionally unchanged; ledger entries alone do not explain every
balance change. Do not present the ledger as a complete accounting audit trail.
Collection records interest when positive and a history snapshot when rewards or a
matured bond are collected; a zero-gain bond payout still creates a snapshot.
History uses the inherited owned-value rules.

`collect_rewards` takes a positive child ID and optional exact kind `all` (default),
`allowance`, `interest`, or `invest`. Missing children are rejected. The result
is the change in owned value, not the full investment payout. Its deduplication,
state bounds and rollback are the same as for other hosted mutations.

A mutation requires a stable, family-scoped operation ID (1–200 characters).
The canonical payload is the action plus its bound argument values, with omitted
defaults filled in. Text is preserved exactly: whitespace differences conflict,
even when bank logic later trims a note. Results and changes are persisted in the
same transaction. Exact canonical retries return the recorded result while the
family remains within safe limits; if current state is unsafe, even a retry fails
closed. A recorded negative business result is also returned on retry; reuse with
a different canonical payload fails. Clients must durably
retain and reuse IDs and payloads through lost responses. Records have no
expiry or pruning policy; do not remove them without a safe retry horizon.

## Conservative hosted-only limits

These limits deliberately favor rejection over precision loss and do not alter the
self-hosted bank. Inputs and every stored individual balance, signed ledger amount,
history amount and bond principal/payout must be integer cents of magnitude at most
1,000,000,000 ($10 million); balances and bond amounts must be nonnegative.
Bond payout must be at least principal, and maturity must not precede purchase,
even before maturity or if collected. Each
child's owned total, each pending reward component and combined pending total must
also be between 0 and that ceiling, both before and after every action. Thus an
otherwise valid gift, accrual, transfer, or investment may be rejected. Interest
and investment calculations retain the original floating-point/round-to-cent bank
semantics; this is not decimal-exact interest accounting.

There may be at most 100 children and 1,000 each of bonds, history snapshots and
transactions **per family**. The full history embedded in `kids` is therefore
bounded; transaction reads allow 1–100 rows (default 60). IDs and input amounts
are positive integers no greater than 1,000,000,000. Allowance periods are 1–3650
days and paid periods at most 100,000; timestamps must be 0–4,000,000,000 epoch
seconds, the hosted time offset must be zero, savings rate 0–100% per week and
bond return 0–100%. Defaults are also checked. Operations that hit a limit fail
atomically; there is no pruning/recovery interface, and an old or long-idle family
can become unavailable when accrued totals cross the ceiling. SQL predicates
check stored numbers before the SQLite-to-JavaScript bridge; bounded row counts,
timestamps and rates keep intermediate sums/products far below 2**53. This does
not prove arbitrary corrupted schemas or production-scale performance safe.
Idempotency records remain unbounded and need a future retention policy. No
supported remediation or recovery exists for corrupt, unsupported-version or
over-limit data; do not bypass the guards or edit a real database to recover it.

## Tests (synthetic data only)

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests_hosted -p 'test_foundation.py' -v
(cd tests_hosted && npm ci --ignore-scripts)
TMPDIR="${TMPDIR:-/tmp}" .venv/bin/python tests_hosted/integration.py
TMPDIR="${TMPDIR:-/tmp}" .venv/bin/python -m unittest discover -s tests -v
```

The integration harness creates a temporary Wrangler config, source copies,
logs, process group, and persistent emulator DB under `TMPDIR`, and serves an
**unauthenticated synthetic-only** worker on loopback. It never deploys and has
no checked-in deployment config. Wrangler is pinned in `tests_hosted`; set
`WRANGLER_BIN` to another pinned executable if needed. It compares real unchanged
bank functions against a separate in-memory SQLite oracle after each tested
operation, including all reward collection modes, exercises synthetic families,
simultaneous distinct and repeated IDs, restart persistence, and exceptions
after inner commits. The fake
SQLite DO unit tests also exercise migration interruption and rollback after the
idempotency insert; those particular fault points have not been proven in the
Cloudflare runtime. Tests access no existing family DB.

## Before any real use

Authentication, authorization, the family-to-object mapping, RPC serialization,
request validation at the trust boundary, end-to-end secure session/revocation, app/API
integration, backup/recovery, limits and abuse controls, billing, and production
Cloudflare behavior remain unimplemented or unverified. The current local test
uses synthetic HTTP to call the service *within* a subclass, not actual
cross-Worker DO RPC. Only tested bank operations are available. The conservative guards are not a
substitute for production quota, abuse, corruption, or accounting review.
Idempotency records remain unbounded. Local concurrent
requests do not prove multi-region failures or crash durability. No real-data
migration or upgrade path has been reviewed. This code is a foundation, not a
claim of production readiness.
