# Cloudflare port: integrated bank, identity, billing and owner controls

> **Publication status:** this source builds on the original hosted snapshot
> `574d423bd1a50fbc523055e08ea238379f502517`, with subsequent public fixes and
> the 31-day trial/subscription-copy update. Publication does not deploy it. Production account settings,
> keys and owner identities are not included. The implementation/test reports below
> are historical, scoped local evidence, not the current deployment inventory.
> In particular, historical “not deployed”, login-unverified and launch-gate wording
> must not be read as a current production status claim. See
> [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for setup and verification boundaries.

This target reuses the current `app.py` routes, unchanged `bank.py` domain logic,
Jinja templates and browser JavaScript. It does **not** run a Flask server behind
a proxy. A Python Worker routes an opaque browser capability to a family-specific
SQLite Durable Object. Flask runs synchronously inside that object's transaction
using Cloudflare's supported WSGI environment builder.

**Status:** the hosted source includes the guest bank, Google OIDC, durable
sessions and guest claiming; Stripe Checkout, portal and settled-invoice webhook
reconciliation; and saved-family owner discovery with audited free-access controls.
Public HTTPS supports an explicit no-payment pilot or separately configured live
billing. Checked-in defaults deliberately enable neither public service nor payments.
The production template uses example domains and omits the deployment account.

See [Google sign-in](OAUTH.md), [Stripe billing](BILLING.md),
[live billing](LIVE-BILLING.md), [owner controls](OWNER-AND-LAUNCH.md) and
[public hosting](PUBLIC-HOSTING.md). Local tests use signed synthetic providers
through real workerd/SQLite and Chromium; they are not real Google/Stripe
acceptance. A new installation needs its own private settings and acceptance.

The existing free self-hosted launcher and the existing opt-in Flask hosted target
remain available from the same source tree. Nothing here migrates their databases.

## Run locally

Prerequisites: Node/npm, Python 3.13+ and `uv`. No Cloudflare login or paid account
is needed for local execution.

```sh
cd cloudflare
uv sync --locked
npm ci --ignore-scripts
uv run pywrangler dev --local --ip 127.0.0.1 --port 8787 --inspector-port 0
```

Open `http://127.0.0.1:8787`. The checked-in origin matches that address exactly;
`localhost` is a different origin. Local persistence lives under
`cloudflare/.wrangler/state` unless an explicit `--persist-to PATH` is supplied.
Use only a disposable path. Do not point at any existing family data directory.
Stop with Ctrl+C. Restart with the same persistence path to retain the device bank.
Restart the process after changing dependency locks.

`uv` 0.13.4 is a pinned development dependency because pywrangler 1.17.9 requires
it. `uv run` places that project-local executable on PATH; there is no need to
upgrade the machine-wide `uv`. Wrangler 4.165.0 and the Python runtime SDK 1.9.6
are pinned, along with Python and npm dependency locks.

## What works

- First visit opens the real dashboard and its shared sample initialization.
- An opaque HTTP-only browser cookie accesses only its corresponding family.
- A 31-day trial is created once using real server time. Dad's demo clock
  cannot extend access. Welcome dismissal persists.
- Existing collect/save/withdraw/bond/spend, kid/settings/allowance/rates,
  gift, fast-forward and transaction edit/delete routes run against real DO SQL.
- Each entire authenticated read/validate/write request is synchronous within
  `transactionSync`; inner domain `commit()` and route `close()` cannot split it.
  Unexpected Flask exceptions escape and roll back the enclosing transaction.
- Native Durable Object hibernation WebSockets send only post-commit
  invalidations to the existing frontend. Its original 30-second reconciliation
  remains. Logout revokes the capability and closes idle connections; a persisted
  alarm closes idle sockets when the trial expires.
- Same-origin + CSRF checks protect writes. A client cannot choose and initialize
  an arbitrary object by supplying bootstrap headers. Invalid capabilities fail
  closed, without manufacturing a replacement bank.
- SQL bridge inputs/results outside JavaScript's exact integer range are rejected
  rather than silently rounding money. This is a bounded-integer adapter, not
  support for arbitrary SQLite 64-bit financial values.
- Static assets come only from `static/`. The build copies an explicit source
  allowlist and generates the template module from the actual HTML. It does not
  bundle the checkout, virtualenvs, tests, environment files or databases.

## Verification

```sh
cd cloudflare
uv run playwright install chromium
uv run pytest -q test_owner.py test_runtime.py test_oauth.py test_billing.py
# Optional: actual Google HTTP with deliberately invalid credentials, no user login
BOD_TEST_GOOGLE_HTTP=1 uv run pytest -q test_google_http.py
```

These tests launch actual local workerd/SQLite on a separate loopback port with
fresh disposable persistence and an isolated Wrangler HOME. They do not mock the
Cloudflare runtime. `test_oauth.py` uses a separate explicitly synthetic HTTP
OIDC provider; billing uses a separate synthetic Stripe REST provider and real
raw-body HMAC signatures. Only the opt-in HTTP probe contacts Google. No test
contacts Stripe.
They exercise:

1. Every bank mutation above against an independent native SQLite oracle,
   including matching ledger content and balances.
2. Two families, forged capabilities, missing/foreign/null authority boundaries,
   CSRF/Origin denial, object ownership checks and unavailable provider routes.
3. An actual SQL trigger failure after financial writes, full database rollback
   (including ledger/history/sequences), and persistence across runtime restarts.
4. The actual responsive UI in two independent Chromium processes: Dad gift,
   saving, same-family push with polling disabled, other-family silence,
   reconnect, welcome persistence and idle logout revocation.
5. Unsafe cumulative financial arithmetic rejected without a partial ledger.
6. A native persisted alarm closing an otherwise idle socket at trial expiry;
   HTTP reads/writes then deny access without resetting the bank or its dates.
7. Browser-bound PKCE/state, one-time/concurrent callbacks, issuer/audience/azp/
   nonce/signature/algorithm/key/time rejection, key rotation, signed-in visits,
   concurrent guest claims, explicit bank collision and device return, original
   expired-trial preservation, crash/restart and injected session-commit failure,
   and device-scoped HTTP/socket logout and idle session expiry.

This is local-runtime acceptance, **not** a deployed-service, real-Google or
real-Stripe acceptance claim. Existing Flask hosted tests still use synthetic
provider fixtures, even when run through real browsers.

## Public HTTPS hosting and operator acceptance

See [Public hosting](PUBLIC-HOSTING.md) for secure cookies, ingress guards, native
abuse admission and the historical acceptance checklist; see
[PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for portable setup. The no-payment pilot
preserves the original 31-day trial and has no purchase actions. Explicit live
billing is separate. Local tests do not approve a new deployment. Recovery/deletion
operator tooling and a hosted restore drill remain separate operational work.

## Account/cost requirements

Cloudflare's current documentation permits SQLite-backed Durable Objects on the
Workers Free plan. Listed free DO quotas include 100,000 requests/day,
13,000 GB-s/day, 5 million SQL rows read/day, 100,000 rows written/day and 5 GB
storage. Exceeding a free quota fails that operation rather than providing
unlimited service. Worker request/CPU limits also apply; local test wall time is
not a provider CPU/billing measurement, and this target has not been benchmarked
against hosted Free-plan CPU limits.

The optional Workers Paid plan has a minimum $5/month usage charge plus applicable
usage. No upgrade was selected or purchased. Hibernation APIs are used here rather
than permanently active socket loops, but deployed hibernation/billing behavior
has not been measured.

Each independent public installation needs its own Cloudflare account/domain, a registered
Google OAuth client and redirect URI, and the existing Stripe account/price,
portal and webhook settings with the correct test/live separation. Those are
configuration and real-provider verification steps, not evidence that another installation is ready.

Official references checked for this implementation:

- [Flask on Python Workers](https://developers.cloudflare.com/workers/languages/python/packages/flask/)
- [Python package tooling](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Python runtime and deployment](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
- [DO SQLite storage and synchronous transactions](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
- [DO WebSockets](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)
- [Current DO pricing and quotas](https://developers.cloudflare.com/durable-objects/platform/pricing/)
