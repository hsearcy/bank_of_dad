# Actual UI local synthetic spike — NEVER DEPLOY

Status: local synthetic browser and runtime tests passed. Base: `f15c5252749e898bdf2d8e6bd2cf90ad0e8102ee`. This is not a deployment or a test of real identities or real data.

`test_browser.py` copies this worktree's actual `templates/index.html`, `static/style.css`, `static/app.js`, other static assets, root Python bank modules and hosted adapter into disposable storage. It extracts the exact pure `cfg` function from `app.py` with Python AST, avoiding Flask's database-opening module initialization. The JS Worker serves the template with only one extra script tag for `synthetic.js`. Asset handling disables HTML canonical redirects; only static UI paths are served. Python source and Wrangler config are outside the asset directory.

`caller.js` translates minimal Flask contracts: state (`kids`, `config`, `server_now`), transactions, fake Dad status, gift, save and spend. Other APIs are unsupported. `bank_worker.py` adapts the existing live-sync Python fixture in this new directory: real `FamilyAuthorization`, `FamilyService`, SQLite adapter and bank domain calls; one family per real Durable Object; ordinary real DO WebSocket. No second ledger. Dashboard reads check authorization and use real `all_kids_state`, rates and clock in a single transaction. Gift/save/spend broadcast only successful new commits, rechecking each socket's grant before sending the content-free `{"type":"invalidate"}`. There is no durable event/revision.

All bootstrap, fake principals, grant display/URL credentials, fake PIN bypass, and test session controls exist only in this directory. The fixture exposes privileged unauthenticated bootstrap and must never be deployed or receive real data. It binds only loopback and uses temporary HOME/cache/persistent state. Host checking is an additional local guard, not an authentication boundary. Tokens are real scoped grants issued against fake fixture principals, not evidence of real browser identity or authentication.

The only production file changed is `static/app.js`: an explicitly injected opt-in **and** loopback hostname are required to use synthetic hooks. Ordinary self-host requests, PIN setup, startup, service worker and polling retain their original paths. In the synthetic fixture, service worker registration and 30-second polling are disabled so polling cannot masquerade as WebSocket synchronization. Push reloads the authorized snapshot; reconnect fetches after socket open, and visible-tab resume reloads. Socket loss clears data, closes panels and invalidates requests. Family change/logout clear credentials, hidden DOM, PIN and what-if state. A session epoch guards all API completions; a request serial guards older dashboard refreshes. Socket callbacks check both epoch and socket identity. An intentional stale API completion rejects with AbortError before UI continuation; fixture code suppresses only those intentional stale-handler rejections.

## Run from repository root

```sh
export WRANGLER_BIN=/home/ares/git/bod-live-sync-spike/tests_hosted/node_modules/.bin/wrangler
export PLAYWRIGHT_MODULE=/home/ares/git/bod-live-sync-spike/tests_hosted/node_modules/playwright
/home/ares/git/bod-js-rpc/.venv/bin/python spike/actual-ui/test_browser.py
```

Use `--serve` for manual local exploration. Bootstrap A once; copy the second displayed fake grant to the second browser and connect it to A. The fixture does not persist credentials across reload; reconnect explicitly. Stop with Ctrl-C to delete state. `TMPDIR` selects the temporary parent; `CHROMIUM` optionally selects the executable. The harness only stops its own Wrangler process group, closes both independent Playwright browser processes in `finally`, removes temporary source/state and checks that the forbidden self-host DB was not created. Successful teardown after the browser run was verified.

`browser.cjs` launches two independent Chromium processes (not two contexts in one process). Its assertions cover exact served source/template, no service worker, both actual dashboards after a Dad-panel gift and savings-modal save, concrete bank state/history agreement, actual spend, exact invalidation frames, transport close, offline mutation/reconnect, page reload/reconnect, wrong-family read/write/socket denial, no cross-family push, older refresh suppression, held real state response across A→B with captured stale socket callbacks, committed gift completion across logout, and held history completion across logout. Held responses contain actual server data; the test does not fabricate balances. Negative event checks use bounded 450 ms windows.

## Validation results

The resumed run, with loopback sockets and Chromium available, passed:

- `python -m unittest discover -s tests -v`: 78 tests.
- `python -m unittest discover -s tests_hosted -v`: 55 tests.
- All five hosted runtime scripts: `integration.py`, `auth_integration.py`, `directory_integration.py`, `rpc_integration.py`, and `js_rpc_integration.py`.
- `spike/live-sync/test_browser.py` and `spike/actual-ui/test_browser.py`; the latter verified two independent Chromium processes and cleanup.
- JavaScript syntax checks, fixture Python compilation, and `git diff --check`.

The preceding sandboxed attempt could not create sockets or run Chromium. Its historical logs, if present locally under `validation/`, describe that earlier attempt, not the resumed run. They are not needed to run the fixture.

## Boundaries

No real auth, Directory/OIDC integration, deployment, live app modification, or real database use. Fake Dad status deliberately bypasses the self-host PIN only inside this fixture; unsupported Dad controls return an error. No automatic reconnect, hibernation, eviction/scale/multi-region testing, timer-driven expiry, durable revisions/outbox or production security claim. A commit can survive logout even if its response is discarded; logout does not revoke the fake grant. A crash after commit can lose push. Request-start ordering prevents stale local refresh completion but cannot establish durable server ordering. Continuously connected clients can miss updates after a lost push; reconnect/visible-tab resume obtain fresh state. Existing `spike/live-sync` and all original tests remain unchanged.
