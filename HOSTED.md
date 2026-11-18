# Hosted mode: one parent identity, one family, Stripe billing

This is an opt-in path in the existing Flask app, not a separate demo. The default
`BANK_OF_DAD_MODE=self-hosted` remains free, local, and needs only the base
Flask/Flask-Sock dependencies. Do not point
hosted mode at an existing local bank database. It creates separate files under
`BOD_HOSTED_DATA`; `BANK_OF_DAD_DB` is not used in hosted requests.

## Smallest setup

Use **direct Google sign-in** for the initial release; no Auth0 account, broker,
or password system is needed. No sign-in is needed to try the hosted bank. Saving
the bank, syncing devices or subscribing with this provider needs a **Google account**
(not necessarily a Gmail address); parents without one cannot use this hosted
login. Google handles account sign-in/recovery. The app requests `openid profile`,
the minimum one additional scope Google's OIDC documentation requires beyond
`openid`; it does not request `email`, offline access, or other Google API scopes.
Google may return profile claims, but the app discards them and retains only the
verified stable issuer/subject identity. It never links accounts by email.

1. In [Google Cloud Console](https://console.cloud.google.com/), select/create a
   project, then open **Google Auth platform**:
   - **Branding → Get Started** (if not configured): enter the app name, user
     support email and developer contact email; choose **External** audience for
     consumer Google accounts, review Google's policies, and create the setup.
     Complete Branding with the public homepage, privacy-policy/terms URLs and
     authorized domain you control as required for the intended launch. Do not
     choose Internal unless deliberately limiting access to your Workspace org.
   - **Audience**: start in **Testing**, and add your intended Google test accounts
     under **Test users** if shown. Google exempts basic sign-in-only scopes such
     as these from the usual test-user allowlist and seven-day authorization
     expiry rules: **Testing is not a private-access gate for this app**. Workspace
     policy or other account restrictions can still block sign-in. See Google's
     [audience rules](https://support.google.com/cloud/answer/15549945).
   - **Data Access → Add or remove scopes**: select only `openid` and
     `https://www.googleapis.com/auth/userinfo.profile` (the Console equivalent
     of the requested `profile` scope). Do not add email or other API scopes.
   - **Clients → Create client → Web application**: name it, add the exact
     **Authorized redirect URI** `https://YOUR-HOST/auth/callback`, and create.
     Match scheme, hostname, port and path exactly; no trailing slash or wildcard.
     Leave **Authorized JavaScript origins** empty: this is a server-side code
     flow, not a JavaScript sign-in SDK. No People API is needed.
   - Copy that web client's **Client ID** and **Client secret** into the private
     service variables below. Use a separate client/project for staging if needed;
     the application requires HTTPS even for a local hosted-mode rehearsal.
2. In a **Stripe sandbox/test account first**, create a product with one active,
   recurring USD **999 cents / year**, quantity one, Price. Configure the Stripe
   customer portal to allow payment-method updates and cancellation at period end,
   without plan/quantity switching. The app's no-card week is local access, not
   a Stripe trial. No Stripe trial, discounts or tax-address collection
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
   BOD_OIDC_CLIENT_ID=<Google web client ID, usually ending in .apps.googleusercontent.com>
   BOD_OIDC_CLIENT_SECRET=<Google web client secret>
   BOD_STRIPE_SECRET_KEY=<sandbox secret API key>
   BOD_STRIPE_PRICE_ID=<sandbox annual price ID>
   BOD_STRIPE_WEBHOOK_SECRET=<endpoint signing secret>
   BOD_STRIPE_LIVEMODE=false
   BOD_SUPPORT_URL=<optional HTTPS contact page or mailto:your-support-address>
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
   Apply ingress rate limits (including `/`, login and billing) at the reverse proxy.
   A fresh anonymous dashboard visit provisions a real bank file; repeated visits
   without cookies can consume storage. There is no application-level anonymous
   signup quota or automatic abandoned-bank cleanup in this change.
   Run as an unprivileged service user, with `umask 077` and a private persistent
   directory. Back up all hosted SQLite files together using SQLite-aware backups
   or while stopped; use local disk, not network-shared SQLite storage.
6. Visit `/`: the actual dashboard opens immediately with Sample Kid ($5 spending,
   $1 savings, $1 invested) and a dismissible welcome. There is no sign-in or
   payment screen first. Changes are real and persist in the same browser. A new
   family can use the bank for **7 days**, with no sign-up or payment method.
   **Save / sync** in the welcome or Dad controls optionally claims the whole bank
   through the configured sign-in provider; it keeps every kid, balance, bond,
   setting and history row, along with the original trial deadline.
   **Dad → Save / sync, subscription & sign out** shows anonymous trial status;
   **Dad → Subscription & sign out** for saved banks shows Subscribe, payment
   management and sign-out, without a separate toolbar billing link. The section
   stays collapsed until opened so it does not get in the way of Dad controls.
   At expiry, the bank opens a styled access page; data is retained. The parent
   can save/sign in, contact support or sign out even after expiry. Anonymous
   banks must be saved before Checkout; saved families can subscribe or refresh
   billing. There is no app-imposed deadline for saving an expired anonymous bank:
   it stays claimable while this browser retains its token and the server retains
   its session and bank data. Saving does not restart the free week.
   Choosing Subscribe opens Stripe Checkout for the paid **$9.99 USD/year** plan;
   it charges only after explicit confirmation, not automatically at trial end.
   Webhook reconciliation activates paid access. If delivery is delayed, the
   **Refresh subscription status** button reads Stripe directly. Manage payment
   methods and cancel in Stripe's portal.

Set `BOD_SUPPORT_URL` to the operator's actual HTTPS contact page or a plain
`mailto:` address, then restart through your normal deployment process. Do not
use the example placeholder as a destination. This link is visible in Dad controls
and the expired-access page, beside a selectable **Family ID**. Invalid schemes,
credentials and whitespace are rejected at startup. If omitted, no fake/dead link
is rendered: users are told to ask the person running this instance, and that a
contact link has not been configured. **Configure it before public onboarding**
so parents who cannot pay have a direct way to reach you. No support inbox,
outbound email service or owner's email is assumed.

Google's issuer defaults to `https://accounts.google.com`; no issuer variable is
needed. The existing `BOD_OIDC_*` names are intentionally retained, not an Auth0
dependency. For an already-configured conformant confidential OIDC provider,
setting `BOD_OIDC_ISSUER` explicitly preserves discovery, RS256, code + PKCE and
the existing `openid`-only request. This is a compatibility option, not another
required service. **Do not change an existing family's issuer to Google and expect
automatic linking**: a different issuer/subject is a different family. Back up and
plan an explicit identity migration instead; matching emails never transfers data.

Before a public launch, move the Google project's **Audience → Publishing status**
to **In production** and satisfy any branding/domain verification and policy
requirements Google displays. Basic sign-in scopes do not by themselves require
sensitive/restricted-scope verification, but a successful Testing login is not
production approval or a production security review.

Do not switch Stripe to live mode until a real Google/browser login and a real Stripe
sandbox purchase, renewal/failure and cancellation have been verified for the
actual deployment. None were performed as part of this local implementation.
Changing the configured issuer, client, Price or Stripe account is not a migration
procedure. Keep credentials/mode consistent and plan any account migration.

## Authorization and subscription policy

- Anonymous dashboard entry creates a random server-selected family ID and a
  separate 256-bit device capability. Only its SHA-256 digest is stored in the
  directory; the secret remains inside the server-side browser session. No
  capability appears in URLs, localStorage, HTML or WebSocket frames. HTTP and
  idle WebSockets check the capability and the shared trial/paid/free access policy.
  The trial ends after seven days; the capability has no separate recovery cutoff.
  Cookie clearing/eviction, browser switching, sign-out or loss of server session
  storage can lose access to an unclaimed bank; there is no anonymous recovery by
  family ID. Browsers may cap cookie persistence despite the requested persistent
  cookie. No bank data is automatically deleted, but retention depends on operator
  storage/backups; this is not a guarantee against data loss. Anonymous bank and
  session storage grows with use: provision disk capacity and ingress limits, and
  obtain an explicit retention policy before adding any cleanup deadline.
- Signup binds the guest family/capability digest to the browser-bound OIDC state.
  Only after verified callback claims does a writer-serialized transaction change
  that same family's owner and revoke the anonymous capability. The bank file is
  not copied/imported/reseeded, and trial dates are not reset. Failed or stolen
  callbacks cannot claim it. Competing distinct identities cannot both claim it.
  Signup never creates a Stripe customer or automatic payment.
- If that identity already owns another family, neither bank is changed. A simple
  explicit choice opens the existing family or keeps this device trial; no merge
  is implied. When opening the existing family, the separate anonymous capability
  remains in this session so Dad can return to the device trial. It is not synced
  or claimed by that existing account; saving it requires a different parent
  identity. Sign-out/session loss can still lose anonymous access.
- Authlib performs code exchange, PKCE, nonce and signed ID-token validation.
  Browser-bound state is consumed atomically in SQLite before exchange. Identity
  is the verified `(issuer, subject)`, never an email or request body family ID.
  Google accepts only its documented signed-token issuers
  `https://accounts.google.com` and `accounts.google.com`; both are stored as
  `https://accounts.google.com` for the same stable subject. Other configured
  issuers must match exactly. Authlib checks audience, nonce, expiry and signature;
  the app also checks authorized party and recent issuance.
- Flask-Session keeps opaque, Secure/HttpOnly/SameSite=Lax cookies backed by
  server-side SQLite sessions. Verified callbacks rotate the session ID and CSRF
  token at the authentication boundary; login startup keeps the guest session
  usable while the provider responds, avoiding socket/refresh-triggered second
  logins. A stale HTTP 401 rechecks the current cookie before starting OIDC and
  reloads the document/CSRF if another tab has completed sign-in; writes are never
  automatically replayed. Local sign-out deletes the server session. Authenticated
  sessions expire after seven days; anonymous server sessions have no recovery
  deadline and request a persistent browser cookie. Legacy retained guest tokens
  also ignore the former recovery cutoff. Local sign-out does **not**
  sign out of the identity provider, so its next login may reuse its SSO session.
  Provider logout/revocation does not automatically revoke existing app sessions.
- All bank pages/APIs require a valid parent session or device capability and a current entitlement.
  Existing routes run against the server-selected family's own SQLite database,
  including settings, PIN, ledger and history. Browser-supplied kid/transaction IDs
  are only local IDs in that family's database. Hosted mutations take a SQLite
  writer lock before their domain reads to avoid concurrent lost balance updates.
- The parent login replaces the local Dad PIN in hosted mode. There are **no child
  logins/restricted devices, co-parent invitations, general family switching or membership
  management** in this initial slice. Anyone using a signed-in parent browser can
  use every control, including spend, gifts, editing and demo fast-forward. An
  anonymous device has the same full controls for its own bank. Do not
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
  **paid** access, until the item period end (capped by an earlier cancellation time).
  Cancel-at-period-end retains already-paid access; canceled, past_due, unpaid,
  incomplete and Stripe `trialing` do not grant paid access. Independently, a
  family has access during its app trial or while an operator free-access grant
  is active. Each new family gets exactly 604800 seconds from first creation,
  persisted in server-owned `trial_started`/`trial_until`. Sign-in, refresh,
  checkout and webhooks never reset the trial or free grant. No Stripe customer
  or Checkout is created during signup. No payment information is required.
  The ordinary parent session also lasts seven days; if it expires, signing back
  in restores that same family's original trial/subscription, never a new trial.
  The real server UTC clock controls expiry (keep it synchronized).
- **Safe upgrade:** on first startup of this version, existing pre-trial families
  receive a one-time seven-day migration-time grace week because the old schema
  has no creation date. Schema changes and backfill are one SQLite transaction,
  under a writer lock; restarting does not reset dates. Existing `paid_until`,
  customer/Checkout mappings, identities and every family bank file are preserved.
  A valid paid subscription takes precedence in the UI and remains valid.
  Back up the hosted directory before an authorized production upgrade, as usual.
  This is a forward migration, not permission to reset or copy a live database.
- Expiry is checked server-side for every bank request and idle/open WebSockets.
  After expiry without paid/free access, bank reads and writes return 402 (the
  dashboard redirects to `/billing`). Family data is **never deleted**, and billing,
  saving/sign-in, contact, status refresh and sign-out remain available. Free self-hosting has no trial,
  directory migration, payment gate or hosted configuration requirement.
- `/api/updates` is also session/entitlement protected, with exact configured
  public Origin and Host validation before upgrade. Its channel is selected from
  the authorized family's server-owned ID, never a request parameter. Only
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

## Operator-only free access

There is **no HTTP/admin grant endpoint** and no client-supplied entitlement.
Run `hosted_access.py` as the trusted service user/operator who owns the private
hosted directory. Filesystem access to that directory is the authorization
boundary: keep it private (`umask 077`, owner-only directory), do not expose this
CLI through a web shell, and do not give support requesters shell/DB access. The
CLI is standard-library-only; it needs no Stripe/OIDC credentials or app import.
It requires an explicit directory, opens only an existing `directory.sqlite`,
does not migrate it and refuses unknown or malformed family IDs.

Exact flow (replace directory and `FAMILY_ID` with verified actual values):

```sh
# 1. Ask the parent for the Family ID shown under Subscription & sign out or
#    on /billing; verify their request through your trusted support channel.
python hosted_access.py --data /absolute/private/hosted list
python hosted_access.py --data /absolute/private/hosted show FAMILY_ID
# 2. Check the exact ID and stored verified issuer/subject against the intended
#    family. Email/name is not an identity key. If uncertain, do not grant.
python hosted_access.py --data /absolute/private/hosted grant FAMILY_ID --reason 'Support request: no-cost access approved'
# 3. Read back the exact family: free_access must be 1. Parent reloads /billing
#    and opens their existing bank; no new signup or restart is needed.
python hosted_access.py --data /absolute/private/hosted show FAMILY_ID
# 4. To revoke only the override, retaining paid access and any unexpired trial:
python hosted_access.py --data /absolute/private/hosted revoke FAMILY_ID --reason 'Free access arrangement ended'
python hosted_access.py --data /absolute/private/hosted show FAMILY_ID
```

Grants are durable and indefinite until revoked. Each grant/revoke and required
reason is recorded atomically with the flag in `access_audit`, with family ID,
operator OS UID and server timestamp. Keep audit reasons short and nonsensitive.
The CLI outputs the stored row only after commit. Repeated grant/revoke operations
are safe (each adds an audit entry). Revocation takes effect on the next bank
request and the next socket recheck (normally within 0.5 seconds idle); it does
not delete bank data, erase trial dates or cancel a paid subscription. Conversely,
**granting free access does not cancel/stop Stripe renewal**: an already-paying
parent must manage/cancel that subscription in Stripe separately. Webhook
reconciliation cannot revoke a free grant. Account management remains accessible.

## Actual retained data / boundaries

The operator stores provider issuer/subject IDs, a random family ID, Stripe customer
and Checkout identifiers/URL/expiry, entitlement expiry, trial dates, free-access
flag and operator audit notes/UID/timestamps, event IDs/payload
fingerprints, browser sessions and short-lived
login state, plus the actual family bank data: children’s display names/emojis,
balances, allowances/rates, investments, spending/gift notes and history. Stripe
collects payment/billing details in hosted pages; the app neither requests nor
stores card details, billing addresses, dates of birth or child email addresses.
Google's required additional `profile` scope may return parent name/picture claims;
these are transiently received during validation, not persisted in the directory,
family database or app session. Parent email is not requested or stored. Access
and ID tokens are discarded after callback validation, not retained for API use.
The identity provider may collect parent email and recovery information.
Infrastructure can retain access logs/IP addresses; configure logging
and retention intentionally, avoid callback query-string logging, and do not enable
Authlib/Stripe debug logging with real credentials or people.

Use `flask --app app session_cleanup` periodically to delete expired SQLAlchemy
sessions. Expired login states are removed on the next login. Webhook fingerprints
and family bank data currently have no automated deletion workflow; operators must
provide a reviewed account deletion/retention process before a public launch. No
analytics, marketing identifiers or emails are added. This is data minimization,
**not zero data**, and not a compliance/security certification.

## Verification

See [anonymous signup lifecycle verification](docs/anonymous-signup-verification.md)
for the correction's regression evidence, exact commands and tested boundaries.

```sh
# Free mode: base runtime + test dependencies only, no hosted credentials required.
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
# Hosted tests: install optional test tools in a disposable environment.
python -m pip install -r requirements-hosted.txt -r requirements-test.txt pytest
python -m pytest tests_hosted/test_hosted.py tests_hosted/test_guest.py tests_hosted/test_trial_access.py tests_hosted/test_duplicate_subscription.py tests_hosted/test_hosted_realtime.py -q
# Actual Chromium UI and hosted WSS isolation/reconnect tests (system Chromium).
python -m pip install playwright
BOD_TEST_CHROMIUM=/usr/bin/chromium python -m pytest tests_hosted -q
# Free-mode real browser tests (no hosted dependencies required).
BANK_OF_DAD_BROWSER_TESTS=1 BOD_TEST_CHROMIUM=/usr/bin/chromium python -m unittest discover -s tests -v
```

Hosted tests use synthetic identities and balances, real Authlib/JWKS/PKCE HTTP
requests to a disposable local fixture with Google's required `openid profile`
scope and documented issuer claims (plus a generic OIDC compatibility regression),
the real Stripe SDK against a local HTTP
fixture, and SDK-signed webhook inputs. The real network tests connect two devices
in family A and an independent family B, assert post-commit A-only notifications,
reconnect resync, anonymous/unpaid/origin denial, and open-socket logout/session/
entitlement expiry. The HTTP-backend test uses the configured HTTPS public Origin
and preserved Host, modeling TLS termination without trusting forwarded headers.
Chromium also exercises the actual hosted UI with polling disabled, family-isolated
push, reconnect, anonymous socket denial and entitlement expiry. Free-mode browser
tests separately exercise the PIN, animations, input preservation and polling
fallback, and can run without any hosted runtime dependencies.

Anonymous tests cover real sample provisioning, mutations/reload, cross-browser
isolation, exact full-bank preservation through signup, a fixed seven-day trial
without a guest recovery cutoff, failed/stolen/replayed callback state, racing distinct claimants,
existing-family collision/return, CSRF and idle socket revocation. Chromium at
390px and 1280px exercises the real welcome/dashboard, editing, reload, guest WSS
push, optional signup, collision, browser-cookie loss, anonymous expiry/contact,
signup after day 30 and the existing subscription route. Delayed real Authlib startup
with live sockets, an in-flight mutation, ordinary tab refresh and a stale revoked-SID
401 must still produce one login and preserve the exact edited bank. HTTP tests
claim retained tokens at ages 31, 365 and 3650 days, including legacy session
storage across restart/cleanup, without extending the trial. Providers remain controlled
local fixtures; the dashboard and bank operations are not mocked.

These tests do not configure or contact a real Stripe or identity-provider account.
Trial tests exercise actual signup without Checkout, fixed expiry across sign-in,
boundary denial, cross-family HTTP and real sockets, local CLI grant/revoke and
durable auditing, webhook independence, and preservation of paid access. Chromium
exercises onboarding, Dad account controls, idle expiry, retained-bank restoration
and sign-out at 390/768/1280px. Set `BOD_TEST_ARTIFACTS` to a disposable directory
to retain screenshots of those real rendered pages. Support URLs in tests are
labeled examples only; they are not an actual owner's support destination.
Do not mistake those fixtures for provider acceptance, a production security review,
or a verified deployment/proxy configuration.

References: [Authlib Flask OIDC](https://docs.authlib.org/en/latest/oauth2/client/web/flask.html),
[Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect),
[Google OAuth production readiness](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance),
[Stripe subscription webhooks](https://docs.stripe.com/billing/subscriptions/webhooks),
[Stripe customer portal](https://docs.stripe.com/customer-management/integrate-customer-portal).
