"""Automatic interest and signed-session expiry share a real persisted DO alarm."""
import json
import sqlite3
import time

import pytest
from test_oauth import oauth, client, csrf, login, setup_parent
from test_runtime import snapshot


def test_auto_interest_does_not_postpone_signed_session_expiry(oauth):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    runtime, provider = oauth
    with client(runtime) as parent, client(runtime) as other:
        csrf(parent)
        login(parent, provider, 'automatic-session-expiry')
        setup_parent(parent, runtime)
        token = csrf(parent)
        headers = {'Origin': runtime.origin, 'X-CSRF-Token': token}
        assert parent.post('/api/admin/rates', json={'auto_interest_enabled': True}, headers=headers).json()['ok']
        csrf(other)
        foreign_cookie = 'bod-local-session=' + other.cookies['bod-local-session']
        foreign_path = runtime.database(foreign_cookie)
        foreign_before = snapshot(foreign_path)
        cookie = 'bod-local-session=' + parent.cookies['bod-local-session']
        path = runtime.database(cookie)
        runtime.stop()
        due, expiry = time.time()+25, time.time()+45
        with sqlite3.connect(path) as conn:
            conn.execute('UPDATE _bod_sessions SET expires=?', (expiry,))
            conn.execute('UPDATE kids SET savings_cents=10000,savings_interest_carry=0,savings_accrued_ts=?,auto_interest_due_ts=?', (due-7*86400,due))
            conn.execute("DELETE FROM transactions WHERE kind='interest'")
        runtime.start()
        with connect(runtime.origin.replace('http:', 'ws:')+'/api/updates', origin=runtime.origin,
                     additional_headers={'Cookie':cookie}, open_timeout=15) as ws:
            assert json.loads(ws.recv(timeout=15))['type'] == 'connected'
            assert json.loads(ws.recv(timeout=35))['type'] == 'state_changed'
            with pytest.raises(ConnectionClosed) as closed:
                while True:
                    ws.recv(timeout=50)
            assert closed.value.rcvd.code == 1008
        assert parent.get('/api/state').status_code == 401
        assert parent.post('/api/admin/rates', json={'auto_interest_enabled':False}, headers=headers).status_code == 401
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT savings_cents FROM kids').fetchone()[0] == 10400
            assert conn.execute("SELECT count(*) FROM transactions WHERE kind='interest'").fetchone()[0] == 1
        assert snapshot(foreign_path) == foreign_before


def test_auto_setting_requires_family_pin_but_child_saving_does_not(oauth):
    runtime, _ = oauth
    cookie, token = runtime.guest()
    other, other_token = runtime.guest()
    path = runtime.database(cookie)
    before = snapshot(path)
    body = {'auto_interest_enabled': True}
    assert runtime.call('/api/admin/rates', cookie=cookie, csrf=token, body=body)[0] == 403
    assert snapshot(path) == before
    assert runtime.call('/api/admin/pin', cookie=cookie, csrf=token, body={'new_pin':'1234'})[1]['ok']
    for pin in (None, '9876'):
        headers = {'X-Dad-Pin':pin} if pin else {}
        assert runtime.call('/api/admin/rates', cookie=cookie, csrf=token, body=body, headers=headers)[0] == 403
    assert runtime.parent_call('/api/admin/rates', cookie=cookie, body=body)[0] == 403
    assert runtime.parent_call('/api/admin/rates', cookie=cookie, csrf=token, body=body, origin='https://foreign.invalid')[0] == 403
    assert runtime.parent_call('/api/admin/rates', cookie=other, csrf=other_token, body=body)[0] == 403
    assert runtime.parent_call('/api/admin/rates', cookie=cookie, csrf=token, body=body)[1]['ok']
    # No parent header on ordinary child controls, including while auto is on.
    assert runtime.call('/api/save', cookie=cookie, csrf=token, body={'kid_id':1,'amount_cents':100})[1]['ok']
    assert not runtime.call('/api/state', cookie=other)[1]['config']['auto_interest_enabled']
