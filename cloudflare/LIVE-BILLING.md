# Explicit Stripe live billing

> **Publication status:** this is the public source snapshot of deployed application
> commit `574d423bd1a50fbc523055e08ea238379f502517`. Production account settings,
> keys and owner identities are not included. The implementation/test reports below
> are historical, scoped local evidence, not the current deployment inventory.
> In particular, historical “not deployed”, login-unverified and launch-gate wording
> must not be read as a current production status claim. See
> [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for setup and verification boundaries.

This change adds configuration support, not provider acceptance or deployment.
Production Wrangler configuration is intentionally unchanged and disabled. No
real live key was available; no Stripe resource or real charge was created.

## Operator configuration (separate authorization required to deploy)

Read secret values only from Worker environment bindings. Store the secret key
and endpoint-specific webhook signing secret in Cloudflare's secret store using
the separately approved staged deployment workflow; do not put them in source,
Wrangler `vars`, URLs, browser code, logs or chat. A connected sandbox does not
supply live credentials. Do not run `wrangler secret put` as harmless preparation:
it may deploy immediately.

For a public paid instance, explicitly set **all** of:

- `BOD_LOCAL_ONLY=false`, exact `BOD_PUBLIC_ORIGIN=https://bank.example.com` (replace with your domain)
- `BOD_PUBLIC_PILOT=false`, `BOD_BILLING_MODE=live`
- `BOD_STRIPE_LIVEMODE=true`
- `BOD_STRIPE_SECRET_KEY`: the intended account's `rk_live_...` restricted key
  (preferred) or `sk_live_...` secret binding
- `BOD_STRIPE_WEBHOOK_SECRET`: this live endpoint's `whsec_...` secret binding
- `BOD_STRIPE_ACCOUNT_ID`: the intended platform `acct_...`
- `BOD_STRIPE_PRODUCT_ID`: the intended active live `prod_...`
- `BOD_STRIPE_PRICE_ID`: its active live USD 999-cent recurring annual `price_...`
- Optionally `BOD_STRIPE_PORTAL_CONFIGURATION`: intended account's portal config;
  otherwise configure its default portal before actual acceptance.

Never set `BOD_TEST_OIDC_ORIGIN` or `BOD_TEST_STRIPE_ORIGIN` publicly. Public ingress
rejects them, sandbox keys, sandbox billing and contradictory flags before assets
or family creation. `unavailable` remains the default; the existing explicit
no-payment pilot configuration still works. Local free self-hosting is unchanged.
Local sandbox billing now explicitly requires `BOD_BILLING_MODE=test` in addition
to its existing `BOD_STRIPE_LIVEMODE=false`, `rk_test_...` or `sk_test_...` test
key, signing secret and price. Key prefixes do not select or override the mode.

### Restricted-key permissions

[Stripe supports restricted keys for all APIs](https://docs.stripe.com/keys/restricted-api-keys).
The application's REST calls and pinned API version are identical for restricted
and unrestricted keys. Prefix acceptance is not proof that a key is authorized.
Review the intended account's key permissions without exposing its value:

| Application API calls | Required access |
| --- | --- |
| `GET /v1/account`, products, prices | Read the platform account and catalog |
| `POST /v1/customers`, `GET /v1/customers/:id` | Customers write/read |
| `POST /v1/checkout/sessions`, `GET /v1/checkout/sessions/:id` | Checkout Sessions write/read, with Stripe's required dependent permissions |
| `POST /v1/billing_portal/sessions` | Customer Portal write, with Stripe's required dependent permissions |
| `GET /v1/subscriptions` and `/:id` | Subscriptions read |
| `GET /v1/invoices`, invoice lines and `/v1/invoice_payments` | Read invoices, lines and invoice payments |
| `GET /v1/payment_intents/:id?expand[]=latest_charge`, `GET /v1/charges/:id` | Payment Intents and Charges read (including expanded charges) |

There are no payout, transfer, bank-account mutation or direct charge-creation
calls. A key granting **Full access—except sensitive operations**, with only
payouts/transfers excluded, should cover this inventory; no missing permission
is identified from the code. The actual key's grants, any IP restrictions and
Stripe account/portal readiness still require separately authorized provider
verification. A narrower custom key must retain every read above: invoice payment
and expanded charge reads establish settlement, not just Checkout success.
Provider permission errors remain fail-closed billing errors; no fallback key,
weakened object checks or paid access based solely on a key prefix is introduced.

## Boundaries

Live operations read `/v1/account` and verify the exact account, price, recurring
interval/count, licensed usage, currency, amount, product and their live flags
before any customer/payment mutation or paid reconciliation. Existing customers
are retrieved and checked for matching mode and ID; subscriptions are checked for
matching mode/customer across every page. Subscription item prices are checked
before granting live paid access. The existing authenticated family, not browser
IDs, email or provider metadata, owns customer association.

Raw-body HMAC verification, rotated v1 signatures and the five-minute past/future
tolerance remain required. Event `livemode` must match the explicitly configured
mode. Connect `account` and `context` events are rejected: this is a pinned
platform-account integration, not a Connect implementation. HTTPS webhooks do not
require a browser cookie or Origin. Browser billing POSTs still require both
session authorization and exact Origin/CSRF.

Family customer/Checkout state is durably bound to mode/account/product/price.
An existing customer or paid balance with a different or missing scope is blocked
for explicit operator reconciliation, not silently migrated or charged again.
Directory customer IDs and event receipts are scoped; family receipts commit
atomically with entitlement and a new `_bod_billing_audit` row containing scope,
operation, event ID, customer, old/new paid-until and reconciliation time.
Public HTTP, WebSocket delivery, expiry alarms and owner access projections mask
unscoped/sandbox/foreign-account paid dates. Trial dates, free grants, auth and bank
balances are not rewritten by that mask. Mode changes are not a migration tool.

No automated live migration/rebinding tool is included. Back up and independently
verify the intended family/customer before any separately authorized migration;
never relabel sandbox payment dates as live. Existing local billing records with
customers but no scope require reconciliation before using the new billing paths.

## Reproducible local tests

From `cloudflare/`, use the locked dependencies and separate disposable state:

```sh
uv sync --frozen
npm ci --ignore-scripts --no-audit --no-fund
uv run pytest -q test_restricted_keys.py test_paid_invoices.py test_live_billing.py test_billing.py test_public.py test_harness.py
```

The fixture speaks the same pinned Stripe REST shapes as the application and runs
through genuine local workerd, Python Durable Objects and SQLite. Dummy
`sk_live_local_fixture` and `sk_test_fixture` values are permitted for a provider
HTTP override only with explicit loopback/local configuration. They are not real
Stripe credentials. No test provider override is permitted publicly. The local
HTTPS tests disable certificate verification only in their self-signed test client.

The exact additional dummy keys `rk_live_local_fixture` and `rk_test_fixture`
have the same local-only HTTP-override restriction. Arbitrary restricted keys
cannot be sent to an HTTP fixture. `test_restricted_keys.py` covers both modes
through actual local workerd Checkout, portal and paid-invoice reconciliation,
plus public HTTPS live acceptance/test rejection and configuration regressions.
These disposable fixtures do not verify a real restricted key's permissions.

Tests cover disabled/mixed configuration, live fixture Checkout/portal, signed
reconciliation, duplicate receipts/audit, cancellation, account/product/price/
customer/subscription mode failures, public HTTPS signature and mode rejection,
and refusal to reuse sandbox/unscoped local paid access publicly. A valid signed
unmapped event is acknowledged over HTTPS as receipt-only, never fulfillment.
Existing tests cover
trial preservation, recovery pagination, atomic rollback/retry, real idle-browser
and WebSocket cancellation, CSRF and session isolation.

## Verification recorded for this change

- `uv run pytest -q test_live_billing.py --basetemp <isolated-scratch>`:
  **13 passed** in 128.80 seconds on the final implementation.
- `uv run pytest -q test_billing.py test_public.py test_harness.py --basetemp <isolated-scratch>`:
  **29 passed** in 366.96 seconds before the final event-object mode hardening and
  removal of redundant directory-level provider reads. The final live suite above
  covers both changes, including positive HTTPS receipt-only delivery.
- `uv run pytest -q test_billing.py test_owner.py --basetemp <isolated-scratch>`:
  **8 passed** in 383.20 seconds on the final implementation, including actual
  browser billing forms, idle cancellation, owner grants/revokes and rollback.
- The initial new test had a malformed synthetic event ID containing a hyphen;
  the verifier correctly rejected it. Correcting the fixture ID produced a passing
  focused live reconciliation run, then the all-green final live suite above.
- Existing Authlib `authlib.jose` deprecation warning remains; it is not a new failure.
- No public config file, provider credential, production data or deployment changed.

## Paid invoice authorization repair

Subscription `active` is not evidence of payment: Stripe documents delayed-method
subscriptions that become active before settlement and remain active after a
failed payment voids the invoice. Entitlement now follows verified paid invoice
line coverage and successful InvoicePayment → PaymentIntent → captured charge
settlement, not subscription item period end. Only the exact configured
customer/subscription/item/price/product and mode qualify. Current paid intervals
survive failed renewal only to their already-paid expiry; future unpaid renewal
periods do not qualify. Immediate cancellation revokes paid access; scheduled
cancellation retains paid coverage to its end. Partial/full refunds, disputes and
post-payment credit notes invalidate the affected invoice's coverage. Zero-dollar
and out-of-band-only invoices do not confer paid access; operator free access is
separate. No automatic charge, refund, cancellation or provider write is added.

Existing subscriptions can use archived associated prices/products for portal,
webhook cancellation and reconciliation; new Checkout requires an active price
and product. Identity/account/mode and annual-price checks still apply. Old live
cached dates without the `invoice-v1` marker are masked until reconciliation;
the marker commits atomically with the entitlement and family receipt.

The new `test_paid_invoices.py` suite exercises real local workerd and disposable
REST-shaped provider fixtures, including a signed unpaid Checkout, processing
versus settled invoice, renewal, refund/dispute, archived catalog portal and
signed cancellation. Fixtures are not real Stripe account acceptance.

Repair verification (disposable local state, no real provider credentials):

- Before the repair, the unpaid-Checkout regression failed on the exact clean
  starting source: a signed unpaid completion with an active subscription granted
  `paid_until` without any paid invoice.
- `test_paid_invoices.py test_live_billing.py test_billing.py test_owner.py`:
  **25 passed** in 728.71 seconds, including receipt rollback/retry/restart,
  idle WebSocket/browser cancellation, billing forms and owner free-access checks.
- Final strengthened `test_paid_invoices.py`: **4 passed** in 169.11 seconds,
  including foreign invoice/payment denial starting from zero paid access and
  restart masking/reconciliation of legacy same-scope live cached dates.
- `test_public.py test_oauth.py`: **49 passed** in 657.83 seconds, covering
  public HTTPS ingress and the existing synthetic-provider authentication suite.
- Only the existing Authlib deprecation warning was emitted.

Official API semantics checked for the pinned `2025-03-31.basil` version:

- [Subscription payment/status semantics](https://docs.stripe.com/billing/subscriptions/overview)
- [Basil invoice parent links](https://docs.stripe.com/changelog/basil/2025-03-31/adds-new-parent-field-to-invoicing-objects)
- [Basil line pricing](https://docs.stripe.com/changelog/basil/2025-03-31/invoice-pricing-configurations)
- [Basil invoice payment migration](https://docs.stripe.com/changelog/basil/2025-03-31/add-support-for-multiple-partial-payments-on-invoices)
- [InvoicePayment listing](https://docs.stripe.com/api/invoice-payment/list?api-version=2025-03-31.basil)

## Real Stripe sandbox verification

On application commit `9cb1cf065a3a7e2487ec75d43585b502509637d0`, a
browser completed a real Stripe-hosted $9.99/year test Checkout against an
isolated local workerd instance. Actual Stripe Basil responses confirmed the
paid invoice → InvoicePayment → succeeded PaymentIntent → captured charge
chain. Nine signed deliveries were acknowledged and matched to durable family
receipts. Paid access survived restart with the scratch trial expired; a forced
failed renewal retained only existing paid coverage; recovery advanced it;
and cancellation revoked it. Portal creation redirected to Stripe. The exact
sandbox test subscription was canceled and the test customer deleted; both were
independently read back from Stripe. No real payment was made.

The identity was a synthetic signed OIDC fixture, trial expiration and renewal
timing were controlled, and recovery/cancellation used sandbox APIs rather than
the portal UI. This verifies the local Worker with the real sandbox—not Google
sign-in, live Stripe, or deployed Cloudflare. Sanitized test evidence is in the
operator scratch directory `bod-real-stripe-9cb/evidence/acceptance.json`.

## Remaining real-provider blockers

Supply and verify actual live account access, existing product/price, key and
endpoint signing secret; configure the live endpoint and customer portal; verify
actual provider Checkout, signed delivery, cancellation and payment recovery in
the separately authorized environment. Confirm payment/email settings. Synthetic
signed fixtures cannot establish those results, live Stripe acceptance or deployed
Cloudflare behavior. No purchases, plan changes, pushes, merges or deployments
are part of this change.
