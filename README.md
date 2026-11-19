# Bank of Dad

A local, kid-friendly savings dashboard for tracking imaginary family balances, allowances, savings interest, and fixed-term investments. It does not connect to a bank or move real money.

## Cloudflare hosted target

The [Cloudflare target](cloudflare/README.md) contains the hosted service source:
guest dashboard, isolated Durable Object SQLite, native WebSocket updates, Google
sign-in/guest claiming, Stripe billing and private owner controls. This public
snapshot also includes [family polish](docs/family-polish-integration.md):
About/Privacy, compact screen controls, child rename/removal, and configurable
savings/investment periods. Operator configuration remains portable, not a copy
of production credentials or infrastructure settings. No live keys, owner
identities or family data are included. See the [publication notes](cloudflare/PUBLIC-RELEASE.md)
for the original hosted snapshot's setup and verification boundaries. Source
publication does not deploy these changes. Free self-hosting remains independent.

## Screenshots

These screenshots show fictional names and sample balances in a disposable database. A fresh self-hosted install starts empty; hosted device trials start with Sample Kid.

**Dashboard** — see balances, projected growth, savings, and investments at a glance.

![Bank of Dad dashboard with two fictional children's savings and investments](docs/screenshots/dashboard.png)

**Money over time** — explore an expanded view of past and projected balances.

![Expanded money-over-time graph for a fictional child](docs/screenshots/money-over-time.png)

## Start

Requires Python 3 and `venv`. From this directory:

```sh
./run.sh
```

The script creates a local virtual environment, installs the requirements (including Flask-Sock for WebSockets), and starts the app. Open **http://127.0.0.1:5001** on the same machine. On first visit, set a 4–8 digit Dad PIN, open **Dad**, and add a kid by name and emoji. A fresh database starts empty; no example family or transaction history is included. Newly added kids receive the starting wallet amount configured in `config.py`.

Use Dad controls to adjust allowances and rates, give money, edit transactions, or advance the clock for demonstrations. Each child also has **Rename** and **Remove…** controls (including the hosted Sample Kid). Rename preserves their money and history. Remove asks for confirmation before permanently deleting that child’s balances, rewards, investments and history; it does not affect other children or move money. See [kid management safety and verification](docs/kid-management.md). Kids can collect rewards, save, withdraw, invest, and record spending. Changes persist in SQLite. This is an educational simulation; rates and balances are not financial advice or actual accounts.

## Configuration and data

| Variable | Default | Meaning |
| --- | --- | --- |
| `BANK_OF_DAD_HOST` | `127.0.0.1` | Listen address |
| `BANK_OF_DAD_PORT` | `5001` | Listen port |
| `BANK_OF_DAD_DB` | `data/bank.db` in this directory | SQLite database path |

For example, `BANK_OF_DAD_PORT=5005 ./run.sh`. Back up your database separately; it contains children's names, balances, history and the PIN hash. Never commit it, local environment files, or backups to a public repository. The app creates the database directory when needed. Configure rules in `config.py` or use the Dad panel for per-child allowances and rates.

## Live updates

The existing Flask app serves a native WebSocket at `/api/updates`. Successful
mutation routes send a content-free `state_changed` notification **after the bank
function commits**; other open dashboards refetch `/api/state` immediately. There
are no write commands over the socket. The frontend uses `ws://` on HTTP and
`wss://` on HTTPS, reconnects with bounded exponential backoff, and resyncs on
reconnection, page resume and returning online. The existing 30-second refresh
remains as fallback/reconciliation for blocked or unsupported WebSockets, missed
notifications, direct database changes and time-derived rewards. Reward sound,
confetti and number-spin animations remain local to the collecting device.

This is an in-memory broadcaster for the existing **single-process**, threaded
self-hosted app (`./run.sh`); it is not a durable event log or a multi-worker
pub/sub service. Each socket consumes server threads. A reverse proxy must pass
WebSocket Upgrade/Connection headers for `/api/updates`, preserve the original
Host, and allow idle connections (the server pings every 25 seconds). In hosted
mode, the expected browser Origin is the configured HTTPS `BOD_PUBLIC_ORIGIN`,
so TLS termination to an HTTP WSGI backend does not require trusting forwarded
headers. Self-hosted mode compares Origin with the request's own host/scheme;
if serving local mode behind TLS, configure trusted scheme forwarding in the
WSGI setup. Never blindly trust arbitrary forwarded headers. If upgrading
is unavailable, HTTP reads/writes and the polling fallback still work. Multiple
worker processes need a shared broadcaster for immediate cross-worker delivery;
without it, reconciliation still runs every 30 seconds. No deployment or proxy
configuration is performed by this change.

Only exact same-origin browser handshakes are accepted (missing, null and foreign
Origins are rejected). In **self-hosted mode**, this is not authentication: the
socket shares the dashboard's local, unauthenticated read boundary and reveals
local activity timing to connected clients. Do not expose local mode publicly.
In **hosted mode**, sockets additionally require an entitled parent session or anonymous device capability,
subscribe only to that server-selected family, and recheck the persisted session
and entitlement before each notification and on idle timeouts. Logout/session
expiry or lost entitlement closes an open socket; HTTP reconciliation on socket
loss moves the dashboard back to login/billing. No family identifiers or balances
are carried in notifications. See [HOSTED.md](HOSTED.md) for operational limits.

## Optional hosted families and subscriptions

An opt-in hosted mode opens straight into a working sample family bank for a
**31-day trial with no sign-up or card required**. Changes persist for that browser.
Optional parent sign-in saves/syncs the entire bank without resetting the trial;
clearing browser data or switching devices can lose anonymous access. After the
trial, subscribe for **$9.99 USD/year per family** or ask for free access. Nothing
is charged automatically. Separate family SQLite files and Stripe-hosted
Checkout/customer portal handle signed-in subscriptions.
Save/sync, subscription and sign-out live in Dad controls; operators can grant durable free
access when a family cannot pay (see the local CLI and support-link setup in
HOSTED.md). Self-hosting stays free and needs no Stripe or
identity-provider dependencies or credentials. Saving a hosted bank with the default sign-in provider needs a Google account; trying it does not.
No Auth0 service is required. See [HOSTED.md](HOSTED.md) for setup,
retained data, verification and explicit limits. Hosted mode is not enabled by
`run.sh`. These Flask instructions are separate from the deployed Cloudflare target;
local fixture tests do not establish real-provider acceptance for your installation.

## Self-hosted security limitations

**Localhost is the safe default. Do not expose self-hosted mode to the public internet or an untrusted network.** The built-in Flask server is for local use, not a production deployment. The PIN is a child deterrent, not secure authentication: it has a small search space, no rate limiting, and is sent in requests without encryption over HTTP. Before the first PIN is set, administrative API access is not protected; set it immediately on a trusted machine. The kid-facing API is intentionally unauthenticated, and anyone with access can view or modify some data. Network access requires your own strong authentication, transport encryption, network isolation, and security review. Self-hosted mode has no payment integration; neither mode connects to a real bank.

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary databases; set `BANK_OF_DAD_DB` to a disposable path when running any additional experiments because importing `app` initializes the configured database.

The ordinary suite includes real HTTP/WebSocket integration on a dynamically
assigned loopback port. Optional browser tests exercise the **actual** template,
JavaScript and Flask routes in independent Chromium processes, with polling
disabled for push/reconnect checks; a separate test verifies polling fallback.
They also check reward animations and preservation of an in-progress modal:

```sh
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium
BANK_OF_DAD_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Without that opt-in, browser tests are skipped and Playwright is not required.

## Licensing

Bank of Dad is available under the [MIT License](LICENSE).
