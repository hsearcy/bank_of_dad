# Parent name and PIN controls

The parent-controls panel now offers a per-bank **Parent name or label** setting (for example Mom, Papa, Alex). The default remains Dad. Names are 1–40 Unicode characters after trimming; control characters and bidi overrides are rejected. Names are text, never HTML. Brand/About, repository and sign-in product names remain Bank of Dad.

The existing `settings` table stores `parent_label` in the family's own SQLite database, including SQLite-backed Durable Objects. Missing settings are read as Dad without writing a default, so upgrades do not overwrite names or PINs and require no DDL/backfill. New default deposit notes use the current parent label; old ledger notes are not rewritten.

## PIN behavior

- Landing and guest welcome remain usable without setting a PIN. The first attempt to open parent controls asks **Set Dad PIN**, then **Confirm PIN**. Cancellation leaves controls closed and mutations blocked.
- Existing PIN hashes are preserved. Every subsequent panel open requires the PIN. Closing, hiding or leaving the page forgets the in-memory PIN. Reload/new-device access does not retain an unlocked panel.
- PINs accept 4–8 ASCII digits and require explicit submission, so an existing 5–8 digit PIN is usable. PIN changes require the current PIN and confirmation; removing protection is not supported.
- All parent bank-mutation APIs require a configured, correct PIN. Hosted identity/capability, trial/subscription, family, Origin and CSRF authorization still run first. A PIN cannot sign into a family, select a family, claim a bank, grant owner access or authorize billing.
- Five incorrect guesses lock PIN verification, changes and protected mutations for 60 real-clock seconds. Throttle state is family-local and durable across restart. Missing credentials deny without recording a guess. Successful authorization retains the existing SQLite writer lock through the protected mutation.
- PINs are never persisted in browser storage or stored/logged in plaintext. The existing hash format is retained for compatibility. This is a shared-device child guard, not a replacement for hosted authentication or TLS. There is no new unauthenticated forgotten-PIN/reset override.

## Regression cause

The original hosted introduction (`f915975`) intentionally replaced PIN status/verification with `hosted_parent=True` / `ok=True`, rejected PIN setup and bypassed the app's PIN check. That policy was carried into the Cloudflare adapter. The UI also skipped initial setup for hosted families and hid its PIN-change control. Those shortcuts are removed; PIN protection is additional to, not instead of, hosted authorization.

## Disposable acceptance

Run from the repository root with Flask/Sock, pytest and Playwright installed:

```sh
python -m pytest -q tests
python -m pytest -q tests_hosted/test_parent_controls.py
```

Run from `cloudflare/` after installing pinned development dependencies and Chromium:

```sh
.venv/bin/python -m pytest -q test_parent_controls.py test_parent_identity.py
```

These feature tests exercise real SQLite, loopback HTTP, workerd/SQLite DOs, Chromium at phone/desktop sizes, setup confirmation/mismatch/cancellation, eight-digit entry, reload/restart, legacy hash retention, direct API denial, Origin/CSRF denial, literal XSS payloads, same-family WebSocket name updates with polling disabled, cross-family isolation, durable throttling, signed synthetic OIDC claim/new-device sign-in and logout. They do not contact real identity, payment or production services. Older hosted/runtime feature fixtures that intentionally perform parent writes without setting/supplying a PIN must now set up a synthetic PIN first; the old bypass is no longer a supported contract.
