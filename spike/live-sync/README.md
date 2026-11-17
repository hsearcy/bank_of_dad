# Local synthetic live-sync spike — NEVER DEPLOY

This is a disposable development fixture, **not an authentication or hosting implementation**. It deliberately exposes privileged family bootstrap, grant creation/revocation, expiry and fault injection through a loopback-only test worker; it puts fake grant tokens in WebSocket URLs. It is unsafe on a network and must never receive real identities, credentials, or data. It does not use a live application, existing database, OIDC, or the global revocation spike.

The temporary JS Worker serves a tiny test page and forwards to a real Python `FamilyBank` Durable Object subclass. It uses the existing `FamilyAuthorization` and real banking functions/SQLite adapter. Each synthetic family has its own DO/database. The Python DO accepts an **ordinary** (not hibernating) WebSocket only after an authorized family read; before each broadcast it rechecks every connection's current grant, closing denied ones. An authorized first successful gift commits via `FamilyAuthorization.mutate` before an invalidation-only message is sent; failed or replayed requests do not send an invalidation. The fixture's pre-check and mutation are synchronous without an intervening await within one DO event. The DO-local authorization still governs reads and writes. Bootstrap creates one synthetic child with the domain's actual starting balance and issues two synthetic grants.

## Run

Prerequisites: Node, Python, Chromium, and the pinned Wrangler from `tests_hosted/package-lock.json`. The browser test also requires Playwright. For example, from repository root:

```sh
npm ci --prefix tests_hosted
npm install --no-save --package-lock=false --prefix tests_hosted playwright@1.64.1
PLAYWRIGHT_MODULE="$PWD/tests_hosted/node_modules/playwright" python spike/live-sync/test_browser.py
```

Override `WRANGLER_BIN`, `PLAYWRIGHT_MODULE`, or `CHROMIUM` if installed elsewhere. `TMPDIR` controls the disposable parent directory. The harness copies source to a new temporary directory, uses separate Wrangler HOME/cache and persistent state, picks a loopback port, stops its own process group, and deletes the state on exit. Do not point it at a production state directory.

For manual local use, run `python spike/live-sync/test_browser.py --serve`, open the printed loopback URL in two browsers, bootstrap family A once, copy its second fake grant into the other browser, and connect both. Click Gift and observe both wallet values. Stop with Ctrl-C. Restarting creates empty synthetic state. The controls are intentionally not a real login flow.

## Verified behavior and boundaries

The Playwright run uses three independent Chromium browser processes: two for family A and one for B. It checks actual wallet values and transaction history, same-family push, no B push, rejected cross-family socket/read, duplicate and conflicting operation IDs, zero-amount rejection, concurrent distinct writes and same-ID retries, disconnection/reconnect snapshot, revoked read/socket and subsequent push denial, forced expiry read/socket closure, and a controlled failure injected after the authorization operation insert. It checks unchanged wallet/history and no invalidation after the failed transaction, then retries successfully. Existing hosted integration has broader full-database snapshot coverage.

This is **not production ready**: the JS gateway and fake identities are intentionally unauthenticated, grants are visible in the page/URL, session ownership is not tied to real browser identity, WebSockets keep the DO resident, and no scale, hibernation, eviction, multi-region or deployment behavior is tested. Expiry is forced by a synthetic DB edit; idle sockets are closed upon a later authorization sweep, not by a timer. There is no durable event/outbox or revision: a crash after commit and before push can lose an invalidation. Reconnect and visible-tab resume fetch a fresh authorized snapshot, but there is no automatic reconnect or periodic reconciliation for a continuously connected client. Async refreshes are not version-ordered. Real credentials, a trusted gateway, access revocation across devices, hardened origin/session handling, durable revisions/reconciliation, lifecycle management and production security review remain future work. This spike proves local integration only, not a hosted service or production authorization.
