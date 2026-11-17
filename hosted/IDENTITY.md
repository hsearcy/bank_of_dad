# Internal identity directory foundation — not deployable

`FamilyDirectory` has no HTTP handler or deployment configuration. It is intended
as **one** globally named SQLite-backed Durable Object, separate from each family
bank object. Every caller of the binding must be trusted application code; do
not expose these methods directly, including `create_family`, `issue_session`,
or `accept_invite`. This code does not authenticate a principal. The exact
`{'issuer': ..., 'subject': ...}` pair must be constructed exclusively by a
future OIDC verifier after signature, issuer, audience, expiry, nonce/state and
other applicable protocol checks. The issuer and subject are exact,
case-sensitive strings; never use email, normalize identifiers, or trust a
client-supplied assertion. Provider selection, linking, recovery and OIDC
verification are not implemented. A caller able to forge this assertion can
impersonate anyone. Malformed assertions are rejected, but validation here
is not verification.

A verified identity may create a family and receive an owner membership and
session in one transaction. Opaque family IDs are random 128-bit values.
Owners may create a bearer invitation for a parent, valid for at most one day;
a verified identity can redeem it once, atomically creating its membership and
session. Invitations are not identity-bound, can be stolen, are not pairing
codes, and must only be delivered through a future secure channel. Only parent
and owner roles are supported; only owners invite, neither can invite an owner.
There is currently no membership disable/remove/role-change API, invitation
cancellation, account recovery, owner transfer, child identity, or pairing.
Membership status is persisted, but no status management workflow is provided.

Sessions are 256-bit random bearer tokens; only SHA-256 digests are stored.
They expire after 12 hours, can be revoked by their bearer, and are rechecked
on every directory authorization. Invitation digests are also stored rather
than raw tokens. Handle tokens as secrets: future callers must prevent logging,
leakage, replay, CSRF, cookie theft and insecure transport, and apply throttles.
Session issuance is only for an existing active member with a freshly verified
principal assertion. No persistence pruning, quotas, rate limiting, audit trail,
production backup/recovery or operational controls exist. Clock correctness,
platform entropy and runtime availability are prerequisites.

`authorize(session, family)` returns only a current role/status decision;
`my_memberships(session)` returns that session's active memberships; and
`members(session, family)` requires active membership in that same family.
All relevant checks and directory mutations execute in synchronous SQLite
transactions in the single object. Expiry is exclusive. Failed redemption
rolls back the invite consumption, membership and session together. A lost
response after successful redemption is not safely retryable: the invite is
consumed and the caller must reauthenticate via `issue_session`.

**Critical integration gap:** an authorization check in the directory and a
subsequent bank-object action are not one transaction. A concurrent revocation
or membership change may race after the check. There is no tested fencing,
serialization, authorization-to-bank routing or atomic cross-object guarantee;
do not connect the directory to the bank as production authorization yet.
The existing `FamilyBank` methods themselves remain unauthenticated internal
operations. RPC serialization across Workers is also untested. Directory
initialization rejects nonempty unversioned storage and unknown versions; no
schema upgrade or corrupted-data remediation is provided.

Before each directory operation, inside the same transaction as the operation,
the v1 SQLite catalog is compared to the exact original table definitions and
primary-key autoindexes; extra user tables, indexes, views and triggers also
fail closed. Equivalent hand-recreated schemas with different SQL formatting
are intentionally rejected. Initialization is atomic; version migrations are
unsupported. This detects catalog changes, not arbitrary invalid row contents,
physical database/index corruption, hostile writable_schema manipulation, or
a compromised runtime/caller. SQLite-owned bookkeeping is excluded. No repair
path is provided; rejection can lock the directory until separately recovered.

## Local verification

Use the hosted README's isolated environment setup and pinned Wrangler. Run:

```sh
.venv/bin/python -m unittest discover -s tests_hosted -p 'test_*.py' -v
TMPDIR="${TMPDIR:-/tmp}" .venv/bin/python tests_hosted/directory_integration.py
```

The second command uses a temporary, **unauthenticated synthetic-only** loopback
Worker, SQLite persistence and made-up principals. It exercises denial across
families, membership reads, bad input, expiry, rollback after actual membership
and session inserts during redemption, distinct-principal concurrent invite
redemption, schema tampering, revocation, and restart persistence. It does not
verify a public API, real OIDC, DO RPC, cross-object revocation, production
multi-region races, crash durability or deployment. Never deploy the synthetic worker.
