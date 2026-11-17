# Hosted mode: one parent identity, one family, Stripe billing

This is an opt-in path in the existing Flask app, not a separate demo. The default
`BANK_OF_DAD_MODE=self-hosted` remains free, local, and needs only the base
Flask/Flask-Sock dependencies. Do not point
hosted mode at an existing local bank database. It creates separate files under
`BOD_HOSTED_DATA`; `BANK_OF_DAD_DB` is not used in hosted requests.

## Smallest setup

Use **Auth0 Universal Login** as the managed parent login/recovery service (or a
conformant confidential OIDC provider using RS256, discovery and code + PKCE).
The application does not implement passwords, collect parent profile information,
or link identities by email. A different issuer/subject is a different family.
Auth0 is a recommended operator choice, not an account provisioned by this change;
review its current pricing/terms before choosing it.

1. Register a Regular Web Application at the identity provider. Allow the exact
   callback `https://YOUR-HOST/auth/callback`, enable Authorization Code and RS256.
   Use provider-hosted signup/recovery; configure the provider's desired login and
   abuse protections. Only the `openid` scope is requested.
2. In a **Stripe sandbox/test account first**, create a product with one active,
   recurring USD **999 cents / year**, quantity one, Price. Configure the Stripe
   customer portal to allow payment-method updates and cancellation at period end,
   without plan/quantity switching. No trial, discounts or tax-address collection
   are requested by this implementation. Any legally required tax/privacy/terms
   configuration remains the operator's responsibility; do not assume it away.
3. Register a platform-account snapshot webhook at
   `https://YOUR-HOST/billing/webhook` for:
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`,
   `invoice.payment_action_required`. Use the endpoint's signing secret, not an API
   key. Configure current Stripe API snapshot events; Connect/thin events are not
   supported. The SDK is pinned to Stripe Python 17.0.0 and uses its default API
   version, with subscription item `current_period_end`.
4. Install `python -m pip install -r requirements-hosted.txt`. Set the following in
   a private service environment, **not git** (the app does not load `.env` itself):

   ```text
   BANK_OF_DAD_MODE=hosted
   BOD_PUBLIC_ORIGIN=https://YOUR-HOST
   BOD_HOSTED_DATA=/absolute/private/persistent/bank-of-dad-hosted
   BOD_SESSION_SECRET=<random value of at least 32 characters>
   BOD_OIDC_ISSUER=https://YOUR-TENANT.auth0.com/
   BOD_OIDC_CLIENT_ID=<registered client ID>
   BOD_OIDC_CLIENT_SECRET=<registered client secret>
   BOD_STRIPE_SECRET_KEY=<sandbox secret API key>
   BOD_STRIPE_PRICE_ID=<sandbox annual price ID>
   BOD_STRIPE_WEBHOOK_SECRET=<endpoint signing secret>
   BOD_STRIPE_LIVEMODE=false
   ```

5. Serve the app with a production WSGI server, for example
   `gunicorn --workers 1 --threads 32 --bind 127.0.0.1:5001 app:app`, behind HTTPS.
   Each open WebSocket consumes a request thread; leave capacity for HTTP/billing.
   Preserve the configured public Host header; do not expose the WSGI listener.
   Forward WebSocket Upgrade/Connection for `/api/updates` and allow idle pings.
   Hosted WebSocket Origin validation uses `BOD_PUBLIC_ORIGIN`, including when
   the trusted proxy terminates HTTPS and talks HTTP to WSGI. No arbitrary
   forwarded scheme/origin headers are trusted.
   Trust neither arbitrary forwarded headers nor a browser-supplied callback URL.
   Apply ingress rate limits (particularly login/billing) at the reverse proxy.
   Run as an unprivileged service user, with `umask 077` and a private persistent
   directory. Back up all hosted SQLite files together using SQLite-aware backups
   or while stopped; use local disk, not network-shared SQLite storage.
6. Visit `/`, sign in, subscribe in Stripe Checkout, then return to `/billing`.
   Webhook reconciliation activates access. If delivery is delayed, the explicit
   **Refresh subscription status** button reads Stripe directly. Then open the
   family bank, open Dad controls, and add children using nicknames if desired.
   Manage payment methods and cancel in the Stripe-hosted portal.

Do not switch to live mode until a real provider/browser login and a real Stripe
sandbox purchase, renewal/failure and cancellation have been verified for the
actual deployment. None were performed as part of this local implementation.
Changing the configured issuer, client, Price or Stripe account is not a migration
procedure. Keep credentials/mode consistent and plan any account migration.

## Authorization and subscription policy

- Authlib performs code exchange, PKCE, nonce and signed ID-token validation.
  Browser-bound state is consumed atomically in SQLite before exchange. Identity
  is the exact `(issuer, subject)`, never an email or request body family ID.
- Flask-Session keeps opaque, Secure/HttpOnly/SameSite=Lax cookies backed by
  server-side SQLite sessions. Login rotates the session ID; local sign-out deletes
  the server session. Sessions expire after seven days. Local sign-out does **not**
  sign out of the identity provider, so its next login may reuse its SSO session.
  Provider logout/revocation does not automatically revoke existing app sessions.
- All bank pages/APIs require this parent session and a current entitlement.
  Existing routes run against the server-selected family's own SQLite database,
  including settings, PIN, ledger and history. Browser-supplied kid/transaction IDs
  are only local IDs in that family's database. Hosted mutations take a SQLite
  writer lock before their domain reads to avoid concurrent lost balance updates.
- The parent login replaces the local Dad PIN in hosted mode. There are **no child
  logins/restricted devices, co-parent invitations, family switching or membership
  management** in this initial slice. Anyone using a signed-in parent browser can
  use every control, including spend, gifts, editing and demo fast-forward. Do not
  present that browser session as restricted child access.
- Checkout and portal use only the family-to-customer association stored by the
  server. The Price and quantity are server configured. Repeated open Checkout
  requests reuse the same session; Stripe idempotency keys mitigate uncertain
  creation responses but are not an exactly-once purchase guarantee across long
  outages, expired keys or time-window boundaries. Before any new Checkout, all
  current customer subscriptions are checked independently of the old Checkout's
  expiry. Any nonterminal subscription (including past_due/unpaid/incomplete)
  routes to the billing portal for recovery instead of a second subscription;
  only canceled/incomplete_expired subscriptions allow subscribing anew. An
  already-entitled family returns to billing. Portal sessions are short-lived.
- Only an `active` subscription with the configured Price and quantity one grants
  access, until the item period end (capped by an earlier cancellation time).
  Cancel-at-period-end retains already-paid access; canceled, past_due, unpaid,
  incomplete and trialing do not grant access. There is no trial or grace period.
  Expiry is checked on each request. An expired subscription blocks bank access,
  but does not delete family data or block billing/refresh/sign-out.
- `/api/updates` is also session/entitlement protected, with exact configured
  public Origin and Host validation before upgrade. Its channel is selected from
  the authenticated family's server-owned ID, never a request parameter. Only
  successful committed bank routes enqueue that family's content-free signal.
  Before initial delivery and each later signal, and every 0.5 seconds while
  idle, the socket rereads the stored session and entitlement; revoked/expired
  access closes it without refreshing the session lifetime. Notification timing
  is visible to other authorized devices in the same family, not other families.
  Authorization checks and socket sends are not an atomic revocation barrier.
  This remains an in-memory single-process broadcaster with bounded queues,
  not durable delivery or multi-worker pub/sub; 30-second HTTP reconciliation
  and reconnect/resume snapshots recover missed signals. More workers require
  a shared broadcaster for immediate cross-worker updates.
- The official Stripe SDK verifies raw webhook bytes and timestamp tolerance;
  future timestamps, wrong mode and connected-account events are rejected.
  Only mapped customer IDs can affect families. Event payloads/metadata and success
  URLs never grant entitlement: the handler reads current subscriptions from
  Stripe, serializes reconciliation under the directory writer lock, and commits
  the receipt and entitlement together. Duplicates are durable no-ops, changed
  payloads for the same event ID are rejected, and failures return 503 for retry.
  Locking is deliberately simple and globally serializes billing calls: suitable
  for a small single-host deployment, not a high-throughput multi-region service.

## Actual retained data / boundaries

The operator stores provider issuer/subject IDs, a random family ID, Stripe customer
and Checkout identifiers/URL/expiry, entitlement expiry, event IDs/payload
fingerprints, browser sessions and short-lived
login state, plus the actual family bank data: children’s display names/emojis,
balances, allowances/rates, investments, spending/gift notes and history. Stripe
collects payment/billing details in hosted pages; the app neither requests nor
stores card details, billing addresses, parent email/profile, dates of birth or
child email addresses. The identity provider may collect parent email and recovery
information. Infrastructure can retain access logs/IP addresses; configure logging
and retention intentionally, avoid callback query-string logging, and do not enable
Authlib/Stripe debug logging with real credentials or people.

Use `flask --app app session_cleanup` periodically to delete expired SQLAlchemy
sessions. Expired login states are removed on the next login. Webhook fingerprints
and family bank data currently have no automated deletion workflow; operators must
provide a reviewed account deletion/retention process before a public launch. No
analytics, marketing identifiers or emails are added. This is data minimization,
**not zero data**, and not a compliance/security certification.

## Verification

```sh
# Free mode: base runtime + test dependencies only, no hosted credentials required.
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
# Hosted tests: install optional test tools in a disposable environment.
python -m pip install -r requirements-hosted.txt -r requirements-test.txt pytest
python -m pytest tests_hosted/test_hosted.py tests_hosted/test_duplicate_subscription.py tests_hosted/test_hosted_realtime.py -q
# Actual Chromium UI and hosted WSS isolation/reconnect tests (system Chromium).
python -m pip install playwright
BOD_TEST_CHROMIUM=/usr/bin/chromium python -m pytest tests_hosted -q
# Free-mode real browser tests (no hosted dependencies required).
BANK_OF_DAD_BROWSER_TESTS=1 BOD_TEST_CHROMIUM=/usr/bin/chromium python -m unittest discover -s tests -v
```

Hosted tests use synthetic identities and balances, real Authlib/JWKS/PKCE HTTP
requests to a disposable local issuer, the real Stripe SDK against a local HTTP
fixture, and SDK-signed webhook inputs. The real network tests connect two devices
in family A and an independent family B, assert post-commit A-only notifications,
reconnect resync, anonymous/unpaid/origin denial, and open-socket logout/session/
entitlement expiry. The HTTP-backend test uses the configured HTTPS public Origin
and preserved Host, modeling TLS termination without trusting forwarded headers.
Chromium also exercises the actual hosted UI with polling disabled, family-isolated
push, reconnect, anonymous socket denial and entitlement expiry. Free-mode browser
tests separately exercise the PIN, animations, input preservation and polling
fallback, and can run without any hosted runtime dependencies.

These tests do not configure or contact a real Stripe or identity-provider account.
Do not mistake those fixtures for provider acceptance, a production security review,
or a verified deployment/proxy configuration.

References: [Authlib Flask OIDC](https://docs.authlib.org/en/latest/oauth2/client/web/flask.html),
[Auth0 web app setup](https://auth0.com/docs/quickstart/webapp/python),
[Stripe subscription webhooks](https://docs.stripe.com/billing/subscriptions/webhooks),
[Stripe customer portal](https://docs.stripe.com/customer-management/integrate-customer-portal).
