"""Real Stripe SDK HTTP against local REST provider, not Stripe acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import threading
import time
import pytest
from test_hosted import hosted_app, login, post, subscription


def test_ambiguous_post_durable_snapshot_and_concurrent_recovery(hosted_app):
    app, host, state, origin = hosted_app
    c = app.test_client()
    login(c, state, origin)
    state['lose_checkout'] = True
    assert post(c, '/billing/checkout', origin).status_code == 503
    with host.connect() as conn:
        row = conn.execute('SELECT * FROM families WHERE customer IS NOT NULL').fetchone()
        attempt = json.loads(row['checkout_attempt'])
        assert row['checkout_id'] is None
    # Genuine HTTP 409 when a captured key is reused with changed parameters.
    changed = dict(attempt['values'], cancel_url=origin + '/changed')
    with pytest.raises(host.stripe_module.StripeError) as conflict:
        host.stripe.v1.checkout.sessions.create(changed, options={'idempotency_key': attempt['key']})
    assert conflict.value.http_status == 409
    assert conflict.value.json_body['error']['type'] == 'idempotency_error'
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: post(c, '/billing/checkout', origin), range(2)))
    assert all(r.status_code == 303 for r in responses)
    assert len(state['checkouts']) == 1
    with host.connect() as conn:
        row = conn.execute('SELECT * FROM families WHERE customer IS NOT NULL').fetchone()
        assert row['checkout_id'] == 'cs_1' and row['checkout_attempt'] == 'ready'


def test_legacy_customer_without_durable_request_fails_closed(hosted_app):
    app, host, state, origin = hosted_app
    c = app.test_client()
    login(c, state, origin)
    # Existing pre-upgrade customer, possibly issued a Checkout in an old time window.
    with host.connect() as conn:
        conn.execute("UPDATE families SET customer='cus_legacy',checkout_attempt=NULL")
    response = post(c, '/billing/checkout', origin)
    assert response.status_code == 503 and b'recovery' in response.data
    assert not state['checkouts']


def test_old_pending_key_not_replayed_after_retention_window(hosted_app):
    app, host, state, origin = hosted_app
    c = app.test_client(); login(c, state, origin)
    state['lose_checkout'] = True
    assert post(c, '/billing/checkout', origin).status_code == 503
    with host.connect() as conn:
        row = conn.execute('SELECT * FROM families WHERE customer IS NOT NULL').fetchone()
        attempt = json.loads(row['checkout_attempt']); attempt['created'] = time.time() - 86400
        conn.execute('UPDATE families SET checkout_attempt=?', (json.dumps(attempt),))
    state['checkout_objects'].clear()
    assert post(c, '/billing/checkout', origin).status_code == 503
    assert len(state['checkouts']) == 1


def pending_checkout(hosted_app, *, saved_old=False):
    app, host, state, origin = hosted_app
    c = app.test_client(); login(c, state, origin)
    if saved_old:
        assert post(c, '/billing/checkout', origin).status_code == 303
        state['checkout_objects']['cs_1']['status'] = 'expired'
    state['lose_checkout'] = True
    assert post(c, '/billing/checkout', origin).status_code == 503
    with host.connect() as conn:
        attempt = json.loads(conn.execute('SELECT checkout_attempt FROM families WHERE customer IS NOT NULL').fetchone()[0])
    return c, attempt


@pytest.mark.parametrize('orphan', [False, True])
def test_legacy_saved_expired_id_does_not_authorize_replacement(hosted_app, orphan):
    app, host, state, origin = hosted_app
    c = app.test_client(); login(c, state, origin)
    assert post(c, '/billing/checkout', origin).status_code == 303
    old = state['checkout_objects']['cs_1']
    old['status'] = 'expired'
    if orphan:
        state['checkout_objects']['cs_orphan'] = dict(old, id='cs_orphan', status='open', metadata={})
    state['checkout_page_size'] = 1
    with host.connect() as conn:
        conn.execute('UPDATE families SET checkout_attempt=NULL,checkout_expires=?', (time.time() - 3600,))
    response = post(c, '/billing/checkout', origin)
    assert response.status_code == 503 and b'recovery' in response.data
    assert len(state['checkouts']) == 1
    assert len(state['checkout_list_calls']) == (2 if orphan else 1)
    with host.connect() as conn:
        assert conn.execute('SELECT checkout_attempt FROM families WHERE customer IS NOT NULL').fetchone()[0] is None


@pytest.mark.parametrize('saved_old', [False, True])
@pytest.mark.parametrize('age', [3600, 2 * 86400])
def test_expired_lost_response_rotates_once_and_recovers_second_lost_response(hosted_app, saved_old, age):
    app, host, state, origin = hosted_app
    c, previous = pending_checkout(hosted_app, saved_old=saved_old)
    old_count = len(state['checkouts'])
    state['checkout_objects']['cs_' + str(old_count)].update(status='expired', expires_at=int(time.time()) - 1)
    with host.connect() as conn:
        previous['created'] = time.time() - age
        conn.execute('UPDATE families SET checkout_attempt=?', (json.dumps(previous),))
    state['checkout_page_size'] = 1
    state['lose_checkout'] = True
    assert post(c, '/billing/checkout', origin).status_code == 503
    with host.connect() as conn:
        next_attempt = json.loads(conn.execute('SELECT checkout_attempt FROM families WHERE customer IS NOT NULL').fetchone()[0])
    assert next_attempt['key'] != previous['key']
    assert next_attempt['values']['metadata']['bod_attempt'] == next_attempt['nonce']
    assert len(state['checkouts']) == old_count + 1
    assert post(c, '/billing/checkout', origin).status_code == 303
    assert post(c, '/billing/checkout', origin).status_code == 303
    assert len(state['checkouts']) == old_count + 1
    assert sum(s['status'] == 'open' for s in state['checkout_objects'].values()) == 1


@pytest.mark.parametrize('status', ['past_due', 'unpaid', 'incomplete', 'active'])
def test_expired_pending_checkout_checks_subscriptions_first(hosted_app, status):
    app, host, state, origin = hosted_app
    c, attempt = pending_checkout(hosted_app)
    state['checkout_objects']['cs_1']['status'] = 'expired'
    state['subscriptions']['cus_1'] = [subscription(status=status)]
    response = post(c, '/billing/checkout', origin)
    assert response.status_code == (302 if status == 'active' else 303)
    assert response.location == ('/billing' if status == 'active' else 'https://billing.stripe.com/synthetic')
    assert len(state['checkouts']) == 1
    assert not state.get('checkout_list_calls')


@pytest.mark.parametrize('problem', ['api', 'duplicate', 'unmatched', 'mode', 'unknown'])
def test_terminal_attempt_never_rotates_when_reconciliation_is_ambiguous(hosted_app, problem):
    app, host, state, origin = hosted_app
    c, attempt = pending_checkout(hosted_app)
    obj = state['checkout_objects']['cs_1']
    obj['status'] = 'expired'
    state['checkout_page_size'] = 1
    if problem == 'api':
        state['fail_checkout_list'] = True
    elif problem in ('duplicate', 'unmatched'):
        state['checkout_objects']['cs_ambiguous'] = dict(obj, id='cs_ambiguous', status='open',
            metadata=obj['metadata'] if problem == 'duplicate' else {})
    elif problem == 'mode':
        obj['livemode'] = True
    else:
        obj['status'] = 'unexpected'
    assert post(c, '/billing/checkout', origin).status_code == 503
    assert len(state['checkouts']) == 1
    with host.connect() as conn:
        retained = json.loads(conn.execute('SELECT checkout_attempt FROM families WHERE customer IS NOT NULL').fetchone()[0])
    assert retained == attempt


def test_concurrent_expired_attempt_rotates_to_one_payable_session(hosted_app, monkeypatch):
    app, host, state, origin = hosted_app
    c, attempt = pending_checkout(hosted_app)
    state['checkout_objects']['cs_1']['status'] = 'expired'
    holding, release, contending = threading.Event(), threading.Event(), threading.Event()
    state['pause_subscriptions'] = (holding, release)
    local = threading.local()
    connect = host.connect

    @contextmanager
    def traced_connect():
        with connect() as conn:
            def trace(sql):
                if getattr(local, 'second', False) and sql == 'BEGIN IMMEDIATE':
                    contending.set()
            conn.set_trace_callback(trace)
            yield conn

    monkeypatch.setattr(host, 'connect', traced_connect)
    cookie = c.get_cookie('__Host-bod-session', domain='bank.example.test')

    def checkout(second):
        local.second = second
        client = app.test_client()
        client.set_cookie('__Host-bod-session', cookie.value, domain='bank.example.test')
        return post(client, '/billing/checkout', origin)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(checkout, False)
        try:
            assert holding.wait(5)
            second = pool.submit(checkout, True)
            # SQLite's trace fires on BEGIN before acquiring the writer lock:
            # this proves real contention, not just worker readiness.
            assert contending.wait(5)
            assert len(state['checkouts']) == 1
        finally:
            release.set()
        responses = [first.result(), second.result()]
    assert all(r.status_code == 303 for r in responses)
    assert len(state['checkouts']) == 2
    assert sum(s['status'] == 'open' for s in state['checkout_objects'].values()) == 1
