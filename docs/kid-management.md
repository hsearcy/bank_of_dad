# Managing kids safely

Open **Dad** and use the **Rename** or **Remove…** button on the intended child's card.
Self-hosting uses the existing Dad PIN; hosted banks use the existing authenticated
parent session or device-bank capability, exact Origin and CSRF checks.

- Rename uses the existing `/api/admin/kid/update` route and `bank.update_kid`.
  Names are trimmed and must contain 1–80 Unicode code points. Balances,
  investments, ledger entries, history, emoji and color are preserved.
- Remove opens a child-named confirmation explaining permanent loss of wallet,
  savings, uncollected rewards, all investments, transactions and chart history.
  Cancel is focused first; Cancel and Escape make no request. Keyboard focus stays
  in the native modal; touch targets are at least 44px high.
- Only the explicit **Permanently remove [name]** submission writes. The request
  carries the exact displayed name along with that child's ID. A rename on another
  device invalidates the old confirmation; missing/changed targets do not succeed.
- There is no archive/soft-delete model in the existing schema. Removal is a single
  parameterized `DELETE FROM kids WHERE id=? AND name=?`, using the existing
  enforced `ON DELETE CASCADE` relationships for bonds, transactions and history.
  No transaction reversal, balance transfer, ID reset or global cleanup runs.
  A cascade error rolls back the whole statement; workerd also encloses the full
  authenticated Flask request in `transactionSync`. Domain `commit()` does not
  split that Durable Object transaction.
- Successful rename/removal uses the existing post-commit WebSocket invalidation
  path. Other screens refetch their own authorized family state. Open Dad lists
  update for changed child names/membership without replacing forms on money ticks.
- No child (including Sample Kid) is automatically removed. Removing the last child
  leaves an empty bank with the existing Add a kid controls; refresh/restart never
  recreates the sample. Removal is irreversible in the application, not a promise
  of physical erasure from separately retained backups or provider recovery data.
- A request with an unknown network result is not automatically retried. The dialog
  asks the parent to close and refresh before trying again.

## Disposable verification

From the repository root, with test requirements and Playwright Chromium installed:

```sh
BANK_OF_DAD_BROWSER_TESTS=1 python -m unittest discover -s tests -v
```

From `cloudflare/`, after `uv sync --locked` and `npm ci`:

```sh
uv run pytest -q test_kid_management.py test_runtime.py
```

The new tests cover self-hosted PIN denial, hosted parent-session and CSRF/Origin
checks, foreign-family targets and overlapping child IDs, strict confirmation,
validation, stale confirmation, exact sibling/ledger preservation, cascade failure
rollback, foreign-key integrity, rename/removal persistence across real server
restarts, sample removal, empty-bank restart, phone-size UI, cancellation, keyboard
focus, escaped names, two independent Chromium processes with polling disabled and
cross-family notification isolation. All state is synthetic and disposable. The
identity fixture signs local tokens and exercises the actual OAuth/session path;
it does not establish real Google-provider acceptance. No deployment is required.
