# Synthetic pending-dashboard backend adapter — NEVER DEPLOY

This is a **local-only, fake-data fixture**, not a production Worker, authentication gateway, or application. The actual dashboard/frontend is **not wired or modified**. The Python DO extends the existing `tests_hosted/synthetic_recovery_socket.py` v3 `RecoveryRevisionAuthorization` fixture; its inherited methods are trusted internal test RPCs, not a safe public binding. No v1 or raw mutation RPC is forwarded by the dashboard gateway. The separately routed `/__unsafe_test_driver/` is privileged and deliberately unsafe: it can bootstrap fake families, issue grants, act, acknowledge, inject faults and inspect entire databases. Its static test secret and loopback hostname check are isolation aids, **not security**. Never deploy this Worker or expose/bind its DO, driver, credentials, or URLs to real users or data.

Dashboard GETs use synthetic `x-spike-family` and `x-spike-token` headers: `/api/state` returns the real dashboard-shaped `kids`, `config`, `server_now` plus domain `revision`; `/api/transactions?kid_id=...` returns transactions, time and revision; `/api/pending` returns only the current grant principal's pending operation or null. `/__spike/socket?family=...&token=...` uses fake query credentials (unsafe outside this fixture). All non-driver non-GET requests return 405 without contacting the DO, including every existing application POST route. This is not a complete UI API and it does not expose dashboard mutation or acknowledgment.

Each newly committed operation, including a false or no-op result that does **not** advance domain revision, sends `{"type":"invalidate"}` to all still-authorized sockets in that family; the first committed exact-result acknowledgment sends another. Replays and repeated acknowledgments send none. Clients must refetch **both** state and pending even when revision is unchanged. Pending remains principal-scoped across different grants; invalidations disclose activity timing to other authorized family members and need a production privacy decision. Sockets are reauthorized on fanout. Delivery is best effort, not an outbox: disconnect or crash can lose events; reconnect and reconciliation remain future work.

## Verify locally

Install pinned test dependencies with `npm ci --prefix tests_hosted`, then run:

```
WRANGLER_BIN="$PWD/tests_hosted/node_modules/.bin/wrangler" python spike/pending-dashboard/test_integration.py
python -m unittest discover -s tests_hosted -p 'test_*.py' -v
```

The adapter harness copies only synthetic source into temporary scratch, extracts the existing `cfg` function without importing the live app, constructs local Wrangler configs, and isolates HOME, logs, and SQLite persistence. It exercises the actual local JS Worker to Python DO boundary and real sockets with two fake families, two grants for one principal and a separate principal, cross-family denial, full SQLite catalog and typed-row snapshots after every application's POST attempts, pending-only and domain events, exact acknowledgment, one-unacknowledged limit, injected transactional failure/retry, and graceful restart/replay. `server_now` naturally changes and is excluded from state equality comparisons. Quiet socket checks are bounded, restart is graceful, and injected failure is not a provider crash or network failure. The more extensive inherited fixture tests are documented in `tests_hosted/README-recovery-socket.md`.

No production identity/session/origin authorization, trusted clock/rollback protection, backup/restore strategy, migration, durable notifications, hibernation, or deployment security is provided or validated. Reusing the fixture in production would be unsafe.
