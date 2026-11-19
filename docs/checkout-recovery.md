# Checkout request upgrades and recovery

Checkout creation is not safely retried by changing an idempotency-key prefix.
A failed HTTP response may have hidden a successful provider mutation.

## Protocol

* Worker `_bod_billing.attempt` remains a nullable TEXT column. New values are
  JSON request snapshots: API version, billing scope, creation time, random
  attempt nonce, full Checkout parameters and a unique family-bound key.
  Bare legacy tokens remain recognizable. Flask adds `families.checkout_attempt`
  and stops using wall-clock windows as request identity.
* Persist the snapshot before POST. Retry its original parameters, not the latest
  code's generated parameters. Use attempt/family metadata only to correlate
  provider objects, never to authorize a family or paid access.
* First enumerate all current subscriptions with cursor pagination. Nonterminal
  subscriptions recover through the portal, including past_due/unpaid/incomplete.
* For uncertain POSTs, enumerate the **mapped customer's** Checkout Sessions using
  GET `/v1/checkout/sessions?customer=...&limit=100`, preserving the original
  `created[gte]` lower bound on every page and adding `starting_after` only.
  Include every status. This is a list endpoint, not eventually-consistent Search.
* A new attempt's matching open session is saved and reused; a completed session
  is saved and routed to the portal. Never create a new session while an older
  session is still `open`, even when local expiry estimates disagree.
* Retry absent new attempts only inside 23 hours. Stripe may prune keys after
  24 hours; absence outside that window requires operator reconciliation.
* Keep the Worker family billing lock and durable epoch fence. Flask retains
  its SQLite writer lock; the attempt is explicitly committed before POST so
  an exception/rollback cannot remove the retry identity. Concurrent retries
  therefore use the same immutable provider request and key.

## Legacy migration (fail closed)

A pre-upgrade token has neither its original request nor its start time. The
Worker can recover one uniquely matching existing subscription Checkout from
its mapped customer: exact mode, original return URLs, and the configured single
price/quantity (read through the line-items endpoint). Multiple candidates,
wrong customer/mode, incomplete pagination, provider errors or an empty list
must **not** cause a fresh POST under another key. Flask's older time-window
attempts cannot be reliably correlated and require operator recovery.

An empty list alone does not prove an earlier POST definitively failed. The
parent sees an explicit recovery/support message instead of endless retries
with a permanently conflicting key.

For the known Managed Payments rejection, an operator must first inspect the
**original** request in the correct Stripe account/mode and verify its definitive
missing-tax-code rejection (not merely the later idempotency conflict), confirm
no in-flight operation, enumerate the mapped customer's sessions/subscriptions
through all pages, and preserve or recover any open session/nonterminal
subscription. Only with that evidence may the exact affected legacy attempt be
retired under the family billing fence (Worker: clear `attempt`; Flask: mark
`checkout_attempt='ready'`). Do not clear the customer, saved checkout, paid
coverage, trial, or another family's state. If provider outcome is uncertain,
leave it blocked and investigate; never guess or offer a second purchase.

No production data or provider credentials are modified by this patch or its
regression tests. Deployment and any evidence-backed legacy-state repair are
separate operations.

## Provider references

Checked official API documentation for `2025-03-31.basil`:

* https://docs.stripe.com/api/checkout/sessions/list?api-version=2025-03-31.basil
  documents `customer`, `created`, `limit` (1–100), `starting_after`, all status
  values, and the `data`/`has_more` response.
* https://docs.stripe.com/api/pagination?api-version=2025-03-31.basil
  documents reverse-chronological cursor pagination.
* https://docs.stripe.com/api/idempotent_requests documents key pruning after
  at least 24 hours and parameter comparison when keys are reused.

Tests use actual workerd/SQLite and the actual Stripe Python SDK against
loopback REST fixtures that compare cached request parameters and return HTTP
409 `idempotency_error` for changes. These are not real Stripe sandbox acceptance.
