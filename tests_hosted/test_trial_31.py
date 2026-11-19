"""31-day policy: disposable SQLite, exact legacy inference and lazy access."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

import pytest

from hosted_access import LEGACY_TRIAL_SECONDS as WEEK, TRIAL_SECONDS, migrate, migrate_family_trial
from test_hosted import hosted_app, call, login


@pytest.mark.parametrize('start,duration,paid,free,expected', [
    (1000, WEEK, 0, 0, TRIAL_SECONDS),  # expired long ago: never visit + 31
    (time.time(), WEEK, 0, 0, TRIAL_SECONDS),
    (1000, WEEK, 9999999999, 0, TRIAL_SECONDS),
    (1000, WEEK, 0, 1, TRIAL_SECONDS),
    (1000, 90 * 86400, 0, 0, 90 * 86400),
    (1000, 3 * 86400, 0, 0, 3 * 86400),
    (1000, -1000, 0, 0, -1000),  # explicit zero/revoked trial
    (0, WEEK, 0, 0, WEEK),
    (-1000, WEEK, 0, 0, WEEK),
    (1000, WEEK + .001, 0, 0, WEEK + .001),
])
def test_exact_legacy_upgrade_restart_and_no_false_positives(tmp_path, start, duration, paid, free, expected):
    path = tmp_path / 'directory.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE families(id TEXT PRIMARY KEY, trial_started REAL, trial_until REAL, paid_until REAL, free_access INTEGER)')
        c.execute('INSERT INTO families VALUES(?,?,?,?,?)', ('a', start, start + duration, paid, free))
        c.commit()
        migrate(c)
        migrate_family_trial(c, 'families', 'a')
        assert c.execute('SELECT trial_started,trial_until,paid_until,free_access,trial_policy FROM families').fetchone() == (start, start + expected, paid, free, 1)
    with sqlite3.connect(path) as c:
        before = list(c.iterdump())
        migrate_family_trial(c, 'families', 'a')
        assert list(c.iterdump()) == before
        # The persisted marker prevents later edits from looking like a new trial.
        c.execute('UPDATE families SET trial_until=trial_started+?', (WEEK,))
        migrate_family_trial(c, 'families', 'a')
        assert c.execute('SELECT trial_until FROM families').fetchone()[0] == start + WEEK


def test_concurrent_atomic_upgrade_and_failure_rollback(tmp_path):
    path = tmp_path / 'directory.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE families(id TEXT PRIMARY KEY, paid_until REAL)')
        c.execute("INSERT INTO families VALUES('a',0)")
        c.commit()
        migrate(c, now=1000)
        c.execute('UPDATE families SET trial_started=1000,trial_until=?', (1000 + WEEK,))
        c.execute("CREATE TRIGGER fail BEFORE UPDATE ON families BEGIN SELECT RAISE(ABORT,'rollback'); END")
    with sqlite3.connect(path) as c:
        with pytest.raises(sqlite3.IntegrityError):
            migrate_family_trial(c, 'families', 'a')
        assert c.execute('SELECT trial_until,trial_policy FROM families').fetchone() == (1000 + WEEK, 0)
        c.execute('DROP TRIGGER fail')
    barrier = threading.Barrier(8)
    def apply(_):
        with sqlite3.connect(path, timeout=10) as c:
            barrier.wait(timeout=10)
            migrate_family_trial(c, 'families', 'a')
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(apply, range(8)))
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT trial_until,trial_policy FROM families').fetchone() == (1000 + TRIAL_SECONDS, 1)


def test_retained_guest_lazy_upgrade_claim_and_no_billing(hosted_app):
    app, host, provider, origin = hosted_app
    client = app.test_client()
    assert call(client, '/', origin).status_code == 200
    with host.connect() as c:
        row = dict(c.execute('SELECT * FROM families').fetchone())
        assert row['trial_until'] == row['trial_started'] + TRIAL_SECONDS
        assert row['trial_policy'] == 1
        start = time.time() - 10 * 86400
        c.execute('UPDATE families SET trial_started=?,trial_until=?,trial_policy=0', (start, start + WEEK))
    assert call(client, '/api/state', origin).status_code == 200
    assert '21 days left' in call(client, '/billing', origin).get_data(as_text=True)
    assert login(client, provider, origin).status_code == 302
    assert '21 days left' in call(client, '/billing', origin).get_data(as_text=True)
    with host.connect() as c:
        claimed = dict(c.execute('SELECT * FROM families').fetchone())
        assert claimed['trial_started'] == start
        assert claimed['trial_until'] == start + TRIAL_SECONDS
        assert claimed['guest_hash'] is None and claimed['trial_policy'] == 1
    assert login(client, provider, origin).status_code == 302
    with host.connect() as c:
        assert dict(c.execute('SELECT * FROM families').fetchone()) == claimed
    assert provider['customers'] == [] and provider['checkouts'] == []
