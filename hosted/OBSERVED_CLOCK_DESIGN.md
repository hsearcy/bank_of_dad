# Recovery observed-clock investigation (design only)

**Status: not implemented; v2 grant expiry remains unsafe on clock rollback.**
This document is not a migration, an authorization boundary, or evidence that a
future design is safe. Keep the synthetic recovery core out of environments that
require reliable expiry. The existing `test_expiry_can_reverse_on_clock_rollback`
intentionally demonstrates the bug, including reconstruction of the auth object.

## Scope and discovered boundary

`RecoveryAuthorization` is synchronous and internal. In addition to its own
`pending`, `outcome`, `acknowledge`, and `mutate`, it inherits `read`, `revoke`,
`set_member`, and `issue_grant_privileged`. The latter samples time without
calling `_auth`; protecting `_auth` alone misses grant issuance. Bootstrap has
no grant but must initialize any new clock row atomically. All these paths must
run through one per-family boundary; `FamilyService`, v1 authorization, direct
storage, and privileged or alternate paths must not be reachable for that family.
The current experimental `FamilyBank` uses v1, while v2 is exercised only by
synthetic DOs. No real identity, routing or production v2 gateway exists.

The existing `FamilyService._transaction` calls synchronous SQLite
`transactionSync`; `_auth` and other error handlers poison the active transaction.
Updating the watermark in the action transaction loses the update on denied auth,
validation error, and injected bank faults. A separate successful logical
transaction followed synchronously by an action transaction avoids that *logical*
rollback, but `transactionSync` returning is not an explicit disk-flush barrier.
Cloudflare documents `sync()` as the async pending-write disk barrier, output gates
as delaying outgoing messages until writes persist, and synchronous write
coalescing. These do not by themselves demonstrate that a separately committed
watermark is durable **before the subsequent action executes**, or establish the
precise behavior of Python RPC exceptions and a later failed transaction for all
relevant crash/failure windows. An output gate protects externally observed
responses; it is not proof of an earlier physical durability point before an
internal action. Conversely `await sync()` introduces a possible scheduling point:
without an enforced family-wide exclusion or correct fencing/revalidation, a
second request may observe newer time and complete before the first request
resumes and acts on its older sample. Do not infer a safe implementation from
SQLite unit locks or from the passing existing local smoke tests.

References: [SQLite storage API](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
(`transactionSync`, `sync`, output gate); [DO rules](https://developers.cloudflare.com/durable-objects/best-practices/rules-of-durable-objects/)
(input/output gates, coalescing); [DO glossary](https://developers.cloudflare.com/durable-objects/reference/glossary/)
(output gate). The documentation describes runtime behavior; it is not a test of
this repository's Python/JS RPC failure paths. Direct retrieval returned HTTP 403
in this environment; the relevant indexed excerpts were retrieved by web search.

## Candidate protocol, prerequisites, and decision gates

1. Choose and verify a runtime-supported way to serialize **every** entry point on
   the same actual family DO across the durable barrier and final transaction,
   including all instances/callbacks, errors, reentrancy, cancellation and RPC
   retries. Alternatively prove a fencing protocol that revalidates/resamples after
   any await, and that no bypass exists. Do not expose a public timestamp parameter;
   production samples only a server-controlled trusted source. Test-only injection
   must be isolated from any deployable ingress. An ordinary lock around each
   individual transaction is insufficient across two transactions.
2. While serialized, validate family and the exact supported fresh v2 catalog;
   sample a finite, non-boolean, bounded trusted clock value, and synchronously
   commit `max(previous, sampled)` in a separate transaction. Invalid/unavailable
   clocks, missing/duplicate/malformed rows, malformed schema and out-of-range
   values fail closed. If the sample is below the high-water, fail closed rather
   than extending grants; never use the older sample as authorization time. Use
   integers and explicit safe upper bounds on all expiry arithmetic and any
   optional generation counter; reject before overflow or unsafe JS conversion.
   Establish whether rounding subsecond samples is acceptable to the expiry
   contract. An equal integer second is not proof the raw clock did not move back
   within that second.
3. Establish and verify the durable boundary before allowing an action, including
   failure of the watermark transaction, failure/ambiguity of the disk barrier,
   action rollback after the barrier, a crash in each interval, and an externally
   observable auth denial. Then perform grant expiry checks, membership checks,
   grant creation/revocation or protected action atomically on the same family DO,
   without awaits in the final check/action transaction. Any uncertainty blocks
   action; a failure after the watermark commit may leave only the watermark, which
   is intentional. The same sampled/validated value must be used consistently.
   Denial or faults must not undo the separately committed observation.
4. Add the clock table and exact constraints to the *fresh* v2 schema and strict
   catalog validation, with atomic bootstrap. Old v2 and v1 schemas must be
   rejected, never silently upgraded. Define and test fresh initialization and
   failed or partial initialization; no automatic migration. A future migration
   requires separate approval and proof, not a guessed starting watermark.

The unresolved runtime/scheduling proof makes implementation premature. In
particular, neither a synchronous call's return value nor successful local tests
alone prove crash durability or the needed cross-RPC exclusion. A future spike
must first demonstrate the barrier and controlled interleavings with actual local
Python DO and JS/Python RPC, then document remaining production assumptions and
obtain independent review. A genuine storage fault/crash at the platform boundary
may require provider-supported fault injection or contractual confirmation; do not
claim such proof from a graceful local restart.

## Fail-safe behavior and operations

A large valid but erroneous future sample can irreversibly advance the watermark
and deny service, potentially for years. Bound representation and arithmetic;
do not silently clamp, reset or discard the observation to improve availability.
Outlier detection needs an independently trustworthy reference and a reviewed
policy: if confidence is absent, quarantine before issuing grants or performing
protected actions. A large forward jump and later rollback must never silently
restore use of earlier grants. Production recovery would require an authenticated,
audited offline process: quarantine the family, independently establish correct
time and data history, revoke/fence all possibly affected grants, reconcile
pending operations and restore history, and explicitly approve any new epoch or
watermark reset. This procedure is not implemented; simply deleting/resetting a
clock row or waiting without assessing compromised time is unsafe.

The guarantee, if built, covers only observations committed in the current family
history. It cannot detect elapsed time or forward/backward movement between
samples, a wrong clock on initial bootstrap/cold start, storage tampering, or a
rollback/restore of the family database to an older watermark. A newer backup is
not proof that no later observation or operation existed. Restore needs a trusted
external anti-rollback anchor or explicit quarantine, revocation and reconciliation;
restoring only local state cannot supply that guarantee. Ordinary eviction/restart
is distinct from backup/PITR rollback. Compromised privileged code and alternate
writes remain outside this mechanism. Cross-family state must never share a
watermark, and untrusted requests must not select a different family's storage.

## Required proof before implementation can be called complete

Use disposable synthetic local DOs and controlled barriers (not sleep-based
ordering): valid monotonic operations; accepted at 1009 then denied at 1010 and
still denied at 1009 after actual DO restart; all inherited and recovery read,
write, ack, membership, revocation and privileged grant paths; independent
families; invalid/unavailable/nonfinite clock and bounds; future jump/rollback;
failure before and after watermark and each bank/ownership/recovery write;
ambiguous barrier and action failure; forced older/newer overlapping async RPCs
in both orders; rollback and restart snapshots; exact fresh-catalog rejection of
old/incomplete v2 and v1 without migration. Test both JS-to-Python and
Python-to-Python boundaries as applicable, with no untrusted test time in a
production interface. Distinguish local-runtime evidence from actual platform
crash, restore, deployed identity and security guarantees.
