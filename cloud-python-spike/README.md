# LOCAL-ONLY Python Durable Object feasibility spike

**Do not deploy or expose this worker.** Its routes are unauthenticated,
accept synthetic operations, and are intentionally unsuitable for real data.
It is an experiment, not an app or migration plan.

## Run

Requires Python 3, Node.js (verified with 26.8.1), npm, and a writable scratch
`TMPDIR`. Install downloads packages; a first Wrangler/Python Workers run may
also download runtime components. No Cloudflare login is required.

```sh
cd /home/ares/git/bod-python-cloud/cloud-python-spike
npm ci --ignore-scripts
TMPDIR=/home/ares/.hermes/cache/scratch python3 test_spike.py
```

Wrangler is pinned at 4.163.0 with its dependency tree in `package-lock.json`;
compatibility date and flags are in `wrangler.jsonc`. The harness stages the
**current checkout's unmodified** root `bank.py`, `db.py`, and `config.py` into
a disposable directory at runtime because Wrangler bundles Python modules from
its worker directory. They are never maintained as spike copies. The SQLite
oracle imports those same root modules directly. A fresh temporary directory
under `TMPDIR` contains all state, logs, and staged source; it is deleted on
normal completion. This Linux harness starts each Wrangler in its own process
session and waits for all non-zombie process-group members to exit before a
restart or cleanup, escalating to SIGKILL only for that group. Ctrl-C also runs
cleanup; SIGKILL or host failure cannot guarantee cleanup. It chooses a fresh
loopback HTTP port for each start, requests an OS-assigned loopback inspector
port (`--inspector-port 0`), and retries up to five times on reported bind
collisions. Wrangler 4.163.0 has no `dev` control-port flag; its internal
Miniflare listeners use dynamically assigned ports. The HTTP port selection
still has a bind race, so readiness is checked against this start's Wrangler log
and retries are bounded. State survives restarts only within that invocation.

To repeat the parallel full-suite check (each process has its own scratch state):

```sh
for round in 1 2; do
  TMPDIR=/home/ares/.hermes/cache/scratch python3 test_spike.py & a=$!
  TMPDIR=/home/ares/.hermes/cache/scratch python3 test_spike.py & b=$!
  wait "$a"; sa=$?
  wait "$b"; sb=$?
  printf 'round %s: %s %s\n' "$round" "$sa" "$sb"
  [ "$sa" -eq 0 ] && [ "$sb" -eq 0 ] || break
done
```

## Coverage and boundary

- The real `db.SCHEMA`, `db._migrate`, `bank.add_kid`, `bank.gift`, savings
  deposit and withdrawal, spend, bond purchase, pending state, time advance,
  and reward collection execute through a small DO SQL adapter. `commit` is
  allowed as a no-op only inside the outer `transactionSync`; the bond INSERT
  cursor obtains its ID with SQLite `last_insert_rowid()` in that transaction.
  A frozen clock and the original fast-forward setting make results repeatable.
- Each step is compared with an in-memory standard SQLite oracle using the
  same unchanged source, and independent fixed results check return values,
  pending rewards, final balances and ledger. A 200-cent 30-day bond cannot
  be collected early, then pays 250 cents; 30 days produce 600 cents allowance
  and 51 cents interest in this scenario. Collection is idempotent on retry.
- Two named synthetic DOs maintain isolated rows across writes, a failed
  collection, and a full runtime restart; both directions are exercised.
  Naming is test routing, **not authentication or authorization**.
- The test compares full raw kids, transaction (including kid_id and bond_id),
  history, bonds and settings rows with SQLite and fixed synthetic expected
  gift values independent of the shared bank implementation. A thrown exception
  after the wallet update and before the ledger insertion must roll those rows
  back completely. A second failure after collection ledger, balance and bond
  updates but before history insertion must also roll back all rows. A separate
  synthetic-only fixture instruments the disposable staged worker, not the
  checked-in worker or application. It verifies collection's adapter `commit`
  returned and its ledger, bond and history writes are visible inside the outer
  transaction, then throws before `transactionSync` returns. The entire raw
  snapshot remains unchanged immediately and after restarting Wrangler before
  retry; pending rewards remain available and a subsequent collection succeeds
  exactly once. This tests that specific local collection path, not every commit
  or failure mode.
- A separate synthetic legacy schema with existing rows captures actual DO
  schema and row snapshots before migration and after each of two calls; the
  second must leave the first unchanged. Fixed expected columns, defaults, and
  preserved rows supplement SQLite parity. It is a focused fixture, not a
  survey of all historic database versions.
- The same persisted DO state is read after a full local Wrangler restart.
  Both worker and oracle forbid application disk-DB entry points; the oracle
  additionally allows only in-memory SQLite, and the configured DB path is a
  disposable sentinel. The local emulator itself necessarily writes its own
  disposable SQLite files to scratch. No family database is used.

This proves only the exercised local slice. There is **no authentication,
authorization, deployed service, production readiness, concurrent-client test,
full adapter/transaction/API parity, or migration of real data**. Other bank
operations, arbitrary transaction semantics, platform limits, upgrades and browser
behavior remain unverified. The adapter implements `lastrowid` only for the
exercised bond INSERT, not generally. The worker's monkeypatches and test routes are
not production patterns.
