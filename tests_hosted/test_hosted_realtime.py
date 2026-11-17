"""Real HTTP/WS through an HTTP backend with a configured HTTPS public origin.

Only synthetic identities/providers and disposable family/session databases.
Host preservation models TLS termination without trusting forwarded headers.
"""
import json
import threading
import time
from contextlib import contextmanager

import pytest
import requests
from websocket import (create_connection, WebSocketBadStatusException,
                       WebSocketConnectionClosedException, WebSocketTimeoutException)
from werkzeug.serving import make_server

from test_hosted import hosted_app, call, csrf, login, post, signed_event, subscription


@contextmanager
def network(app):
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def cookie(client):
    return '__Host-bod-session=' + client.get_cookie('__Host-bod-session', domain='bank.example.test').value


def entitled(app, state, origin, subject, customer):
    client = app.test_client()
    assert login(client, state, origin, subject).status_code == 302
    assert post(client, '/billing/checkout', origin).status_code == 303
    state['subscriptions'][customer] = [subscription()]
    assert signed_event(client, origin, 'evt_' + customer, customer).status_code == 200
    return client


def connect(base, origin, session_cookie=None, **kwargs):
    return create_connection(base.replace('http:', 'ws:') + '/api/updates',
                             host='bank.example.test', origin=origin,
                             cookie=session_cookie, timeout=2, **kwargs)


def invalidation(ws):
    ws.settimeout(2)
    assert json.loads(ws.recv()) == {'type': 'state_changed'}


def quiet(ws):
    ws.settimeout(0.25)
    with pytest.raises(WebSocketTimeoutException):
        ws.recv()


def test_family_scoped_post_commit_notifications_and_reconnect(hosted_app):
    app, host, state, origin = hosted_app
    a = entitled(app, state, origin, 'parent-a', 'cus_1')
    b = entitled(app, state, origin, 'parent-b', 'cus_2')
    with network(app) as base:
        headers = {'Host': 'bank.example.test', 'Origin': origin,
                   'Cookie': cookie(a), 'X-CSRF-Token': csrf(a)}
        def http(method, path, **kwargs):
            return requests.request(method, base + path, headers=headers, timeout=5, **kwargs)
        sockets = []
        try:
            # Two devices in A and an independent B must not share notifications.
            for client in (a, a, b):
                sockets.append(connect(base, origin, cookie(client)))
                invalidation(sockets[-1])
            first, second, other_family = sockets
            response = http('POST', '/api/admin/kid', json={'name': 'Only A'})
            assert response.status_code == 200 and response.json()['ok']
            for ws in (first, second):
                invalidation(ws)
                assert http('GET', '/api/state').json()['kids'][0]['name'] == 'Only A'
            quiet(other_family)
            assert call(b, '/api/state', origin).json['kids'] == []
            response = http('POST', '/api/admin/gift', json={'kid_id': 1, 'amount_cents': 125})
            assert response.json()['ok']
            for ws in (first, second):
                invalidation(ws)
                assert http('GET', '/api/state').json()['kids'][0]['wallet_cents'] == 625
            quiet(other_family)
            # Failed business result and a CSRF-denied request emit nothing.
            assert not http('POST', '/api/spend', json={'kid_id': 1, 'amount_cents': 0}).json()['ok']
            denied = requests.post(base + '/api/admin/gift', headers={
                'Host': 'bank.example.test', 'Cookie': cookie(a)},
                json={'kid_id': 1, 'amount_cents': 999}, timeout=5)
            assert denied.status_code == 403
            for ws in sockets:
                quiet(ws)
            first.close()
            assert http('POST', '/api/admin/gift', json={'kid_id': 1, 'amount_cents': 50}).json()['ok']
            invalidation(second)
            quiet(other_family)
            replacement = connect(base, origin, cookie(a))
            sockets.append(replacement)
            invalidation(replacement)  # Reconnect resyncs the missed committed change.
            assert http('GET', '/api/state').json()['kids'][0]['wallet_cents'] == 675
        finally:
            for ws in sockets:
                ws.close()


def test_handshake_requires_session_entitlement_and_exact_trusted_origin(hosted_app):
    app, host, state, origin = hosted_app
    a = entitled(app, state, origin, 'parent-a', 'cus_1')
    unpaid = app.test_client()
    login(unpaid, state, origin, 'unpaid-parent')
    with network(app) as base:
        for session_cookie, expected in ((None, 401), (cookie(unpaid), 402)):
            with pytest.raises(WebSocketBadStatusException) as error:
                connect(base, origin, session_cookie)
            assert error.value.status_code == expected
        for bad_origin in ('null', 'https://evil.example', 'http://bank.example.test'):
            with pytest.raises(WebSocketBadStatusException) as error:
                connect(base, bad_origin, cookie(a))
            assert error.value.status_code == 403
        with pytest.raises(WebSocketBadStatusException) as error:
            connect(base, origin, cookie(a), suppress_origin=True)
        assert error.value.status_code == 403
        ws = connect(base, origin, cookie(a))  # HTTPS browser -> HTTP proxy backend works.
        try:
            invalidation(ws)
        finally:
            ws.close()


@pytest.mark.parametrize('revoke', ['logout', 'session_expiry', 'entitlement_expiry'])
def test_open_socket_reauthorizes_and_closes_without_further_notifications(hosted_app, revoke):
    app, host, state, origin = hosted_app
    a = entitled(app, state, origin, 'parent-a', 'cus_1')
    with network(app) as base:
        saved_cookie = cookie(a)
        ws = connect(base, origin, saved_cookie)
        try:
            invalidation(ws)
            if revoke == 'logout':
                assert post(a, '/auth/logout', origin).status_code == 302
            elif revoke == 'session_expiry':
                import sqlite3
                with sqlite3.connect(host.root / 'sessions.sqlite') as conn:
                    conn.execute("UPDATE sessions SET expiry='2000-01-01 00:00:00'")
            else:
                with host.connect() as conn:
                    conn.execute('UPDATE families SET paid_until=?', (time.time() - 1,))
            # No mutation needed: idle connections must not survive revoked access.
            ws.settimeout(3)
            try:
                assert ws.recv() == ''
            except WebSocketConnectionClosedException:
                pass
            with pytest.raises(WebSocketBadStatusException) as error:
                connect(base, origin, saved_cookie)
            assert error.value.status_code == (402 if revoke == 'entitlement_expiry' else 401)
        finally:
            ws.close()
