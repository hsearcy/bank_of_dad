"""Disposable SQLite + delayed RPC work-budget tests, not provider timing."""
import asyncio
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from types import SimpleNamespace


import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ISSUER = 'https://accounts.google.com'


@pytest.fixture
def directory(monkeypatch):
    # The native interpreter has no Workers JS module. Stub only that adapter;
    # execute the real selection, rendering, access authorization and SQLite SQL.
    auth = SimpleNamespace(COOKIE='fixture', cookie=lambda *_: 'fixture',
        response=lambda text='', status=200, **kw: SimpleNamespace(text=text, status=status, **kw))
    templates = SimpleNamespace(TEMPLATES={'owner.html': (ROOT / 'templates/owner.html').read_text()})
    monkeypatch.setitem(sys.modules, 'cloudflare_auth', auth)
    monkeypatch.setitem(sys.modules, 'cloudflare_templates', templates)
    spec = importlib.util.spec_from_file_location('owner_selection_under_test', ROOT / 'cloudflare_owner.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = sqlite3.connect(':memory:')
    sql.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
    # Permit multiple identities per family to guard DISTINCT semantics even if
    # the present production schema has a unique family association.
    sql.executescript('CREATE TABLE identities (issuer TEXT, subject TEXT, family TEXT); '
        'CREATE INDEX identities_family ON identities(family); '
        'CREATE TABLE owner_families (family TEXT PRIMARY KEY);')

    class Directory(module.OwnerDirectory):
        env = SimpleNamespace(BOD_OWNER_IDENTITIES=json.dumps([dict(issuer=ISSUER, subject='fixture-owner')]))
        delay = .0
        active = peak = 0
        def __init__(self):
            self.calls = []
        def setup(self):
            pass
        def transact(self, fn):
            return fn(sql)
        async def inspect(self, _):
            return dict(issuer=ISSUER, subject='fixture-owner', is_guest=False, csrf='fixture-csrf')
        async def form(self, *_):
            return self.form_data
        def family(self, family):
            async def owner(payload):
                data = json.loads(payload)
                self.calls.append((family, data['operation']))
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(self.delay)
                    return json.dumps(dict(id=family, is_guest=False, trial_started=1,
                        trial_until=time.time() + 3600, paid_until=0, free_access=0,
                        billing_reconciled_at=None, changed_at=None, audit=[]))
                finally:
                    self.active -= 1
            return SimpleNamespace(owner=owner)
    d = Directory()
    yield d, sql
    sql.close()


def listing(d, query=''):
    return asyncio.run(d.owner_http(SimpleNamespace(url='https://fixture.invalid/owner' + query, method='GET')))


def cards(result):
    assert result.status == 200
    return re.findall(r'data-family="([0-9a-f]{32})"', result.text)


def seed(sql, count=123):
    families = [f'{i:032x}' for i in range(1, count + 1)]
    sql.executemany('INSERT INTO identities VALUES (?,?,?)', [(ISSUER, str(i), f) for i, f in enumerate(families)])
    sql.executemany('INSERT INTO identities VALUES (?,?,?)', [(ISSUER, 'second-' + str(i), f) for i, f in enumerate(families)])
    sql.executemany('INSERT INTO owner_families VALUES (?)', [(f'{i:032x}',) for i in range(1, 10001)])
    return families


def test_saved_filter_dedupe_pagination_prefix_exact_and_no_guest_rpcs(directory):
    d, sql = directory
    families = seed(sql)
    before = list(sql.iterdump())
    for page, expected in ((1, families[:50]), (2, families[50:100]), (3, families[100:])):
        result = listing(d, '?page=' + str(page))
        assert cards(result) == expected, (page, result.text, d.calls)
        assert ('>Next</a>' in result.text) == (page < 3)
    assert cards(listing(d, '?family=' + families[-1])) == [families[-1]]
    prefix = families[-1][:-1]
    assert cards(listing(d, '?family=' + prefix)) == [f for f in families if f.startswith(prefix)]
    assert cards(listing(d, '?family=' + f'{9000:032x}')) == []
    assert cards(listing(d, '?family=ffffffff')) == []
    assert cards(listing(d, '?page=4')) == []
    assert all(f in families for f, _ in d.calls)
    assert list(sql.iterdump()) == before
    plan = sql.execute('EXPLAIN QUERY PLAN SELECT DISTINCT family FROM identities WHERE family >= ? AND family < ? ORDER BY family LIMIT 51 OFFSET ?', ('', 'g', 0)).fetchall()
    assert any('SEARCH' in r['detail'] and 'identities_family' in r['detail'] for r in plan)


def test_guest_direct_grant_and_revoke_rejected_without_fanout(directory):
    d, sql = directory
    seed(sql)
    for action in ('grant', 'revoke'):
        d.form_data = dict(family=f'{9000:032x}', action=action, reason='Fixture', confirm_revoke='yes')
        result = asyncio.run(d.owner_http(SimpleNamespace(url='https://fixture.invalid/owner/access', method='POST')))
        assert result.status == 404
    assert d.calls == []


def test_fifty_saved_families_use_bounded_parallel_waits(directory):
    d, sql = directory
    seed(sql, 50)
    d.delay = .02
    started = time.monotonic()
    result = listing(d)
    elapsed = time.monotonic() - started
    assert len(cards(result)) == 50 and len(d.calls) == 50
    assert d.peak == 5
    assert elapsed < .65  # Serial baseline needs at least 1 second of RPC waits.
    print('SYNTHETIC_50_SAVED_10000_REGISTERED_MS', round(elapsed * 1000, 1), 'PEAK', d.peak)
