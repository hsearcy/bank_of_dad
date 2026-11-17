"""Real Authlib code/PKCE/JWKS and Stripe SDK HTTP against synthetic providers.

Never contacts real identity/billing accounts. All bank state is disposable.
"""
import base64
import hashlib
import hmac
import importlib
import json
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import requests
from flask import Flask, jsonify, redirect, request
from joserfc import jwt
from joserfc.jwk import RSAKey
from werkzeug.serving import make_server


@pytest.fixture
def hosted_app(tmp_path, monkeypatch):
    issuer = 'https://identity.example.test/'
    origin = 'https://bank.example.test'
    settings = {
        'BANK_OF_DAD_MODE': 'hosted', 'BOD_PUBLIC_ORIGIN': origin,
        'BOD_OIDC_ISSUER': issuer, 'BOD_OIDC_CLIENT_ID': 'test-client',
        'BOD_OIDC_CLIENT_SECRET': 'synthetic-client-secret',
        'BOD_HOSTED_DATA': str(tmp_path / 'hosted'), 'BOD_STRIPE_PRICE_ID': 'price_family',
        'BOD_STRIPE_WEBHOOK_SECRET': 'whsec_synthetic', 'BOD_STRIPE_LIVEMODE': 'false',
        'BOD_STRIPE_SECRET_KEY': 'sk_test_synthetic', 'BOD_SESSION_SECRET': 's' * 40,
        'BANK_OF_DAD_DB': str(tmp_path / 'must-not-exist.sqlite'),
        'AUTHLIB_INSECURE_TRANSPORT': '1',
    }
    for k, v in settings.items():
        monkeypatch.setenv(k, v)
    provider = Flask('synthetic-providers')
    key = RSAKey.generate_key(2048, parameters={'kid': 'one'})
    state = {'subject': 'parent-a', 'claims': {}, 'codes': {}, 'customers': [],
             'checkouts': [], 'portals': [], 'subscriptions': {}, 'token_calls': 0,
             'subscription_calls': 0, 'fail': False, 'price_amount': 999}
    state['key'] = key

    @provider.get('/.well-known/openid-configuration')
    def discovery():
        return jsonify(issuer=issuer, authorization_endpoint=base + '/authorize',
                       token_endpoint=base + '/token', jwks_uri=base + '/jwks',
                       id_token_signing_alg_values_supported=['RS256'],
                       response_types_supported=['code'], token_endpoint_auth_methods_supported=['client_secret_basic'])

    @provider.get('/jwks')
    def jwks():
        return jsonify(keys=[state['key'].as_dict(private=False)])

    @provider.get('/authorize')
    def authorize():
        assert request.args['scope'] == 'openid'
        assert request.args['code_challenge_method'] == 'S256'
        code = secrets.token_urlsafe(20)
        state['codes'][code] = dict(request.args)
        state['codes'][code]['subject'] = state['subject']
        return redirect(request.args['redirect_uri'] + '?' + urlencode({'state': request.args['state'], 'code': code}))

    @provider.post('/token')
    def token():
        state['token_calls'] += 1
        data = state['codes'].pop(request.form['code'])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(request.form['code_verifier'].encode()).digest()).decode().rstrip('=')
        if challenge != data['code_challenge']:
            return jsonify(error='invalid_grant'), 400
        now = int(time.time())
        claims = dict(iss=issuer, sub=data['subject'], aud='test-client', iat=now,
                      exp=now + 300, nonce=data['nonce'])
        claims.update(state['claims'])
        signing_key = state.get('signing_key', state['key'])
        signed = jwt.encode({'alg': state.get('algorithm', 'RS256'), 'kid': signing_key.kid}, claims, signing_key)
        return jsonify(access_token='synthetic-access-token', token_type='Bearer', id_token=signed)

    @provider.get('/v1/prices/price_family')
    def price():
        return jsonify(id='price_family', object='price', active=True, livemode=False,
                       currency='usd', unit_amount=state['price_amount'], recurring={'interval': 'year', 'interval_count': 1})

    @provider.post('/v1/customers')
    def customer():
        assert not request.form  # No names, emails, addresses or metadata sent.
        cid = 'cus_' + str(len(state['customers']) + 1)
        state['customers'].append(cid)
        return jsonify(id=cid, object='customer')

    @provider.get('/v1/subscriptions')
    def subscriptions():
        state['subscription_calls'] += 1
        if state['fail']:
            return jsonify(error={'type': 'api_error', 'message': 'Synthetic outage'}), 503
        snapshot = json.loads(json.dumps(state['subscriptions'].get(request.args['customer'], [])))
        pause = state.pop('pause_subscriptions', None)
        if pause:
            pause[0].set()
            assert pause[1].wait(timeout=10)
        return jsonify(object='list', data=snapshot,
                       has_more=False, url='/v1/subscriptions')

    @provider.post('/v1/checkout/sessions')
    def checkout():
        data = request.form.to_dict()
        assert data['mode'] == 'subscription'
        assert data['line_items[0][price]'] == 'price_family'
        assert data['line_items[0][quantity]'] == '1'
        state['checkouts'].append(data)
        return jsonify(id='cs_' + str(len(state['checkouts'])), object='checkout.session',
                       url='https://checkout.stripe.com/c/pay/synthetic', expires_at=int(time.time()) + 86400)

    @provider.post('/v1/billing_portal/sessions')
    def portal():
        state['portals'].append(request.form.to_dict())
        return jsonify(id='bps_synthetic', object='billing_portal.session', url='https://billing.stripe.com/synthetic')

    @provider.get('/v1/checkout/sessions/<checkout_id>')
    def retrieve_checkout(checkout_id):
        return jsonify(id=checkout_id, object='checkout.session', status=state.get('checkout_status', 'open'),
                       subscription='sub_synthetic',
                       url='https://checkout.stripe.com/c/pay/synthetic')

    @provider.get('/v1/subscriptions/sub_synthetic')
    def retrieve_subscription():
        return jsonify(id='sub_synthetic', object='subscription', status=state.get('prior_status', 'incomplete'))

    server = make_server('127.0.0.1', 0, provider, threaded=True)
    base = 'http://127.0.0.1:' + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    import db
    monkeypatch.setattr(db, 'DB_PATH', settings['BANK_OF_DAD_DB'])
    import app as module
    module = importlib.reload(module)
    app = module.app
    app.testing = True
    host = app.extensions['bod_hosted']
    host.oauth._server_metadata_url = base + '/.well-known/openid-configuration'
    import stripe
    host.stripe = stripe.StripeClient('sk_test_synthetic', base_addresses={'api': base},
                                      max_network_retries=0, http_client=stripe.RequestsClient(timeout=2))
    yield app, host, state, origin
    server.shutdown()
    thread.join()


def call(client, path, origin, **kwargs):
    return client.open(path, base_url=origin, **kwargs)


def login(client, state, origin, subject='parent-a'):
    state['subject'] = subject
    start = call(client, '/auth/login', origin)
    assert start.status_code == 302
    auth = requests.get(start.location, allow_redirects=False, timeout=3)
    callback = urlsplit(auth.headers['Location'])
    result = call(client, callback.path + '?' + callback.query, origin)
    return result


def csrf(client):
    with client.session_transaction(base_url='https://bank.example.test') as session:
        return session['csrf']


def post(client, path, origin, **kwargs):
    return call(client, path, origin, method='POST', headers={'X-CSRF-Token': csrf(client)}, **kwargs)


def signed_event(client, origin, event_id='evt_paid', customer='cus_1', event_type='customer.subscription.updated', **changes):
    payload = dict(id=event_id, object='event', type=event_type, livemode=False,
                   data={'object': {'customer': customer}})
    stamp = changes.pop('timestamp', int(time.time()))
    payload.update(changes)
    raw = json.dumps(payload).encode()
    sig = hmac.new(b'whsec_synthetic', str(stamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return call(client, '/billing/webhook', origin, method='POST', data=raw,
                headers={'Stripe-Signature': f't={stamp},v1={sig}', 'Content-Type': 'application/json'})


def subscription(status='active', price='price_family', until=None):
    return dict(id='sub_synthetic', object='subscription', status=status, livemode=False,
                items={'data': [{'price': {'id': price}, 'quantity': 1,
                                  'current_period_end': until or int(time.time()) + 86400}]})


def test_full_parent_checkout_entitlement_bank_portal_logout(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    assert call(a, '/api/state', origin).status_code == 401
    assert login(a, state, origin).status_code == 302
    assert call(a, '/api/state', origin).status_code == 402
    assert call(a, '/', origin).location == '/billing'
    page = call(a, '/billing', origin)
    assert b'$9.99 USD per year' in page.data
    assert call(a, '/billing/checkout', origin, method='POST').status_code == 403
    checkout = post(a, '/billing/checkout', origin, data={'customer': 'cus_attacker', 'family': 'other'})
    assert checkout.status_code == 303
    assert checkout.location.startswith('https://checkout.stripe.com/')
    assert state['checkouts'][0]['customer'] == 'cus_1'
    assert state['checkouts'][0]['success_url'] == origin + '/billing'
    assert call(a, '/api/state', origin).status_code == 402  # No entitlement from redirect.
    assert post(a, '/billing/checkout', origin).status_code == 303
    assert len(state['customers']) == len(state['checkouts']) == 1
    state['subscriptions']['cus_1'] = [subscription()]
    assert signed_event(a, origin).status_code == 200
    assert post(a, '/api/admin/kid', origin, json={'name': 'Nickname A'}).json['ok']
    assert post(a, '/api/admin/gift', origin, json={'kid_id': 1, 'amount_cents': 200, 'note': 'Chores'}).json['ok']
    assert post(a, '/api/save', origin, json={'kid_id': 1, 'amount_cents': 100}).json['ok']
    snapshot = call(a, '/api/state', origin)
    assert snapshot.json['kids'][0]['name'] == 'Nickname A'
    assert snapshot.json['kids'][0]['wallet_cents'] == 600
    assert snapshot.json['kids'][0]['savings_cents'] == 100
    assert call(a, '/api/admin/status', origin).json == {'pin_set': False, 'hosted_parent': True}
    assert call(a, '/', origin).headers['Cache-Control'] == 'no-store'
    assert b'csrf-token' in call(a, '/', origin).data
    assert b'no fetch handler' in call(a, '/sw.js', origin).data
    assert post(a, '/billing/portal', origin, data={'customer': 'cus_attacker'}).status_code == 303
    assert state['portals'] == [{'customer': 'cus_1', 'return_url': origin + '/billing'}]
    cookie = a.get_cookie('__Host-bod-session', domain='bank.example.test')
    assert cookie.secure and cookie.http_only and cookie.same_site == 'Lax'
    assert post(a, '/auth/logout', origin).status_code == 302
    assert call(a, '/api/state', origin).status_code == 401
    replay = app.test_client()
    replay.set_cookie('__Host-bod-session', cookie.value, domain='bank.example.test')
    assert call(replay, '/api/state', origin).status_code == 401
    assert login(a, state, origin).status_code == 302
    assert call(a, '/api/state', origin).json['kids'][0]['wallet_cents'] == 600
    assert not __import__('pathlib').Path(__import__('db').DB_PATH).exists()


def test_two_families_all_existing_routes_are_isolated(hosted_app):
    app, host, state, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    state['claims'] = {'email': 'same-email@example.test'}  # Never an identity or linking key.
    for client, subject, customer in ((a, 'parent-a', 'cus_1'), (b, 'parent-b', 'cus_2')):
        assert login(client, state, origin, subject).status_code == 302
        assert post(client, '/billing/checkout', origin).status_code == 303
        state['subscriptions'][customer] = [subscription()]
        assert signed_event(client, origin, 'evt_' + customer, customer).status_code == 200
    assert post(a, '/api/admin/kid', origin, json={'name': 'Only A'}).json['ok']
    assert call(b, '/api/state?family=attacker', origin).json['kids'] == []
    assert call(b, '/api/transactions?kid_id=1', origin).json['transactions'] == []
    with host.connect() as conn:
        family_a = conn.execute("SELECT id FROM families WHERE subject='parent-a'").fetchone()['id']
    path_a = host.family_path(family_a)
    before = __import__('pathlib').Path(path_a).read_bytes()
    # Every mutator and object identifier is routed to B's database, including tx IDs.
    bodies = {
        '/api/collect': {'kid_id': 1}, '/api/save': {'kid_id': 1, 'amount_cents': 1},
        '/api/withdraw': {'kid_id': 1, 'amount_cents': 1},
        '/api/bond': {'kid_id': 1, 'amount_cents': 1, 'term': '1mo'},
        '/api/spend': {'kid_id': 1, 'amount_cents': 1},
        '/api/admin/gift': {'kid_id': 1, 'amount_cents': 1},
        '/api/admin/kid/update': {'kid_id': 1, 'name': 'Stolen'},
        '/api/admin/kid/allowance': {'kid_id': 1, 'amount_cents': 1, 'period_days': 1},
        '/api/admin/tx/delete': {'tx_id': 1}, '/api/admin/tx/edit': {'tx_id': 1, 'amount_cents': 1},
        '/api/admin/rates': {'savings_weekly_pct': 1}, '/api/admin/fastforward': {'days': 1},
    }
    for route, body in bodies.items():
        result = post(b, route, origin, json={**body, 'family': family_a, 'customer': 'cus_1'})
        assert result.status_code == (404 if 'kid_id' in body or 'tx_id' in body else 200)
    assert __import__('pathlib').Path(path_a).read_bytes() == before
    assert post(b, '/api/admin/kid', origin, json={'name': 'Only B'}).json['ok']
    assert call(b, '/api/state', origin).json['kids'][0]['name'] == 'Only B'
    assert call(a, '/api/state', origin).json['kids'][0]['name'] == 'Only A'


@pytest.mark.parametrize('claims', [
    {'iss': 'https://attacker.example/'}, {'aud': 'other'}, {'nonce': 'wrong'},
    {'exp': 1}, {'iat': 1}, {'iat': 9999999999}, {'azp': 'wrong'}, {'sub': ''},
])
def test_oidc_claim_rejection(hosted_app, claims):
    app, host, state, origin = hosted_app
    state['claims'] = claims
    client = app.test_client()
    assert login(client, state, origin).status_code == 400
    assert call(client, '/api/state', origin).status_code == 401
    with host.connect() as conn:
        assert conn.execute('SELECT count(*) FROM families').fetchone()[0] == 0


def test_callback_browser_binding_state_replay_and_expiry(hosted_app):
    app, host, state, origin = hosted_app
    a, b = app.test_client(), app.test_client()
    start = call(a, '/auth/login', origin)
    auth = requests.get(start.location, allow_redirects=False, timeout=3)
    callback = auth.headers['Location'].replace(origin, '')
    assert call(a, callback + '&state=duplicate', origin).status_code == 400
    assert call(b, callback, origin).status_code == 400
    assert state['token_calls'] == 0
    assert call(a, callback, origin).status_code == 302
    assert call(a, callback, origin).status_code == 400
    assert state['token_calls'] == 1
    start = call(b, '/auth/login', origin)
    auth = requests.get(start.location, allow_redirects=False, timeout=3)
    with host.connect() as conn:
        conn.execute('UPDATE login_states SET expires=0')
    assert call(b, auth.headers['Location'].replace(origin, ''), origin).status_code == 400
    assert state['token_calls'] == 1


def test_verified_webhooks_current_state_dedupe_retries_and_mode(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    post(a, '/billing/checkout', origin)
    assert call(a, '/billing/webhook', origin, method='POST', data=b'{}').status_code == 400
    assert signed_event(a, origin, timestamp=int(time.time()) + 1000).status_code == 400
    assert signed_event(a, origin, timestamp=1).status_code == 400
    assert signed_event(a, origin, livemode=True).status_code == 400
    assert signed_event(a, origin, account='acct_other').status_code == 400
    state['subscriptions']['cus_1'] = [subscription(price='price_other')]
    assert signed_event(a, origin, 'evt_otherprice').status_code == 200
    assert call(a, '/api/state', origin).status_code == 402
    state['subscriptions']['cus_1'] = [subscription()]
    assert signed_event(a, origin).status_code == 200
    calls = state['subscription_calls']
    assert signed_event(a, origin).status_code == 200
    assert state['subscription_calls'] == calls
    assert signed_event(a, origin, event_type='customer.subscription.deleted').status_code == 400
    state['subscriptions']['cus_1'] = [subscription(status='past_due')]
    assert signed_event(a, origin, 'evt_late_old_paid_event').status_code == 200
    assert call(a, '/api/state', origin).status_code == 402
    state['subscriptions']['cus_1'] = [subscription()]
    state['fail'] = True
    assert signed_event(a, origin, 'evt_retry').status_code == 503
    with host.connect() as conn:
        assert not conn.execute("SELECT 1 FROM stripe_events WHERE id='evt_retry'").fetchone()
    state['fail'] = False
    assert signed_event(a, origin, 'evt_retry').status_code == 200
    assert call(a, '/api/state', origin).status_code == 200
    state['subscriptions']['cus_1'] = []
    assert post(a, '/billing/refresh', origin).status_code == 302
    assert call(a, '/api/state', origin).status_code == 402


def test_csrf_origin_host_and_price_fail_closed(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    state['price_amount'] = 1000
    assert post(a, '/billing/checkout', origin).status_code == 503
    assert not state['customers']
    assert call(a, '/billing', 'https://attacker.example').status_code == 400
    assert call(a, '/billing/checkout', origin, method='POST', headers={
        'Origin': 'https://attacker.example', 'X-CSRF-Token': csrf(a)}).status_code == 403


def test_oidc_signature_algorithm_rotation_and_pkce(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    state['signing_key'] = RSAKey.generate_key(2048, parameters={'kid': 'one'})
    assert login(a, state, origin).status_code == 400
    state.pop('signing_key')
    state['algorithm'] = 'RS512'
    assert login(a, state, origin).status_code == 400
    state['algorithm'] = 'RS256'
    assert login(a, state, origin).status_code == 302
    # Same long-lived Authlib client: an unknown kid forces JWKS refresh.
    state['key'] = RSAKey.generate_key(2048, parameters={'kid': 'rotated'})
    assert login(a, state, origin).status_code == 302
    with host.connect() as conn:
        assert conn.execute('SELECT count(*) FROM families').fetchone()[0] == 1
    start = call(a, '/auth/login', origin)
    auth = requests.get(start.location, allow_redirects=False, timeout=3)
    for code in state['codes'].values():
        code['code_challenge'] = 'mismatched-code-binding'
    assert call(a, auth.headers['Location'].replace(origin, ''), origin).status_code == 400
    assert call(a, '/api/state', origin).status_code == 401


def test_receipt_insert_failure_rolls_back_entitlement_and_retries(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    post(a, '/billing/checkout', origin)
    state['subscriptions']['cus_1'] = [subscription()]
    with host.connect() as conn:
        conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON stripe_events BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END")
    assert signed_event(a, origin, 'evt_rollback').status_code == 503
    with host.connect() as conn:
        assert conn.execute('SELECT count(*) FROM stripe_events').fetchone()[0] == 0
        assert conn.execute('SELECT paid_until FROM families').fetchone()[0] == 0
        conn.execute('DROP TRIGGER fail_receipt')
    assert signed_event(a, origin, 'evt_rollback').status_code == 200
    assert call(a, '/api/state', origin).status_code == 200


def test_concurrent_parent_balance_writes_are_serialized(hosted_app):
    from concurrent.futures import ThreadPoolExecutor
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    post(a, '/billing/checkout', origin)
    state['subscriptions']['cus_1'] = [subscription()]
    signed_event(a, origin)
    post(a, '/api/admin/kid', origin, json={'name': 'Concurrent Nickname'})
    cookie = a.get_cookie('__Host-bod-session', domain='bank.example.test').value
    token = csrf(a)
    barrier = threading.Barrier(8)
    def gift(_):
        client = app.test_client()
        client.set_cookie('__Host-bod-session', cookie, domain='bank.example.test')
        barrier.wait(timeout=10)
        result = call(client, '/api/admin/gift', origin, method='POST',
                      headers={'X-CSRF-Token': token}, json={'kid_id': 1, 'amount_cents': 1})
        assert result.status_code == 200 and result.json['ok']
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(gift, range(8)))
    assert call(a, '/api/state', origin).json['kids'][0]['wallet_cents'] == 508
    transactions = call(a, '/api/transactions?kid_id=1', origin).json['transactions']
    assert len(transactions) == 8
    assert all(t['amount_cents'] == 1 for t in transactions)


def test_period_cancellation_session_expiry_and_resubscribe(hosted_app):
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    post(a, '/billing/checkout', origin)
    state['checkout_status'] = 'complete'
    assert post(a, '/billing/checkout', origin).location == 'https://billing.stripe.com/synthetic'
    assert len(state['checkouts']) == 1  # Do not double-charge pending payments.
    state['prior_status'] = 'canceled'
    assert post(a, '/billing/checkout', origin).status_code == 303
    assert len(state['checkouts']) == 2  # A terminal old subscription permits resubscription.
    sub = subscription()
    sub['cancel_at_period_end'] = True
    state['subscriptions']['cus_1'] = [sub]
    signed_event(a, origin, 'evt_period_end_cancel')
    assert call(a, '/api/state', origin).status_code == 200
    sub['cancel_at'] = int(time.time()) - 1
    signed_event(a, origin, 'evt_earlier_cancel')
    assert call(a, '/api/state', origin).status_code == 402
    sub.pop('cancel_at')
    sub['items']['data'][0]['current_period_end'] = int(time.time()) - 1
    signed_event(a, origin, 'evt_period_expired')
    assert call(a, '/api/state', origin).status_code == 402
    with sqlite3.connect(host.root / 'sessions.sqlite') as conn:
        conn.execute("UPDATE sessions SET expiry='2000-01-01 00:00:00'")
    assert call(a, '/api/state', origin).status_code == 401


def test_concurrent_webhooks_cannot_overwrite_a_newer_snapshot(hosted_app, monkeypatch):
    from contextlib import contextmanager
    app, host, state, origin = hosted_app
    a = app.test_client()
    login(a, state, origin)
    post(a, '/billing/checkout', origin)
    state['subscriptions']['cus_1'] = [subscription()]
    fetched, release, second_attempt = threading.Event(), threading.Event(), threading.Event()
    state['pause_subscriptions'] = (fetched, release)
    original_connect = host.connect
    @contextmanager
    def traced_connect():
        with original_connect() as conn:
            if threading.current_thread().name == 'second-webhook':
                conn.set_trace_callback(lambda sql: second_attempt.set() if sql == 'BEGIN IMMEDIATE' else None)
            yield conn
    monkeypatch.setattr(host, 'connect', traced_connect)
    results = {}
    def deliver(name):
        results[name] = signed_event(app.test_client(), origin, name).status_code
    first = threading.Thread(target=deliver, args=('evt_first',), name='first-webhook')
    second = threading.Thread(target=deliver, args=('evt_second',), name='second-webhook')
    before = state['subscription_calls']
    first.start()
    try:
        assert fetched.wait(timeout=5)
        state['subscriptions']['cus_1'] = []
        second.start()
        assert second_attempt.wait(timeout=5)
        assert state['subscription_calls'] == before + 1
    finally:
        release.set()
        first.join(timeout=10)
        if second.ident:
            second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert results == {'evt_first': 200, 'evt_second': 200}
    assert state['subscription_calls'] == before + 2
    assert call(a, '/api/state', origin).status_code == 402
