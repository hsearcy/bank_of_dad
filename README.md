# Bank of Dad

A local, kid-friendly savings dashboard for tracking imaginary family balances, allowances, savings interest, and fixed-term investments. It does not connect to a bank or move real money.

## Screenshots

These screenshots show fictional names and sample balances in a disposable database. A fresh install starts empty.

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

Use Dad controls to adjust allowances and rates, give money, edit transactions, or advance the clock for demonstrations. Kids can collect rewards, save, withdraw, invest, and record spending. Changes persist in SQLite. This is an educational simulation; rates and balances are not financial advice or actual accounts.

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
Host and same-origin scheme, and allow idle connections (the server pings every
25 seconds). A TLS-terminating proxy must configure trusted scheme forwarding
in its WSGI setup; do not blindly trust arbitrary forwarded headers. If upgrading
is unavailable, HTTP reads/writes and the polling fallback still work. Multiple
worker processes need a shared broadcaster for immediate cross-worker delivery;
without it, reconciliation still runs every 30 seconds. No deployment or proxy
configuration is performed by this change.

Only same-origin browser handshakes are accepted (missing, null and foreign
Origins are rejected). This avoids adding a cross-origin subscription; it does
**not** authenticate a family or user. The socket shares the dashboard's existing
local, unauthenticated read boundary and leaks local activity timing to connected
clients. Do not expose it as a public multi-tenant service.

## Security limitations

**Localhost is the safe default. Do not expose this app to the public internet or an untrusted network.** The built-in Flask server is for local use, not a production deployment. The PIN is a child deterrent, not secure authentication: it has a small search space, no rate limiting, and is sent in requests without encryption over HTTP. Before the first PIN is set, administrative API access is not protected; set it immediately on a trusted machine. The kid-facing API is intentionally unauthenticated, and anyone with access can view or modify some data. Network access requires your own strong authentication, transport encryption, network isolation, and security review. No payment or banking integration is provided.

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
