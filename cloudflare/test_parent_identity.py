"""Synthetic signed OIDC + real workerd verifies PIN is additional, not identity."""
import sqlite3
from test_oauth import oauth, client, csrf, login, bank


def test_claim_and_new_device_preserve_parent_settings_and_pin(oauth):
    runtime, provider = oauth
    with client(runtime) as parent, client(runtime) as other:
        token = csrf(parent)
        csrf(other)
        old_cookie = parent.cookies['bod-local-session']
        family = old_cookie.split('.')[0]
        headers = {'Origin': runtime.origin, 'X-CSRF-Token': token}
        assert parent.post('/api/admin/pin', json={'new_pin': '654321'}, headers=headers).json()['ok']
        headers['X-Dad-Pin'] = '654321'
        assert parent.post('/api/admin/label', json={'parent_label': '👩‍👧 Mama'}, headers=headers).json()['ok']
        path = runtime.database('bod-local-session=' + old_cookie)
        with sqlite3.connect(path) as conn:
            before = conn.execute("SELECT key,value FROM settings ORDER BY key").fetchall()
        login(parent, provider, 'parent-controls-claim')
        assert parent.cookies['bod-local-session'].split('.')[0] == family
        assert bank(parent)['config']['parent_label'] == '👩‍👧 Mama'
        assert runtime.call('/api/state', cookie='bod-local-session=' + old_cookie)[0] == 401
        token = csrf(parent)
        headers['X-CSRF-Token'] = token
        assert parent.post('/api/admin/label', json={'parent_label': 'Mom'}, headers={
            'Origin': runtime.origin, 'X-CSRF-Token': token}).status_code == 403
        with client(runtime) as returning:
            login(returning, provider, 'parent-controls-claim')
            assert bank(returning)['config']['parent_label'] == '👩‍👧 Mama'
            t = csrf(returning)
            for pin in (None, '000000'):
                gate = {'Origin': runtime.origin, 'X-CSRF-Token': t}
                if pin is not None:
                    gate['X-Dad-Pin'] = pin
                assert returning.post('/api/admin/label', json={'parent_label': 'Denied'}, headers=gate).status_code == 403
            assert returning.post('/api/admin/verify', json={'pin':'654321'}, headers={
                'Origin':runtime.origin, 'X-CSRF-Token':t}).json()['ok']
            assert returning.get('/owner').status_code == 404  # Owner allowlist is intentionally unconfigured.
        assert bank(other)['config']['parent_label'] == 'Dad'
        assert other.post('/api/admin/label', json={'parent_label':'Stolen'}, headers={
            'Origin':runtime.origin, 'X-CSRF-Token':csrf(other), 'X-Dad-Pin':'654321'}).status_code == 403
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT key,value FROM settings ORDER BY key").fetchall() == before
        assert parent.post('/auth/logout', headers=headers).status_code == 302
        assert parent.get('/api/state').status_code == 401
        assert parent.post('/api/admin/label', json={'parent_label':'After logout'}, headers=headers).status_code == 401
