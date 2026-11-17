# Synthetic combined recovery/revision DO fixture

Local test only. `synthetic_js_combined.py` composes the v3 coordinator inside a
fresh Python Durable Object. The protected RPC surface is `read`, `snapshot`,
`mutate`, `pending`, `outcome`, and `acknowledge`; no FamilyService or legacy
mutation RPC is exposed. Its `fixture_*` methods and the JS caller are
**unauthenticated privileged test controls**, including arbitrary SQL and grant
creation. They must never be deployed or bound to untrusted callers. There is
no real identity verification, trusted routing, dashboard integration, or socket
notification here.

Run `python tests_hosted/js_combined_integration.py` with the pinned Wrangler
from `tests_hosted/package-lock.json` installed (or `WRANGLER_BIN` pointing to
that version). The harness creates isolated temporary source, configuration,
state, and loopback runtime processes; it cleans them up after the test. It
exercises the actual JS-to-Python RPC boundary and a graceful local restart.
The lost-reply case sends a real loopback HTTP request, waits for the JS gateway
to observe a completed DO mutation RPC and signal a test gate, closes the client
socket without reading a response, then releases the gateway to attempt its
response. The gate confirms the handler resumed; it does not prove the runtime
noticed the closed socket or that the response write failed. The DO-to-JS RPC
reply itself is received, not lost: loss on that upstream leg remains modeled,
not exercised. This is neither a provider crash nor proof of network failure.
After a graceful restart, a newly issued grant to the same synthetic principal
discovers and replays the pending ID without browser storage, then must explicitly
acknowledge the exact result before a new ID can proceed. No real identity check
or browser is involved.

Snapshots compare the entire SQLite catalog and all table rows with per-cell
`typeof` tags (blobs represented as hex); a separate integer-versus-real probe
checks sensitivity. Test-only Python-origin outcomes match exact exception types
and messages; unrelated errors are reported as unexpected, not treated as denials.

The fixture supports fresh v3 only and rejects v1, v2 and damaged schemas; no
migration exists. Grant clock rollback and restoration of an older database can
invalidate expiry or deduplication assumptions. Trusted Python/SQL access is not
a sandbox. Local tests do not establish production readiness, live identity,
provider crash durability, or real-time delivery.
