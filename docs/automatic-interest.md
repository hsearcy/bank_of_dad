# Automatic savings interest

The Dad savings settings include one **family-wide** opt-in, off by default, like the existing family-wide savings rate and period. It applies to every child, including children added later; family-local storage and existing parent/PIN, hosted session, Origin and CSRF checks remain authoritative.

## Policy

- Enabling credits existing pending interest **once now**. It does not reconstruct or compound earlier periods. The first future deadline is enabling time plus the currently selected savings period (fixed days, not calendar months).
- Thereafter each completed savings period credits interest into savings, which earns interest during the next period. Deadlines are persisted per child, not reset by dashboard reads, reloads, manual collection or deposits. Missed periods replay in deadline order at their scheduled timestamps.
- While on, manual interest collection is a no-op (other rewards still work), and the child sees the automatic collection countdown instead of a tappable interest reward. Save/withdraw changes principal without capitalizing the partially earned interest. Withdrawals can use credited savings, not the still-pending interest.
- Disabling first settles completed periods, preserves partial earned interest without collecting it, and removes future deadlines. Re-enabling claims current pending earnings once and starts a fresh period. Repeated saves of the same toggle are no-ops, not extra claims.
- Rates change only future earnings, preserving exact accrued carry at the change boundary. A rate-only change keeps the next collection date. A savings-period change starts a fresh period from the change boundary, retaining already-earned carry without an extra claim. When enabling and editing terms together, preserve past earnings at the old terms, claim once, and start the new selected period.
- Automatic credits use the existing nearest-cent (Python ties-to-even) rounding, but retain the fractional remainder, including a negative remainder after rounding up. Zero-cent deadlines still advance and retain fractions. Native/Worker clocks that move backward do not reopen an earned interval: new schedules use at least the stored accrual boundary. Fractional carry is internal; displayed pending interest never goes negative. Existing manual-mode collection/transfer rounding rules remain unchanged.

## Runtime behavior and safety

Cloudflare multiplexes interest deadlines into the existing single Durable Object alarm along with access and session expiry. The alarm settles interest transactionally even without an open dashboard, publishes the normal content-free WebSocket invalidation, then rearms the earliest deadline. Demo-clock offsets are converted back to a real alarm delay. No additional service, cron or provider is used.

Free self-hosting has **access-based catch-up**, not an offline background service. The open dashboard's existing reconciliation fetch also settles due periods. Closing the dashboard or stopping the server delays materialization, but the next state read or balance/terms mutation replays the same scheduled credits rather than compounding once at the visit time. No claim is made that self-hosting posts ledger entries while offline.

Each call processes at most 512 due child-periods, globally ordered. If more remain, it commits catch-up progress and responds with retryable HTTP 503 before performing the requested money/terms mutation; subsequent reads/retries continue. Worker alarms rearm promptly while backlog remains. Native caller-owned transactions retain their own commit/rollback authority. Keep retrying a catch-up state read before submitting a new mutation; a catch-up response is not successful completion of that requested mutation.

All native balance mutations acquire the SQLite writer lock before reading; Worker mutations remain inside the existing family `transactionSync`. Principal, carry, next deadline, interest ledger and history commit together. Additive migration supplies a nullable deadline on existing children, without enabling the setting, reseeding, or changing sessions. Automatic credit rejects nonfinite amounts or resulting savings outside the SQL/JS exact-integer-cent range (0 through 2^53−1), rather than silently rounding a large balance. Such an extreme persisted balance requires operator review before enabling/settling automatic interest.

No production database, deployment, provider, merge or push is needed to test this feature.

## Verification

- `cloudflare/.venv/bin/python -m pytest -q tests/test_auto_interest.py`
- From `cloudflare/`: `.venv/bin/python -m pytest -q test_auto_interest_runtime.py test_rate_periods_runtime.py test_runtime.py`
- `BANK_OF_DAD_BROWSER_TESTS=1 cloudflare/.venv/bin/python -m pytest -q tests`

The runtime tests use disposable real workerd/SQLite and Chromium at phone and desktop widths. No live provider or real family data is used.
