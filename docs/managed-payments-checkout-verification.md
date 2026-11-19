# Checkout: per-session Managed Payments opt-out

## Scope

Both hosted Checkout implementations explicitly disable Managed Payments on each
new subscription Checkout Session. The Worker sends the literal form field
`managed_payments[enabled]=false`; Flask passes `{'managed_payments': {'enabled': False}}`
through the installed Stripe SDK, which serializes that same field.

This does not change Stripe account settings, tax codes, price, payment collection,
subscription mode, trial policy, customer association, authorization or retries.
Ordinary Stripe-hosted payment collection remains in place. Creating Checkout is
not payment or an entitlement grant.

## Official guidance and pinned-version acceptance

Stripe's current official [Managed Payments setup guide](https://docs.stripe.com/payments/managed-payments/set-up)
says: “To disable Managed Payments for a specific Checkout Session, set
managed_payments[enabled] to false.” Its [API reference](https://docs.stripe.com/api/checkout/sessions/create#create_checkout_session-managed_payments-enabled)
describes the field on current Checkout. The reference requested with
`api-version=2025-03-31.basil` did not expose the field in the retrieved HTML, so
current documentation alone was **not** treated as proof of Basil support.

A direct authorized **real Stripe sandbox** probe used an existing test-mode
annual price and explicitly sent `Stripe-Version: 2025-03-31.basil`:

- Omitting the field returned HTTP 400 with the reported missing product tax code /
  account-default Managed Payments error and advice to send the field as false.
- Sending the identical form with `managed_payments[enabled]=false` returned HTTP
  200, response version `2025-03-31.basil`, `livemode: false`, and
  `managed_payments: {enabled: false}`.
- Reading back that exact Session verified it was open, test-mode, and had no
  subscription or PaymentIntent. It was then expired and read back as expired.
- No hosted payment was completed; no payment method, subscription or charge was
  created, and no live credentials, live API writes or account settings were used.

This proves actual provider support on the pinned version and reproduces the
reported error in sandbox. It does not establish production deployment or a live
customer's completed payment.

## Regression evidence

Before the source fix, the real local workerd billing test returned app HTTP 503
against a REST fixture that rejected missing opt-out with the reported Stripe
HTTP 400. After the fix:

- `cloudflare/test_billing.py`, `test_live_billing.py`, `test_restricted_keys.py`
  and `test_public.py`: **72 passed**. Actual outgoing HTTP form and Basil header
  are checked by the fixture. Includes real browser billing forms, exact Origin /
  CSRF denial, default-unavailable mode, sandbox/live configuration separation,
  retries, subscription recovery and no paid access from Checkout alone.
- The added failed-Checkout retry case preserves its persisted customer and
  attempt, then successfully reuses the same serialized request and idempotency
  key without creating another customer; subsequent retries reuse Checkout.
- `tests_hosted/test_hosted.py` and `test_duplicate_subscription.py`:
  **34 passed**, through the installed Stripe SDK HTTP boundary, including the
  literal lowercase `false` form value.

The fixture's simulated live mode uses loopback and dummy keys only. It is not a
live Stripe test. All application databases are disposable. Production integration
and deployment remain the release owner's separate step.
