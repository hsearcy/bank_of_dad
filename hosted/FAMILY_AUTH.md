# Experimental family-local authorization — NOT deployable

`FamilyAuthorization` is an internal-only, synthetic foundation, not a Worker,
RPC interface or an integration with `Directory` or `FamilyBank`. It provides no
identity verification or family routing. **Do not expose it to clients.** The
existing `FamilyService` and `FamilyBank` remain unauthenticated and bypass this
module entirely; they must not be exposed as an alternative path.

A future trusted boundary must verify identity and select the correct family DO
server-side. `bootstrap_privileged` and `issue_grant_privileged` are privileged
internal assertions, not client authentication. Bootstrap only accepts an empty
SQLite database and atomically creates the bank schema, local auth schema, and
first owner. It must be called only by a future authorized bootstrap coordinator;
none exists. Existing family databases are rejected, not silently migrated.

Each object stores one family ID, owner/parent membership, and hashed opaque
family-local grants. Owners can change memberships; both owners and parents can
read and perform every listed bank mutation. Only owners can manage members.
A member can revoke their own grant; disabling a member revokes all its grants,
including across later reactivation. There is no last-owner protection or recovery
flow. An owner may accidentally lock the family out. Issuance requires a trusted
verified principal assertion and an active membership. Grants last 1–43,200
seconds, expire exclusively, and issuance prunes expired/revoked grants; there
are at most 1,000 unexpired, unrevoked grants per family. Clock and identity must
come from trusted infrastructure. No global logout, revocation propagation, or
relationship to directory sessions is implemented.

Every local check, bank operation, idempotency lookup/result and membership change
or revocation uses the same synchronous SQLite transaction, with no await inside
it. The existing bank transaction guard remains in force, and its domain functions
are unchanged. Operation IDs are family-scoped, bound to the original issuer and
subject and canonical payload. Current authorization is checked before any replay;
a different principal cannot see a result or reuse its ID. A same-principal new
grant can retry while still authorized. An exception after the bank write rolls
back the bank write, result, and ownership record together.

Auth catalog definitions and version are checked on every operation, and unknown,
missing or modified local auth schema fails closed. There is no migration path for
any existing bank/auth schema. The older bank schema uses its existing version
checks and does not validate its entire SQLite catalog against malicious changes;
this is not a defense against a compromised trusted caller or arbitrary SQLite
access. Idempotency and membership records are not bounded or pruned. Operational
limits, anti-abuse, ownership recovery, audit, backup, secure token handling,
OIDC, gateway, actual RPC serialization, global revocation, coordinated bootstrap,
real-data migration, security review and production deployment remain undone.

Synthetic tests: `python -m unittest discover -s tests_hosted -p 'test_*.py'` and
`python tests_hosted/auth_integration.py` (requires pinned Wrangler as described
in README). The integration starts an intentionally unauthenticated test fixture on
loopback with disposable emulator data; it is not an authenticated deployment.
