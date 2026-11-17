# Actual UI local read-only synthetic fixture — NEVER DEPLOY

This is a disposable local experiment, not a hosted application, an authentication system, or a production security boundary. **Never deploy it or use real identities, money or data.** It binds loopback only and uses temporary Wrangler source, HOME/cache and persistent state. The browser sees the actual application template, CSS and JS; the harness injects only the synthetic session script. The fake identities and grants are not evidence of real authentication.

## Separation and scope

- `dashboard-proxy.cjs` is the loopback dashboard ingress. It rejects every non-GET request, including gift, save, spend, other existing mutation routes and unknown paths, and never forwards test control paths. It forwards only the WebSocket upgrade for the fixture socket. The internal worker independently rejects non-GET dashboard requests, including asset and root POSTs.
- `caller.js` runs on a **separate internal loopback port**. Only the test harness talks directly to its `/__test/setup` and `/__test/mutate` controls, using a fresh per-run secret supplied in disposable Wrangler configuration and the test process environment. Bootstrap creates fake principals and initial synthetic data; mutations call the real Python bank through the synthetic test driver, not through the dashboard gateway. These privileged controls and their secret are test machinery, not a production access design. A local user able to inspect the harness or temporary config can obtain the secret. Separate loopback ports are not a security boundary: local processes and potentially browser-origin requests can reach loopback; the driver does not enforce a trustworthy Origin or protect secrets from an untrusted local environment. Never expose either port to untrusted users. Manual `--serve` does not bootstrap the dashboard; bootstrap is intentionally in the isolated automated driver, not a dashboard control.
- The synthetic browser script hides and disables Dad, spend, save, invest and reward-collection controls; mutator entry points also return before local optimistic effects and the client rejects API mutations. It contains no pending-operation or recovery UI. The gateway, not CSS or JavaScript, is the read-only enforcement point. Existing non-fixture application behavior is unchanged.
- `bank_worker.py` retains the real `FamilyAuthorization`, `FamilyService`, SQLite adapter, bank domain methods and ordinary Python Durable Object WebSocket. Snapshot and transaction reads are authorized within the family. Successful new driver mutations broadcast content-free invalidations only to sockets whose grants still authorize. This is synthetic state, not a second ledger.
- Synthetic mode suppresses the service worker and 30-second poll. After socket open or a visible-tab resume, the actual UI refetches an authorized snapshot. Socket loss, logout and family switch clear sensitive displayed state and detach the old socket. Session epochs, request serials and socket identity prevent older responses and callbacks from reviving stale views. There is no automatic reconnect; use the fixture connect control. Socket loss retains the fake token in the input for manual retry; logout and family switch clear it. The token is also present in the WebSocket URL and may be exposed in browser tools or logs.

## Run

From the repository root, with Node.js supported by the pinned Wrangler and local Chromium:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd tests_hosted && npm ci --ignore-scripts)
export PLAYWRIGHT_MODULE="$PWD/tests_hosted/node_modules/playwright"
# Optional: export CHROMIUM=/path/to/chromium
.venv/bin/python spike/actual-ui/test_browser.py
.venv/bin/python spike/actual-ui/test_normal_browser.py
```

`WRANGLER_BIN` can override the pinned local binary and `TMPDIR` the disposable parent directory. The harness stops its owned dashboard proxy and Wrangler process group, closes the two browser processes, removes temporary source and state, and checks that its forbidden self-host DB was not created. `--serve` exposes only the read-only dashboard for manual inspection until Ctrl-C; it does not issue grants or populate data.

## Verification

`browser.cjs` launches **two independent Chromium processes**. With dashboard polling disabled, it uses the private test driver to seed two fake families and make real gift/save/spend mutations; both actual dashboards update through invalidation frames. It verifies cross-family read, driver-mutation and socket denial, no cross-family push, socket loss and reconnect, visibility resume after deliberately dropping push, stale overlapping snapshot suppression, old family socket callbacks, held family-switch state and held history across logout. It verifies hidden money controls, exercises visible what-if/graph/history interactions, rejects dashboard POSTs for supported and unsupported paths, and compares deterministic full snapshots of both families before and after all dashboard POST attempts, including every SQLite table, schema catalog and sequence. It also invokes mutation handlers programmatically and tests a delayed older 500 overtaken by a successful snapshot. Driver controls are rejected on the dashboard ingress and without the secret on the internal port. The real Flask app is separately tested to confirm fake control routes are absent.

The authorized driver gift is a positive control: the full snapshot, child row, ledger and SQLite sequence change while the other family's snapshot and the schema remain unchanged. Schema is included but deliberate schema-change sensitivity is not exercised. All three reward handlers, every rendered fast-forward button and the static Dad and money entry points are invoked directly; no browser write request, optimistic DOM/state change or server snapshot change is allowed. Dynamic Dad gift, allowance, edit-history and rates handlers cannot be independently invoked in synthetic mode because their guarded renderers never create them; the test verifies those elements remain absent, rather than bypassing or rewriting the guard. Dynamic edit/delete transaction and money-modal submission handlers are likewise unreachable through guarded entry points. This is not exhaustive proof against arbitrary injected scripts.

`test_normal_browser.py` starts the real Flask app on an ephemeral loopback port with a fresh temporary database. With no fixture injection, Chromium performs first-run PIN setup and re-entry, adds a child, gifts and saves money, verifies service-worker activation, and checks the registered 30-second poll callback by invoking it directly (not by waiting 30 seconds). Both the Flask server and its database are discarded. The synthetic test also covers an older success overtaken by a newer injected failure and an old-family injected failure delivered after switching families. These browser-intercepted 500s model failures, not actual outages.

## Limitations

WebSocket invalidation is non-durable: a crash or lost message can leave a continuously connected dashboard stale until reconnect or visibility resume. There is no outbox, durable revision, automatic reconnect, timer-driven expiry, hibernation, eviction, scaling, production identity, real browser auth, real deployment or live-data verification. Request-start ordering guards stale local completions but does not establish durable server ordering. Local logout does not revoke a fake grant.

A future money-enabled dashboard must first implement and validate **authoritative server-side operation recovery** for uncertain outcomes and duplicate human intent across lost browser state, tab/device changes, logout and account switching. Replaying a client-generated operation ID while it survives in session storage is not sufficient. This fixture deliberately avoids dashboard writes rather than claiming to solve that problem.
