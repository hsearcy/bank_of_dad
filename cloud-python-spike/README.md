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
normal completion. Wrangler binds only loopback and is stopped with its process
group in a finally block. Persisted state and logs are outside its watched
source directory to prevent reload loops. Ctrl-C also runs cleanup; SIGKILL or
host failure cannot guarantee cleanup.

## Coverage and boundary

- The real `db.SCHEMA`, `db._migrate`, `bank.add_kid`, and `bank.gift` execute
  through a deliberately small adapter for DO SQL cursor rows, bound
  parameters and transaction ownership (`commit` is intentionally a no-op
  inside `transactionSync`). A fixed synthetic clock enables exact comparison.
- The test compares full raw kids, transaction (including kid_id and bond_id),
  history, bonds and settings rows with SQLite and fixed synthetic expected
  gift values independent of the shared bank implementation. A thrown exception
  after the wallet update and before the ledger insertion must roll those rows
  back completely.
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
operations, transaction semantics, platform limits, upgrades and browser
behavior remain unverified. The worker's monkeypatches and test routes are
not production patterns.
