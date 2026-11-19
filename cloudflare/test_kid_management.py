"""Kid management on real local workerd SQL/transactions and Chromium.

Identity provider is a signed loopback fixture, not Google acceptance.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import sqlite3
import sys

from test_runtime import ROOT, INIT, snapshot, state
from test_oauth import oauth, client, login, csrf, setup_parent

sys.path.insert(0, str(ROOT / 'tests'))
from kid_management_ui import exercise_kid_controls


def child_rows(path, kid):
    with sqlite3.connect(path) as conn:
        return {t: conn.execute(f'SELECT * FROM {t} WHERE {"id" if t == "kids" else "kid_id"}=? ORDER BY id', (kid,)).fetchall()
                for t in ('kids', 'bonds', 'transactions', 'history')}


def test_parent_session_csrf_foreign_family_and_confirmations(oauth):
    runtime, provider = oauth
    with client(runtime) as a, client(runtime) as b:
        login(a, provider, 'kid-management-parent')
        login(b, provider, 'kid-management-other-parent')
        setup_parent(a, runtime)
        setup_parent(b, runtime)
        token = csrf(a)
        headers = {'Origin': runtime.origin, 'X-CSRF-Token': token}
        kid = a.get('/api/state').json()['kids'][0]['id']
        # A child present only in B is never resolved using client family hints.
        assert b.post('/api/admin/kid', json={'name': 'Foreign'}, headers={
            'Origin': runtime.origin, 'X-CSRF-Token': csrf(b)}).status_code == 200
        foreign = b.get('/api/state').json()['kids'][-1]['id']
        before_b = b.get('/api/state').json()['kids']
        for path, payload in [('update', {'kid_id': kid, 'name': 'Parent renamed'}),
                              ('remove', {'kid_id': kid, 'confirm_name': 'Sample Kid'})]:
            url = '/api/admin/kid/' + path
            assert runtime.call(url, body=payload)[0] == 401
            assert a.post(url, json=payload).status_code == 403
            assert a.post(url, json=payload, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
            assert a.post(url, json={**payload, 'kid_id': foreign, 'family_id': b.cookies['bod-local-session'].split('.')[0]}, headers=headers).status_code == 404
        assert a.post('/api/admin/kid/remove', json={'kid_id': kid}, headers=headers).status_code == 400
        assert a.post('/api/admin/kid/update', json={'kid_id': kid, 'name': '  '}, headers=headers).status_code == 400
        assert a.post('/api/admin/kid/update', json={'kid_id': kid, 'name': 'Parent renamed'}, headers=headers).json()['ok']
        assert a.post('/api/admin/kid/remove', json={'kid_id': kid, 'confirm_name': 'Sample Kid'}, headers=headers).status_code == 409
        assert a.post('/api/admin/kid/remove', json={'kid_id': kid, 'confirm_name': 'Parent renamed'}, headers=headers).json()['ok']
        assert a.get('/api/state').json()['kids'] == []
        assert b.get('/api/state').json()['kids'] == before_b
        assert a.post('/auth/logout', data={'csrf_token': token}, headers={'Origin': runtime.origin}).status_code == 302
        assert a.post('/api/admin/kid/remove', json={'kid_id': kid, 'confirm_name': 'Parent renamed'}, headers=headers).status_code == 401


def test_cascade_failure_atomic_scope_and_restart(oauth):
    runtime, _ = oauth
    cookie, token = runtime.parent_guest()
    kid = state(runtime, cookie)['kids'][0]['id']
    assert runtime.parent_call('/api/admin/kid', cookie=cookie, csrf=token, body={'name': 'Keep'})[1]['ok']
    sibling = state(runtime, cookie)['kids'][-1]['id']
    for route, body in [('/api/admin/gift', {'kid_id': sibling, 'amount_cents': 400}),
                        ('/api/save', {'kid_id': sibling, 'amount_cents': 100}),
                        ('/api/bond', {'kid_id': sibling, 'amount_cents': 100, 'term': '1mo'})]:
        assert runtime.parent_call(route, cookie=cookie, csrf=token, body=body)[1]['ok']
    runtime.stop()
    path = runtime.database(cookie)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_remove BEFORE DELETE ON history BEGIN SELECT RAISE(ABORT, 'test cascade rollback'); END")
    before = snapshot(path)
    preserved = child_rows(path, sibling)
    runtime.start()
    assert runtime.parent_call('/api/admin/kid/remove', cookie=cookie, csrf=token,
                        body={'kid_id': kid, 'confirm_name': 'Sample Kid'})[0] == 500
    runtime.stop()
    assert snapshot(path) == before
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER fail_remove')
    runtime.start()
    assert runtime.parent_call('/api/admin/kid/update', cookie=cookie, csrf=token,
                        body={'kid_id': kid, 'name': 'Renamed sample'})[1]['ok']
    runtime.stop()
    renamed = child_rows(path, kid)
    runtime.start()
    assert state(runtime, cookie)['kids'][0]['name'] == 'Renamed sample'
    assert runtime.parent_call('/api/admin/kid/remove', cookie=cookie, csrf=token,
                        body={'kid_id': kid, 'confirm_name': 'Renamed sample'})[1]['ok']
    runtime.stop()
    assert all(not rows for rows in child_rows(path, kid).values())
    assert child_rows(path, sibling) == preserved
    assert renamed['bonds'] and renamed['transactions'] and renamed['history']
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    runtime.start()
    assert [k['name'] for k in state(runtime, cookie)['kids']] == ['Keep']


def test_phone_controls_cancel_push_and_empty_bank(oauth):
    from playwright.sync_api import sync_playwright
    runtime, _ = oauth
    with sync_playwright() as pw, pw.chromium.launch() as browser, pw.chromium.launch() as second:
        context = browser.new_context()
        context.add_init_script(INIT)
        a = context.new_page()
        a.goto(runtime.origin)
        a.locator('#tryGuestBank').click()
        a.locator('#tourSkip').click()
        shared = second.new_context(storage_state=context.storage_state())
        shared.add_init_script(INIT)
        b = shared.new_page()
        b.goto(runtime.origin)
        for page in (a, b):
            page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
        # An unrelated tenant observes neither rename nor delete notifications.
        other = second.new_context()
        other.add_init_script(INIT)
        foreign = other.new_page()
        foreign.goto(runtime.origin)
        foreign.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
        cookie = next(c for c in context.cookies() if c['name'] == 'bod-local-session')
        exercise_kid_controls(a, b, lambda page: open_parent(page))
        assert b.evaluate('window.__frames.filter(f => JSON.parse(f).type === "state_changed").length') >= 4
        assert foreign.locator('#kids .name').inner_text() == 'Sample Kid'
        assert not foreign.evaluate('window.__frames.some(f => JSON.parse(f).type === "state_changed")')
    runtime.stop()
    with sqlite3.connect(runtime.database(cookie['name'] + '=' + cookie['value'])) as conn:
        for table in ('kids', 'bonds', 'transactions', 'history'):
            assert conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    runtime.start()
    assert state(runtime, cookie['name'] + '=' + cookie['value'])['kids'] == []
