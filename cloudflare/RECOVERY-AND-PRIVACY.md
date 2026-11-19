# Recovery and privacy: minimal operator handoff

## Status and scope

Source reviewed: `df8c490`; Cloudflare documentation checked 2026-11-19.
This is a technical runbook and proposed disclosure, **not a deployed recovery
service, tested restore, legal policy, or promise of zero data loss**. No provider
writes or real family-data reads were performed for this review.

The smallest practical path is to use native SQLite Durable Object point-in-time
recovery (PITR), keep the shared identity directory current, and reconcile one
restored family with its current identity and Stripe customer. An independent
export system is not a prerequisite for investigating this path, but PITR is not
an off-platform backup or protection against loss of the account/namespace.
**The present app has no operator PITR/deletion interface.** This document does
not turn that missing interface into a working command. Do not expose arbitrary
SQL, generic RPC dispatch, restore, or delete operations on public ingress.

## What Cloudflare provides (and what it does not)

- `FamilyBank` and `Directory` are configured with `new_sqlite_classes` in both
  Wrangler configurations. SQLite Durable Objects and their storage API are
  available on Workers Free and Paid. The DO PITR documentation specifies the
  **past 30 days**; do not substitute D1's Free-plan seven-day Time Travel limit.
- PITR addresses the **whole individual object's SQL and key-value storage**.
  It is not a single-table restore, a cross-object transaction, a Stripe rollback,
  or a Worker-code rollback. A bookmark is a restore position, not an exported
  backup; saving one does not extend the documented recovery window.
- Important state here is in DO SQLite, not only instance memory. Hibernation,
  idle eviction and ordinary object restart discard memory, not committed storage.
  No family-data TTL or automatic abandoned/expired-trial purge was found in this
  source. Trial expiry denies access; it does not delete the bank. This is not a
  guarantee against bugs, explicit deletion or provider/account failure.
- `await ctx.storage.getCurrentBookmark()` returns the current position.
  `await ctx.storage.getBookmarkForTime(timestamp)` resolves approximately the
  requested time within the last 30 days. Numeric timestamps are **epoch
  milliseconds**, not Python `time.time()` seconds.
- `await ctx.storage.onNextSessionRestoreBookmark(bookmark)` schedules restore
  on the next object restart and returns an **undo bookmark** for the state just
  before recovery. Capture that result outside the target object before invoking
  `ctx.abort()` to restart it. An abort is not an ordinary success response;
  verify through a new object session. Scheduling and aborting in one RPC without
  external capture can lose the undo response. Once scheduled, even an unexpected
  restart may apply it: keep the target fenced throughout.
- PITR is **not supported by local development**. Passing local SQLite tests or
  detecting that a method exists cannot establish hosted restore behavior.
- The published pricing table has no separately listed PITR/history surcharge;
  the reviewed DO docs do not explicitly promise a zero-cost restore. Ordinary
  Worker/DO requests, duration, storage reads/writes and stored data still have
  quotas/pricing. Free-plan exhaustion fails operations; it is not documented as
  automatic deletion. This review did not establish the account's current usage
  or billing plan and did not purchase one. Do not market this as unlimited free
  backup. Current Free allowances include 5 GB total SQL storage, 5 million rows
  read/day and 100,000 rows written/day; recheck before deployment.
- Storage/PITR calls execute **inside the owning DO** using `ctx.storage`, not
  with a D1 command. No stand-alone DO restore REST/Wrangler workflow was
  established in this review. Listing namespaces/objects is not restoring them.
  Shipping a private operator adapter requires a separately reviewed deployment.
  Current Workers permission docs require Editor on an existing Worker to deploy
  code; changing its route also requires zone Workers Routes Write. Runtime
  access additionally requires the correct namespace binding and operator
  authentication/authorization. App `/owner` free-access grants do not confer
  PITR, arbitrary SQL or deletion authority. No new account token was requested.

## Exact state and identity to preserve

| Location | Stored data / recovery implications |
| --- | --- |
| `FAMILIES`, class `FamilyBank`, `idFromName(family_id)` | `family_id` is 32 lowercase hex characters, **not** the Cloudflare object ID and **not** a bearer capability. Bank tables: `kids`, `bonds`, `transactions`, `history`, `settings` (including optional Dad PIN hash). Family metadata `_bod_family` contains trial/paid/free status, guest capability hash, CSRF, customer and claimed issuer/subject. `_bod_sessions` is family-local session authority. `_bod_billing`, `_bod_stripe_events`, `_bod_billing_audit`, `_bod_access_audit` retain billing/receipt/access state when initialized. |
| `DIRECTORY`, class `Directory`, `idFromName('google-v1')` | Shared across all families: `identities` maps canonical Google issuer/subject to family; `sessions`, `login_states`, `choices`, `device_returns` include session/temporary flow state (some rows contain device bearer tokens). `owner_families` is discovery only. `stripe_customers` maps scope-qualified customer to family; `stripe_events` tracks scope-qualified delivery receipt/done state. |
| Google | Login is canonical issuer `https://accounts.google.com` plus verified subject, **not email**. Losing the guest browser cookie is loss of its access capability; a Family ID alone cannot recover authority. Changing issuer/subject or creating a new family is not account recovery. |
| Stripe | Customer, subscriptions, invoices/payments and hosted Checkout/portal are external. DO restoration cannot undo charges, refunds, cancellations or subscriptions. Billing scope includes mode/account/product/price; cached paid policy must remain `invoice-v1`. |
| Browser / platform | HTTP-only cookies carry session/device capabilities and temporary login/choice identifiers. Chart preferences use local storage. Hosted service worker clears Cache Storage on activation and has no offline fetch handler. Cloudflare request/observability logs and support correspondence are separate stores, not covered by a family restore. Their configured retention was not audited here. |

The source has no family export/import endpoint for this Worker. Do not point
self-hosted file-backup tooling at a hosted DO or copy production data into tests.

## Targeted family recovery procedure (requires private operator tooling)

Use this for an accidental family-local write/corruption with the **current
Directory and family identity association still trustworthy**. If that condition
is false, stop and scope an incident-specific directory repair. Do not rewind the
shared Directory to fix one family's ledger: that also rewinds everyone else's
identity, flow/session and Stripe receipt state. Matching timestamps across DOs
do not create an atomic snapshot.

1. **Authorize and identify.** Verify the parent's control of the existing saved
   account (or existing guest capability), incident and intended restore time.
   Record exact account, environment, Worker version, namespace/class, Family ID,
   derived Cloudflare object ID, UTC incident time and expected lost changes.
   Never request a full browser cookie through email/chat. Family ID or email
   alone is not proof of ownership. Stop if no defensible identity is available.
2. **Fence traffic before reading or scheduling.** Block all target-family bank,
   auth claim/session, owner grant and billing mutation paths; close its sockets
   and prevent alarms from changing state during recovery. Pause/queue relevant
   webhook processing without acknowledging unapplied events as successful. The
   current code has no per-family maintenance fence. A reviewed whole-service
   maintenance window is a simpler alternative for a small launch; it must also
   fence DO alarms and in-flight work, not just show a maintenance page. Do not
   assume `BOD_PUBLIC_PILOT=false` is such a fence when live billing is enabled.
3. **Capture current evidence privately.** Read current exact family metadata,
   Directory identity/customer associations and relevant access audit decisions.
   Record current family bookmark, chosen target bookmark and approved reason
   outside that family DO. Keep the current directory bookmark as incident
   evidence, **not** an instruction to restore it. Do not log child data or bearer
   tokens. Confirm the restore point is after the intended family was created.
4. **Schedule, save undo, restart.** In the target object's private operator
   method, await `getBookmarkForTime(epoch_ms)` (or use a verified existing
   bookmark), then await `onNextSessionRestoreBookmark(target)`. Durably capture
   the returned undo bookmark outside the target; in a separate controlled call
   invoke its `ctx.abort()`. If delivery is ambiguous, inspect before repeating;
   don't blindly schedule again. Reconnect privately and read the restored state.
5. **Reconcile before reopening.** Compare the restored `_bod_family.id`, issuer/
   subject and customer with the captured current Directory association. Never
   accidentally turn a claimed bank back into a guest bank: old guest hashes and
   revoked sessions can be resurrected. In a reviewed family transaction restore
   the verified *current* claimed identity, keep guest authority disabled, clear
   `_bod_sessions`, rotate CSRF and remove that family's Directory sessions and
   pending auth/choice/device-return flows. Those flow tables can reference bearer
   tokens without a direct family column; inspect all relevant references, not
   just `WHERE family=?`. For an unsaved guest, capability rotation/delivery needs
   a separately verified possession path; do not send an old capability to someone
   who only knows the Family ID. Reapply current explicit free-access decisions
   from trustworthy audit evidence; do not restart the trial or silently revive a
   revoked grant. Any required row repair needs a reviewed operator adapter: it
   is **not** available in `/owner` today.
6. **Reconcile billing with today's provider state.** Verify exact Stripe mode/
   account/product/price scope and current customer from the saved evidence; if a
   restore predates customer creation, repair that association and the Directory
   mapping before allowing billing. Never create a replacement customer/Checkout
   to guess the old relationship. Use the existing family `billing` operation
   `refresh` with fresh authenticated parent authority after identity repair (or
   an equivalently reviewed private reconcile adapter). It reads current provider
   subscriptions and paid invoice settlement, commits entitlement/audit and arms
   an alarm without creating Checkout/portal/customer. This performs local state
   writes and requires separate incident authorization. Do not rely solely on old
   webhook replay: the unchanged Directory may already mark an event `done` even
   when its family-local receipt/entitlement was rewound. Keep pending receipts
   retryable until actual family reconciliation succeeds.
7. **Verify and release.** Privately verify expected children, balances, bonds,
   ledger/history and settings; check all financial dates as well as amounts.
   Confirm the correct identity/customer and trial/paid/free entitlement; old
   capabilities/sessions must fail and fresh Google sign-in must reach only this
   family. Rearm the native entitlement/session alarm, reconnect the real UI and
   ensure queued webhook work reconciles. Record before/target/undo bookmarks,
   actual lost changes, provider reconciliation time and verification outcome.
   Remove the fence only after readback; a successful RPC alone is not recovery.
   If wrong, stay fenced and consider the captured undo bookmark within its
   supported window; then repeat identity/billing checks. Undo also rewinds
   repairs made after restore, so it is not a blind rollback button.

A family restore intentionally discards changes newer than its target. No
cross-object consistency or zero-data-loss guarantee is established here.

## Deletion requests and truthful disclosure

**Proposed short copy, pending owner review and public placement:**

> Bank of Dad stores the names or nicknames you enter for children, their bank
> balances and activity, and family settings. For saved banks we also store a
> Google account identifier, sign-in/session information, trial/access status and
> Stripe customer/billing references. Google handles sign-in and Stripe handles
> hosted payment details; the app does not collect card numbers. Cloudflare hosts
> the app and its databases. Your browser stores access cookies and preferences.
> Trial expiry, signing out or clearing browser data does not delete the stored
> bank. Unsaved device-bank access can be lost if its browser cookie is removed;
> a Family ID by itself is not an access credential. For privacy questions or to
> request deletion, contact support@thebankofdad.app. We need to verify control of
> the family before acting. Recovery history and provider records are separate
> from the active bank; we cannot promise instant removal from every copy.

This draft is deliberately not a full legal/privacy policy. It makes no company,
tax, response-deadline, automatic-erasure or precise provider-erasure commitment.
The configured support link exists; delivery/handling and owner approval of the
copy remain unverified. Do not represent deletion requests as automated.

For a verified deletion request, first record the approved scope, any continuing
subscription and the owner's decision on provider records. Deleting app data or
revoking free access **does not cancel Stripe renewal**. Agree cancellation with
the parent separately. Under a maintenance fence, remove the target's identity,
registry, customer mapping and all associated session/temporary-flow references
from the shared Directory; do **not** call `deleteAll()` on that shared object.
For complete target FamilyBank storage removal, Cloudflare documents
`await ctx.storage.deleteAlarm()` then `await ctx.storage.deleteAll()`; deleting
rows or dropping tables alone leaves internal storage metadata. No deletion RPC
is implemented here, and recreation by in-flight requests must be prevented.
Read back exact active-state absence and authorization denial before reporting
that active app data was removed. Handle support/log/provider copies separately.

PITR's 30-day *recoverability window* is not proof of physical erasure by day 30.
The reviewed docs do not establish residual-copy erasure timing or whether a
particular deletion leaves recoverable history. Do not promise either immediate
irrecoverability or guaranteed post-deletion restore. Keep a minimal restricted
record of honored deletions outside any restored object so an incident restore
does not silently reactivate deleted data; define its scope/retention with owner
approval rather than inventing it here.

## Smallest remaining launch acceptance

- Owner accepts the bounded native-recovery/no-independent-export tradeoff and
  decides support handling, privacy copy/public placement and deletion process.
- Authorize and implement a **private**, exact-object recovery/deletion adapter
  and a real maintenance fence, rather than expanding the public owner panel.
- After deployment authorization, rehearse on a **disposable hosted family**:
  write known state, capture bookmark, change state, restore, prove exact readback,
  undo, clear sessions and verify identity, then refresh sandbox billing and prove
  Directory receipt handling. Rehearse targeted deletion with an unrelated
  disposable family unchanged. Local workerd cannot prove PITR.
- Record exact deployed version, real account plan/quotas, successful support
  receipt and drill evidence before claiming this operational gate is accepted.

No new infrastructure purchase, scheduled export system, generic admin console
or real-family recovery was introduced by this handoff.

### Verification of this documentation change

A scratch-only Python probe parsed constant schema declarations from the reviewed
source, created their tables in an in-memory SQLite database and checked that all
of those table names appear in this inventory. It also checked both configured
SQLite class names, the actual object-routing expressions, configured support
address, absence of restore/delete methods on the Worker, hosted service-worker
behavior and local Markdown link targets. All checks and `git diff --check`
passed. Nested runtime-created tables were inspected in source separately.
This is source/inventory verification, **not a hosted recovery or deletion drill**;
no application runtime suite was rerun for these documentation-only changes.

## Primary references

- [SQLite storage API / PITR](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#pitr-point-in-time-recovery-api)
- [Storage access / removal](https://developers.cloudflare.com/durable-objects/best-practices/access-durable-objects-storage/)
- [DO lifecycle](https://developers.cloudflare.com/durable-objects/concepts/durable-object-lifecycle/)
- [DO pricing and Free allowances](https://developers.cloudflare.com/durable-objects/platform/pricing/)
- [Workers roles and permissions](https://developers.cloudflare.com/workers/authorization/workers/)

The docs extraction tool was unavailable and direct docs requests returned 403;
primary content was read from Cloudflare's public `cloudflare-docs` `production`
branch (corresponding `src/content/docs/durable-objects/` files and pricing/plan
partials) and indexed official documentation. No D1 pricing or community claim
was used as proof of DO restore pricing or erasure semantics.
