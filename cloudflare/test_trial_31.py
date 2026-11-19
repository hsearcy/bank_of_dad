"""Real workerd lazy legacy schema upgrade, concurrency, expiry and isolation."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

from test_runtime import runtime, state

DAY = 86400


def test_31_day_trial_and_old_schema_lazy_migration(runtime):
    cases = [
        # days since creation, old duration, paid, free, HTTP status
        (0, 7, 0, 0, 200),
        (10, 7, 0, 0, 200),  # former trial already expired, now active
        (40, 7, 0, 0, 402),  # never start 31 days from today's visit
        (40, 7, time.time() + DAY, 0, 200),
        (40, 7, 0, 1, 200),
        (10, 90, 0, 0, 200),
        (10, 3, 0, 0, 402),
        (10, None, 0, 0, 402),  # explicit zero expiry
    ]
    guests = [runtime.guest() for _ in cases]
    for cookie, _ in guests:
        with sqlite3.connect(runtime.database(cookie)) as c:
            start, end, policy, customer = c.execute('SELECT trial_started,trial_until,trial_policy,customer FROM _bod_family').fetchone()
            assert end == start + 31 * DAY and policy == 1 and customer is None
    runtime.stop()
    expectations = []
    for (cookie, _), (age, duration, paid, free, code) in zip(guests, cases):
        start = time.time() - age * DAY
        old_end = start + duration * DAY if duration is not None else 0
        new_end = start + 31 * DAY if duration == 7 else old_end
        path = runtime.database(cookie)
        with sqlite3.connect(path) as c:
            c.execute('ALTER TABLE _bod_family DROP COLUMN trial_policy')
            c.execute('UPDATE _bod_family SET trial_started=?,trial_until=?,paid_until=?,free_access=?', (start, old_end, paid, free))
        expectations.append((path, start, new_end, paid, free, code))
    runtime.start()
    # Multiple in-flight requests race the first old-schema open in the real DO.
    first = guests[0][0]
    with ThreadPoolExecutor(8) as pool:
        assert list(pool.map(lambda _: runtime.call('/api/state', cookie=first)[0], range(8))) == [200] * 8
    for (cookie, _), (path, start, end, paid, free, code) in zip(guests, expectations):
        assert runtime.call('/api/state', cookie=cookie)[0] == code
        assert runtime.call('/api/state', cookie=cookie)[0] == code
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT trial_started,trial_until,paid_until,free_access,trial_policy,customer FROM _bod_family').fetchone() == (start, end, paid, free, 1, None)
            assert c.execute('SELECT wallet_cents FROM kids WHERE id=1').fetchone()[0] == 500
    runtime.stop()
    runtime.start()
    for (cookie, _), (_, _, _, _, _, code) in zip(guests, expectations):
        assert runtime.call('/api/state', cookie=cookie)[0] == code
    # A newly opened family is independent of all expired/paid/granted families.
    fresh, _ = runtime.guest()
    assert state(runtime, fresh)['kids'][0]['wallet_cents'] == 500
