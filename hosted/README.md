# Hosted storage foundation — not deployable

This is internal storage code, not a hosted product. `hosted.object.FamilyBank`
has no HTTP handler. Each instance must be bound to exactly one family's DO
storage. A future trusted caller must authenticate the principal, authorize the
requested family and operation, and resolve the family DO server-side. DO IDs,
client-supplied family names, the self-host PIN, and knowledge of an operation
ID are **not authorization**. Do not wire this to a public Worker yet.

`FamilyService` uses the original root `bank.py`, `db.py`, and `config.py`.
All access runs in a synchronous DO transaction; the adapter rejects SQL or
`commit()` outside it. The bank's inner commits are no-ops and exceptions roll
back the whole outer transaction. Only the explicitly listed read and mutation
operations are supported. A nonempty unversioned DB and an unknown schema
version are rejected; no import or migration of existing family data is offered.
The version marker, original schema/migrations and operation table are created
atomically. Future schema versions need explicit, transactional upgrade code.

A mutation requires a stable, family-scoped operation ID (1–200 characters).
The normalized action and arguments, result and changes are persisted in the same
transaction. Identical retries return the recorded result, including a negative
business result; reuse with different parameters fails. Clients must durably
retain and reuse IDs and payloads through lost responses. Records have no
expiry or pruning policy; do not remove them without a safe retry horizon.

## Tests (synthetic data only)

```sh
python3 -m unittest discover -s tests_hosted -p 'test_foundation.py' -v
(cd tests_hosted && npm ci --ignore-scripts)
TMPDIR=/home/ares/.hermes/cache/scratch python3 tests_hosted/integration.py
python3 -m pip install -r requirements.txt
TMPDIR=/home/ares/.hermes/cache/scratch python3 -m unittest discover -s tests -v
```

The integration harness creates a temporary Wrangler config, source copies,
logs, process group, and persistent emulator DB under `TMPDIR`, and serves an
**unauthenticated synthetic-only** worker on loopback. It never deploys and has
no checked-in deployment config. Wrangler is pinned in `tests_hosted`; set
`WRANGLER_BIN` to another pinned executable if needed. It compares real unchanged
bank functions against a separate in-memory SQLite oracle after each tested
operation, exercises two synthetic families, simultaneous distinct and repeated
IDs, restart persistence, and an exception after an inner commit. The fake
SQLite DO unit tests also exercise migration interruption and rollback after the
idempotency insert; those particular fault points have not been proven in the
Cloudflare runtime. Tests access no existing family DB.

## Before any real use

Authentication, authorization, the family-to-object mapping, RPC serialization,
request validation at the trust boundary, secure session/revocation, app/API
integration, backup/recovery, limits and abuse controls, billing, and production
Cloudflare behavior remain unimplemented or unverified. The current local test
uses synthetic HTTP to call the service *within* a subclass, not actual
cross-Worker DO RPC. Only tested bank operations are available. Amount inputs are
bounded, but aggregate SQLite/JavaScript numeric precision and overflow, storage
quotas and unbounded history/operation records have not been proven safe at
scale; do not use for real balances until bounded and tested. Local concurrent
requests do not prove multi-region failures or crash durability. No real-data
migration or upgrade path has been reviewed. This code is a foundation, not a
claim of production readiness.
