# Public source release and portable configuration

This public snapshot contains the application from deployed source commit
`574d423bd1a50fbc523055e08ea238379f502517`, including the saved-family owner-panel
correction. It is published as one new commit on public `master`, without importing
the prepublication Cloudflare branch history.

## Source parity and intentional differences

Application Python modules, templates, static assets, test fixtures, tests,
dependency locks and build tooling are byte-for-byte identical to that source.
Only these publication files intentionally differ:

- `README.md`: current Cloudflare status and free self-hosted instructions.
- `cloudflare/wrangler.production.jsonc`: no Cloudflare account ID; example
  domain/origin/support destination instead of the production operator's settings.
- `cloudflare/README.md`, `OAUTH.md`, `LIVE-BILLING.md`,
  `OWNER-AND-LAUNCH.md`, `PUBLIC-HOSTING.md`: portable operator instructions and
  clear separation of historical implementation reports from current source status.
- `docs/welcome-tour-verification.md`: portable virtualenv path.
- This publication note.

No live keys, private owner identities, databases, deployment secrets, local
Wrangler state or customer/family records are included. Synthetic credentials and
identities in tests are deliberate local fixtures, not usable service credentials.
A bounded source/history privacy scan reduces risk; it is not proof of absence.
The public product domain, support address and GitHub project links in the app are
intentional product copy, not private service endpoints.

## Local and free self-hosted use

The repository-root `./run.sh` remains free, loopback-only by default and independent
of Cloudflare, Google and Stripe credentials. See the root README's security limits;
the Dad PIN is not public-network authentication.

For the Cloudflare local target, follow [README.md](README.md). Tests use disposable
storage, loopback providers and synthetic identities. Do not use real family data.

## Deploying your own instance

1. Copy `wrangler.production.jsonc` to an ignored private operator configuration
   outside Git. Keep build/static paths relative to `cloudflare/`, or adjust them
   if placing that file elsewhere. Do not commit the resulting operator file.
2. Select your own Cloudflare account privately (for example through
   `CLOUDFLARE_ACCOUNT_ID`), choose your Worker name and domain, and replace every
   `bank.example.com` / `support@example.com` placeholder. Verify the sample
   rate-limit namespace IDs do not collide with another Worker on your account.
3. Register your own Google web client with the exact HTTPS callback and install
   credentials through the provider secret store, not source or shell arguments.
   Configure the private verified issuer/subject owner allowlist only after login.
   Never use an example identity as an owner or an email as the Google subject.
4. Defaults are deliberately fail-closed: `BOD_PUBLIC_PILOT=false` and
   `BOD_BILLING_MODE=unavailable`. Explicitly choose the no-payment pilot or follow
   [LIVE-BILLING.md](LIVE-BILLING.md) for the separate live mode. Configure your own
   account/product/annual price, portal and endpoint-specific signing secret;
   never reuse fixture keys or enable test-provider overrides publicly.
5. Independently verify login, cookies, cross-family authorization, billing delivery,
   quotas, support and privacy/recovery handling for your own deployment. Publishing
   this source neither deploys it nor authorizes charges, plan upgrades or changes
   to an existing service. Secret writes may themselves deploy; use a separately
   approved staged workflow.

## Evidence boundaries and remaining operational work

The focused documents preserve historical local-runtime measurements and their
limitations. Their old “not deployed” or “real login unverified” statements describe
those implementation runs, not the present hosted service. This source publication
does not access production data or independently retest real-provider transactions.
Public repository parity is not a production security certification.

[RECOVERY-AND-PRIVACY.md](RECOVERY-AND-PRIVACY.md) describes the remaining private
recovery/deletion tooling, maintenance fence and hosted restore/deletion drills.
Trial expiry is not data deletion; a free-access grant does not cancel a subscription.
No automatic abandoned-family purge or independent off-platform export is added.
