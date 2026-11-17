# Synthetic Python DO clock-barrier experiment

**Unsafe test fixture only.** This is not wired to the root app, v1, recovery,
identity, or a deployable gateway. The harness creates temporary Wrangler
configuration and disposable local SQLite state, binds HTTP to loopback, and
removes its temporary directory. Its endpoint deliberately allows callers to set
clock, failure and gate controls, and even disable serialization. Never deploy it
or expose it to an untrusted client.

Run from the repository root:

```sh
npm ci --prefix tests_hosted --no-audit --no-fund
python tests_hosted/clock_barrier_probe.py
```

The SQLite-backed Python DO serializes its synthetic entry point using
`blockConcurrencyWhile`, performs a separate synchronous watermark transaction,
awaits `storage.sync()`, and then performs a separate synchronous synthetic
expiry/action transaction. The test clock is an integer in [0, 2000], the
synthetic grant expires at 1010, and rollback fails closed. The bound is an
arbitrary fixture rule, not a trusted-clock policy. The test-only pause occurs
**immediately after sync resolves, before the action**; it is not a pause inside
`storage.sync()` or a simulation of its underlying disk operations.

The harness submits distinct JS-origin calls via two stubs and waits for an
external control DO to record the second invocation while the first is paused.
It checks that the second has not entered the guarded callback, tests the reverse
order using Python DO-to-DO RPC, and verifies another family can proceed. An
ungated negative control actually reproduces a newer 1010 observation followed by
an incorrect stale 1009 action. Thus the test demonstrates that the local
runtime and harness can expose that race, and observes exclusion for the tested
ordering. It does **not** prove all possible RPC scheduling, reentrancy,
cancellation, retry, or callback-failure interleavings. The test-only control
call inside the gated callback is not proposed for real authorization.

The suite also checks denial at 1010, rollback at 1009 after a graceful Wrangler
restart, before/after-barrier failures, SQLite transaction rollback after writes,
invalid/nonfinite clock values, future-time lockout, and independent family
state. `before_sync` is a simulated barrier failure before making the call, not
an injected rejection or ambiguous completion of the actual `storage.sync()`.
`after_sync` simulates failure before action; `action_tx` throws after the actual
synthetic insert to exercise rollback. These are synthetic faults, not a provider
failure injection facility. Runtime exceptions are reduced to `runtime_error`
inside the gated callback to keep the experiment running; this is not a
production error-handling policy.

A successful sync and graceful local restart do not prove physical-disk crash
recovery or storage-provider durability, and neither provides backup/PITR
anti-rollback. There is no schema migration, validation of real family
catalogs, real grants, identity, trusted clock, production authorization, or
verification that all real entry points are serialized. The design document's
production blockers remain unresolved; a forward clock jump can permanently
quarantine a family, and safe operational recovery still requires a separate
policy and review.
