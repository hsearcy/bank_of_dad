"""Retained hosted Flask SQLite authorization (synthetic signed provider)."""
import sqlite3
from test_hosted import hosted_app, call, login, csrf


def test_parent_pin_is_additional_to_identity_and_is_family_local(hosted_app):
    app, host, provider, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    assert login(a, provider, origin, 'label-parent-a').status_code == 302
    assert login(b, provider, origin, 'label-parent-b').status_code == 302
    def post(client, route, body, pin=None):
        headers = {'Origin': origin, 'X-CSRF-Token': csrf(client)}
        if pin: headers['X-Dad-Pin'] = pin
        return call(client, '/api/admin/' + route, origin, method='POST', json=body, headers=headers)
    assert post(a, 'label', {'parent_label':'Mom'}).status_code == 403
    assert post(a, 'pin', {'new_pin':'123456'}).json['ok']
    assert post(a, 'label', {'parent_label':'Mom'}, '123456').json['ok']
    assert post(b, 'label', {'parent_label':'Stolen'}, '123456').status_code == 403
    assert call(b, '/api/state', origin).json['config']['parent_label'] == 'Dad'
    assert call(a, '/api/state', origin).json['config']['parent_label'] == 'Mom'
    with a.session_transaction(base_url=origin) as session:
        family = session['family']
    with sqlite3.connect(host.family_path(family)) as conn:
        original = conn.execute("SELECT value FROM settings WHERE key='dad_pin_hash'").fetchone()[0]
    assert login(a, provider, origin, 'label-parent-a').status_code == 302
    assert call(a, '/api/admin/status', origin).json == {'pin_set':True}
    assert post(a, 'label', {'parent_label':'Alex'}).status_code == 403
    assert post(a, 'label', {'parent_label':'Alex'}, '123456').json['ok']
    with sqlite3.connect(host.family_path(family)) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='dad_pin_hash'").fetchone()[0] == original
    assert call(a, '/auth/logout', origin, method='POST', headers={'Origin':origin, 'X-CSRF-Token':csrf(a)}).status_code == 302
    assert call(a, '/api/admin/verify', origin, method='POST', json={'pin':'123456'}).status_code == 401
