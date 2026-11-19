"""Real Stripe SDK HTTP against local REST provider, not Stripe acceptance."""
from concurrent.futures import ThreadPoolExecutor
import json
import time
import pytest
from test_hosted import hosted_app, login, post


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
