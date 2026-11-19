"""Real workerd + disposable REST fixture; never contacts Stripe."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time
import httpx
from test_billing import billing, post, record, subscription
from test_oauth import client, login, csrf


def pending(runtime, oidc, stripe, c, name):
    csrf(c); login(c, oidc, name); token = csrf(c)
    stripe.checkout_failures = 1
    try:
        assert post(c, runtime, '/billing/checkout', token).status_code == 503
    finally:
        stripe.checkout_failures = 0
    path, family = record(runtime, c)
    return token, path, family


def legacy(stripe, path, family):
    attempt = 'a' * 32
    with sqlite3.connect(path) as sql:
        sql.execute('UPDATE _bod_billing SET attempt=?', (attempt,))
    key = 'bod-checkout-' + family['id'] + '-' + attempt
    form = next(q.copy() for m, p, q, _ in reversed(stripe.calls)
                if p == '/v1/checkout/sessions' and m == 'POST')
    form.pop('managed_payments[enabled]', None)
    form = {k: v for k, v in form.items() if not k.startswith('metadata[')}
    stripe.key_params[key] = form
    stripe.keys[key] = {'error': {'type': 'invalid_request_error'}}
    return key, form


def test_legacy_cached_shape_conflict_fails_closed_actionably(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        token, path, family = pending(runtime, oidc, stripe, c, 'old-cache')
        key, form = legacy(stripe, path, family)
        headers = {'Authorization': 'Bearer ' + stripe.key, 'Stripe-Version': '2025-03-31.basil', 'Idempotency-Key': key}
        result = httpx.post(stripe.origin + '/v1/checkout/sessions', data=dict(form, **{'managed_payments[enabled]': 'false'}), headers=headers)
        assert result.status_code == 409 and result.json()['error']['type'] == 'idempotency_error'
        before = len(stripe.checkouts), len(stripe.calls)
        result = post(c, runtime, '/billing/checkout', token)
        assert result.status_code == 503 and 'recovery' in result.text.lower()
        assert len(stripe.checkouts) == before[0]
        assert not any(m == 'POST' and p == '/v1/checkout/sessions' for m,p,_,_ in stripe.calls[before[1]:])


def test_legacy_lost_response_recovers_unique_matching_session(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        token, path, family = pending(runtime, oidc, stripe, c, 'old-lost')
        _, form = legacy(stripe, path, family)
        old = dict(id='cs_legacy', customer=family['customer'], livemode=False, mode='subscription', metadata={},
                   created=int(time.time()), status='open', expires_at=int(time.time()) + 1800,
                   success_url=form['success_url'], cancel_url=form['cancel_url'], url='https://checkout.stripe.com/c/pay/legacy')
        stripe.checkouts[old['id']] = old
        stripe.checkouts['cs_expired_other'] = dict(old, id='cs_expired_other', status='expired')
        before = len(stripe.checkouts)
        result = post(c, runtime, '/billing/checkout', token)
        assert result.status_code == 303 and result.headers['location'] == old['url']
        with sqlite3.connect(path) as sql:
            assert sql.execute('SELECT checkout_id,attempt FROM _bod_billing').fetchone() == (old['id'], None)
        assert len(stripe.checkouts) == before


def test_new_lost_response_and_concurrent_retry_creates_at_most_once(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'new-lost'); token = csrf(c)
        before = len(stripe.checkouts)
        stripe.checkout_lost_responses = 1
        assert post(c, runtime, '/billing/checkout', token).status_code == 503
        assert len(stripe.checkouts) == before + 1
        stripe.session_list_fail = True
        try:
            assert post(c, runtime, '/billing/checkout', token).status_code == 503
        finally:
            stripe.session_list_fail = False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: post(c, runtime, '/billing/checkout', token), range(2)))
        assert all(r.status_code == 303 for r in results)
        assert len(stripe.checkouts) == before + 1
        with sqlite3.connect(record(runtime, c)[0]) as sql:
            assert sql.execute('SELECT checkout_id,attempt FROM _bod_billing').fetchone()[1] is None


def test_new_pending_request_snapshot_survives_payload_upgrade(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        token, path, family = pending(runtime, oidc, stripe, c, 'snapshot')
        with sqlite3.connect(path) as sql:
            raw = sql.execute('SELECT attempt FROM _bod_billing').fetchone()[0]
            attempt = json.loads(raw)
            # A future upgrade must replay the saved body, not rebuild it.
            attempt['values']['cancel_url'] += '?old=1'
            sql.execute('UPDATE _bod_billing SET attempt=?', (json.dumps(attempt),))
        result = post(c, runtime, '/billing/checkout', token)
        assert result.status_code == 303
        assert stripe.calls[-1][2]['cancel_url'].endswith('?old=1')


def test_legacy_ambiguous_candidates_do_not_create(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        token, path, family = pending(runtime, oidc, stripe, c, 'legacy-ambiguous')
        _, form = legacy(stripe, path, family)
        obj = dict(customer=family['customer'], livemode=False, mode='subscription', metadata={},
                   created=int(time.time()), status='open', expires_at=int(time.time()) + 1800,
                   success_url=form['success_url'], cancel_url=form['cancel_url'], url='https://checkout.stripe.com/c/pay/legacy')
        stripe.checkouts['cs_ambiguous1'] = dict(obj, id='cs_ambiguous1')
        stripe.checkouts['cs_ambiguous2'] = dict(obj, id='cs_ambiguous2')
        before = len(stripe.checkouts)
        response = post(c, runtime, '/billing/checkout', token)
        assert response.status_code == 503 and 'recovery' in response.text.lower()
        assert len(stripe.checkouts) == before
        # Even a closed Checkout cannot bypass a current nonterminal subscription.
        stripe.subs[family['customer']] = [subscription(family['customer'], status='past_due')]
        assert post(c, runtime, '/billing/checkout', token).headers['location'].startswith('https://billing.stripe.com/')
        assert len(stripe.checkouts) == before


def test_pending_after_key_retention_is_not_replayed(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        token, path, family = pending(runtime, oidc, stripe, c, 'retention')
        with sqlite3.connect(path) as sql:
            attempt = json.loads(sql.execute('SELECT attempt FROM _bod_billing').fetchone()[0])
            attempt['created'] = time.time() - 86400
            sql.execute('UPDATE _bod_billing SET attempt=?', (json.dumps(attempt),))
        before = len(stripe.checkouts)
        response = post(c, runtime, '/billing/checkout', token)
        assert response.status_code == 503 and 'recovery' in response.text.lower()
        assert len(stripe.checkouts) == before
