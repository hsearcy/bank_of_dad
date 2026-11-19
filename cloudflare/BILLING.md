# Cloudflare Stripe billing (explicit mode separation)

Billing is **unavailable by default**. Public sandbox payments are forbidden.
The production Wrangler file is unchanged and remains disabled. No deployment,
real Stripe credential validation, product creation or payment was performed.

See [live billing configuration](LIVE-BILLING.md) for the explicit opt-in and
local-runtime verification boundary.

The integrated target uses Stripe's official REST API via Workers `fetch`, pinned
with `Stripe-Version: 2025-03-31.basil`. Checkout and customer portal are hosted by
Stripe. The bank never handles card data. No Stripe Python SDK or dependency is
added to the free self-hosted application.

## Configuration

For an **approved disposable Stripe sandbox** only, install runtime values in a
private, ignored `cloudflare/.dev.vars` file (not in source or Wrangler `vars`):

- `BOD_STRIPE_SECRET_KEY`: sandbox `sk_test_...` secret key
- `BOD_STRIPE_WEBHOOK_SECRET`: endpoint-specific `whsec_...` signing secret
- `BOD_STRIPE_PRICE_ID`: the existing active USD 999-cent/year recurring Price
- `BOD_BILLING_MODE`: exactly `test` (local only)
- `BOD_STRIPE_LIVEMODE`: exactly `false`
- `BOD_STRIPE_PORTAL_CONFIGURATION`: optional explicit sandbox `bpc_...`; otherwise
  configure the sandbox's default customer portal before testing

Google client configuration is still required for subscribing as a signed-in
parent. No customer is created by guest onboarding or sign-in. The original
31-day no-card trial is not reset or transformed into a Stripe trial.
Checkout starts payment immediately only after the parent confirms in Stripe.

Use Stripe CLI forwarding to the exact loopback `/billing/webhook` URL for local
sandbox delivery; use **that listener's** signing secret. Do not put secret keys
in browser JavaScript or share them in chat. An approved public deployment must use Cloudflare's secret store, not checked-in
variables. Live billing requires the explicit configuration in LIVE-BILLING.md;
no deployment or provider acceptance is performed here.
`BOD_TEST_STRIPE_ORIGIN` is only for the disposable HTTP test fixture, requires a
loopback origin and the exact dummy key `sk_test_fixture`, and must never be set
for real sandbox validation.

## Identity, entitlement and ordering

- POST `/billing/checkout`, `/billing/portal`, `/billing/refresh` require the
  existing authenticated family session, exact Origin and its CSRF token. Guest
  requests redirect to Google sign-in. Billing stays accessible after expiry.
- A family DO creates its customer with a family-derived idempotency key, persists
  that association, then registers a unique customer/family mapping in the
  directory **before** starting Checkout. Browser fields, Stripe metadata, email,
  success URLs and client references never establish the association or access.
- Checkout reads every page of current subscriptions before issuing another
  purchase. Any nonterminal status, including `past_due`, `unpaid`, `incomplete`
  and unknown statuses, goes to the portal for recovery, even after Checkout
  expiry. Only `canceled`/`incomplete_expired` allow replacement. Open sessions
  are reused; new creation attempts are durable before their idempotent POST.
- Webhooks verify HMAC-SHA256 on exact raw bytes with constant-time comparison,
  any matching `v1` rotation signature, and a timestamp bounded in both directions
  by 300 seconds. Wrong mode and Connect account/context are rejected.
- The directory stores event ID/digest and pending/done state. Relevant mapped
  events re-fetch the customer's **current** full subscription list, not the
  historical event's subscription fields. Unmapped events cannot grant access.
- Each family serializes billing across network awaits using an async lock;
  every operation gets a durable epoch checked before writes. Network calls never
  run inside `transactionSync`, so ordinary bank requests can continue while
  Stripe responds. Restarted DOs do not resume promises from the old instance.
- Subscription `active` and Checkout completion are **not** settlement evidence.
  Paid access uses only current, unexpired, non-proration invoice-line coverage
  mapped to the exact customer/subscription/item/configured price. The invoice
  must be paid with positive total and zero remaining balance; its Basil
  InvoicePayments must allocate enough actually successful PaymentIntent/charge
  settlement. Zero-dollar, out-of-band-only, processing and failed payments do
  not establish paid access. Trial and free grants remain independent.
- `active`, `past_due` and `unpaid` can retain an already-paid unexpired interval,
  but an unpaid renewal cannot extend it. `trialing` is not paid access; immediate
  cancellation revokes paid coverage and scheduled cancellation caps it at
  `cancel_at`. Any partial/full refund, dispute or post-payment credit note
  invalidates that invoice's coverage (no automatic refund proration). Won
  disputes require operator review if Stripe still marks the charge disputed.
  Invoice, async-payment-failure, refund, dispute and credit-note events reconcile
  current provider state; old paid events cannot restore refunded access.
- Archived associated product/prices remain valid for existing-customer portal,
  cancellation and reconciliation. New Checkout still requires an active catalog.
  Live account/product/price/customer/mode identity checks are not relaxed.
- Legacy status-derived live cached dates are masked until reconciliation records
  the `invoice-v1` paid policy; trial/free/auth/ledger state is untouched.
  Entitlement, paid policy and the family's event receipt commit atomically.
  Failure returns 503 and leaves retryable pending work, not a successful receipt.
  A post-commit durable alarm delivers socket invalidation/revocation and then
  rearms the existing expiry policy. Replaying an already-committed family receipt
  rearms that notification. No RPC-context Close-frame defect was established.
- Completed duplicates do not fetch again; an event ID with different raw bytes
  is rejected. A directory failure after family commit retries through that
  family's durable receipt. Paid changes recheck sockets and schedule expiry.

## Local verification and cancellation diagnosis

```sh
uv run pytest -q test_paid_invoices.py test_live_billing.py test_billing.py
```

The original `test_ordering_failure_retry_and_restart` failure was reproduced
unchanged: `ws.recv(timeout=5)` raised `TimeoutError`. Wire-level instrumentation
then established that **both Close frames had already been exchanged**, with
code `1008` and reason `Access ended`. Python's client remained `CLOSING` waiting
for TCP EOF; the DO close callback had already observed `CLOSED`, code `1008`,
and `was_clean=True`. Paid/trial dates were zero and HTTP access returned 402.
Thus the previous claim that the cancellation Close frame was missing was wrong.
This resembles [workerd issue 8185](https://github.com/cloudflare/workerd/issues/8185),
but does not establish that every transport/runtime shares that issue.

The test now synchronizes on the actual incoming protocol Close event and checks
its exact code/reason and reciprocal Close, with the same five-second deadline.
It does **not** claim TCP EOF completed in five seconds. No production policy,
hibernation API, timeout or frontend was changed. A new independent Chromium
regression waits for the server's `connected` message before cancellation, disables
the existing 30-second polling, requires clean code-1008 closure and navigation
to billing within five seconds, and checks another family's socket and bank remain
untouched. The synchronized diagnostic browser run navigated in 2.376 seconds.

Full integrated regression now completes: `test_owner.py test_runtime.py
test_oauth.py test_billing.py` returned **40 passed** in 869.73 seconds, including
all four billing tests and actual idle-browser cancellation. The only warning
was Authlib's existing `authlib.jose` deprecation. The transport-EOF discrepancy
is documented, not repaired or hidden by a longer wait. This is local runtime
acceptance with synthetic provider HTTP fixtures, not real Stripe acceptance.

Actual local workerd/SQLite and the real Flask templates are exercised with
separate disposable OIDC/Stripe HTTP fixtures: browser forms at phone/tablet/
desktop sizes, Checkout/portal redirects stopped before leaving the app, CSRF,
concurrent Checkout reuse, raw signature/mode/digest rejection, duplicate and old
events, paginated payment recovery, terminal resubscribe, trial/price semantics,
provider failure/retry, SQL-trigger rollback, restart persistence, ordering during
a delayed network response and ordinary bank reads during that response. The
paid-access idle WebSocket checks distinguish protocol Close delivery from TCP
shutdown, as detailed above.

Remaining launch work: real Google browser login; actual sandbox Checkout and
signed provider delivery/cancellation/recovery; portal configuration; Stripe
failed-payment/customer email settings; and the existing public HTTPS, traffic,
backup/restore and deployment release gates. Owner controls, audited free-access
administration and the configured support mailto are now integrated; see
[owner and launch handoff](OWNER-AND-LAUNCH.md). Fixture success does not
claim any of those checks happened. No live customer, purchase, price, endpoint,
account, deployment or production secret was created or modified.
