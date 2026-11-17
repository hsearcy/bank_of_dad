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
The lost-reply case discards the successful RPC response at the test gateway;
it does not simulate a provider crash or network failure.

The fixture supports fresh v3 only and rejects v1, v2 and damaged schemas; no
migration exists. Grant clock rollback and restoration of an older database can
invalidate expiry or deduplication assumptions. Trusted Python/SQL access is not
a sandbox. Local tests do not establish production readiness, live identity,
provider crash durability, or real-time delivery.
