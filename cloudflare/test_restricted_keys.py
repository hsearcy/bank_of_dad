"""Restricted-key regressions; disposable REST fixtures, never real Stripe keys."""
import copy
import time
from types import SimpleNamespace

import pytest

from test_runtime import Runtime
from test_oauth import Provider, client, csrf, login
from test_billing import StripeFixture, event, paid_invoice, post, record, subscription, webhook
from test_live_billing import LIVE, PUBLIC_LIVE, live_event
from test_public import PUBLIC, PublicRuntime, family_databases
from cloudflare_security import runtime_mode, stripe_config


def config(mode, key_kind='rk'):
    values = dict(LIVE, BOD_BILLING_MODE=mode,
                  BOD_STRIPE_LIVEMODE='true' if mode == 'live' else 'false')
    values['BOD_STRIPE_SECRET_KEY'] = (
        key_kind + '_live_local_fixture' if mode == 'live' else key_kind + '_test_fixture')
    return values


@pytest.mark.parametrize('mode', ['test', 'live'])
@pytest.mark.parametrize('key_kind', ['sk', 'rk'])
def test_matching_secret_and_restricted_key_modes(mode, key_kind):
    values = config(mode, key_kind)
    env = SimpleNamespace(**values, BOD_LOCAL_ONLY='true',
                          BOD_PUBLIC_ORIGIN='http://127.0.0.1:8787')
    assert runtime_mode(env) == 'local'
    result = stripe_config(env)
    assert result['key'] == values['BOD_STRIPE_SECRET_KEY']
    assert result['live'] is (mode == 'live')
    if mode == 'live':
        env.BOD_LOCAL_ONLY = 'false'
        env.BOD_PUBLIC_ORIGIN = PUBLIC
        env.BOD_PUBLIC_PILOT = 'false'
        assert runtime_mode(env) == 'public'
        assert stripe_config(env, public=True)['live'] is True
    else:
        with pytest.raises(ValueError):
            stripe_config(env, public=True)


@pytest.mark.parametrize('mode', ['test', 'live'])
@pytest.mark.parametrize('key_kind', ['sk', 'rk', 'pk', 'unknown'])
def test_wrong_mode_and_nonsecret_keys_rejected(mode, key_kind):
    values = config(mode)
    other = 'test' if mode == 'live' else 'live'
    values['BOD_STRIPE_SECRET_KEY'] = key_kind + '_' + other + '_not_a_real_key'
    with pytest.raises(ValueError):
        stripe_config(SimpleNamespace(**values))


@pytest.mark.parametrize('mode', ['test', 'live'])
@pytest.mark.parametrize('key_kind', ['pk', 'unknown'])
def test_matching_mode_is_not_enough_for_nonsecret_keys(mode, key_kind):
    with pytest.raises(ValueError):
        stripe_config(SimpleNamespace(**config(mode, key_kind)))


@pytest.mark.parametrize('change', [
    {'BOD_BILLING_MODE': 'test', 'BOD_STRIPE_LIVEMODE': 'false',
     'BOD_STRIPE_SECRET_KEY': 'rk_test_fixture'},
    {'BOD_STRIPE_SECRET_KEY': 'rk_test_fixture'},
    {'BOD_STRIPE_SECRET_KEY': 'pk_live_not_a_real_key'},
    {'BOD_STRIPE_LIVEMODE': 'false'}, {'BOD_STRIPE_ACCOUNT_ID': ''},
    {'BOD_STRIPE_PRODUCT_ID': ''}, {'BOD_STRIPE_PRICE_ID': ''},
    {'BOD_STRIPE_WEBHOOK_SECRET': ''}, {'BOD_PUBLIC_PILOT': 'true'},
    {'BOD_TEST_STRIPE_ORIGIN': 'http://127.0.0.1:9999'},
    {'BOD_TEST_OIDC_ORIGIN': 'http://127.0.0.1:9999'},
])
def test_restricted_public_config_stays_fail_closed(change):
    values = dict(PUBLIC_LIVE, **config('live'), BOD_LOCAL_ONLY='false',
                  BOD_PUBLIC_ORIGIN=PUBLIC)
    values.update(change)
    with pytest.raises(ValueError):
        runtime_mode(SimpleNamespace(**values))


@pytest.mark.parametrize('mode', ['test', 'live'])
def test_restricted_key_checkout_portal_and_settlement_in_workerd(tmp_path, mode):
    oidc, stripe = Provider(), StripeFixture(live=mode == 'live', key=config(mode)['BOD_STRIPE_SECRET_KEY'])
    values = dict(config(mode), BOD_LOCAL_ONLY='true',
                  BOD_OIDC_CLIENT_ID='fixture-client', BOD_OIDC_CLIENT_SECRET='fixture-secret',
                  BOD_TEST_OIDC_ORIGIN=oidc.origin, BOD_TEST_STRIPE_ORIGIN=stripe.origin)
    runtime = Runtime(tmp_path, values)
    try:
        runtime.start()
        with client(runtime) as c:
            csrf(c); login(c, oidc, 'restricted-' + mode); token = csrf(c)
            assert post(c, runtime, '/billing/checkout', token).status_code == 303
            _, family = record(runtime, c)
            customer = family['customer']
            assert family['paid_until'] == 0
            assert post(c, runtime, '/billing/portal', token).status_code == 303
            end = int(time.time()) + 86400
            sub = subscription(customer, end=end)
            sub['livemode'] = mode == 'live'
            sub['items']['data'][0]['price'] = copy.deepcopy(stripe.price)
            stripe.subs[customer] = [sub]
            paid_invoice(stripe, sub)
            make_event = live_event if mode == 'live' else event
            assert webhook(runtime, make_event(customer, 'restricted_paid')).status_code == 200
            assert record(runtime, c)[1]['paid_until'] == end
            paths = {path for _, path, _, _ in stripe.calls}
            assert {'/v1/checkout/sessions', '/v1/billing_portal/sessions',
                    '/v1/invoices', '/v1/invoice_payments', '/v1/payment_intents/pi_in_fixture'} <= paths
            # Same key type does not bypass provider object mode validation.
            stripe.customers[customer]['livemode'] = mode != 'live'
            assert webhook(runtime, make_event(customer, 'restricted_wrong_mode')).status_code == 503
            assert record(runtime, c)[1]['paid_until'] == end
        runtime.stop()
        calls = len(stripe.calls)
        # An arbitrary matching-prefix key must never reach the HTTP override.
        runtime.variables['BOD_STRIPE_SECRET_KEY'] = 'rk_' + mode + '_not_a_fixture'
        runtime.start()
        assert webhook(runtime, make_event(customer, 'restricted_not_dummy')).status_code == 503
        assert len(stripe.calls) == calls
    finally:
        runtime.stop(); oidc.stop(); stripe.stop()


@pytest.mark.parametrize('valid', [True, False])
def test_restricted_public_https_mode_boundary(tmp_path, valid):
    values = dict(PUBLIC_LIVE, **config('live' if valid else 'test'))
    runtime = PublicRuntime(tmp_path, values, readiness_status=200 if valid else 503)
    try:
        runtime.start()
        assert runtime.call('/')[0] == (200 if valid else 503)
        if valid:
            # Signed unmapped receipts never contact Stripe or confer paid access.
            import hashlib
            import hmac
            import httpx
            import json
            from test_billing import SECRET
            raw = json.dumps(live_event('cus_Unmapped', 'restricted_https')).encode()
            stamp = str(int(time.time()))
            sig = hmac.new(SECRET.encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
            result = httpx.post(runtime.origin + '/billing/webhook', content=raw, verify=False,
                headers={'Host': 'thebankofdad.app', 'CF-Connecting-IP': '192.0.2.42',
                         'Stripe-Signature': f't={stamp},v1={sig}'}, timeout=45)
            assert result.status_code == 200
        else:
            assert family_databases(runtime) == set()
    finally:
        runtime.stop()
