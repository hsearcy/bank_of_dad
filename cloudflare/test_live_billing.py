"""Disposable Stripe REST-shaped fixtures + real workerd; NOT Stripe acceptance."""
import copy
import sqlite3
import time
from types import SimpleNamespace

import pytest
from test_runtime import Runtime
from test_oauth import Provider, client, login, csrf
from test_billing import StripeFixture, SECRET, post, webhook, event, subscription, record, paid_invoice
from test_public import PublicRuntime, PUBLIC
from cloudflare_security import runtime_mode, stripe_config

LIVE = dict(BOD_BILLING_MODE='live', BOD_STRIPE_LIVEMODE='true',
    BOD_STRIPE_SECRET_KEY='sk_live_local_fixture', BOD_STRIPE_PRICE_ID='price_fixture',
    BOD_STRIPE_WEBHOOK_SECRET=SECRET, BOD_STRIPE_ACCOUNT_ID='acct_Fixture',
    BOD_STRIPE_PRODUCT_ID='prod_Fixture')
PUBLIC_LIVE = dict(LIVE, BOD_PUBLIC_PILOT='false')


def test_explicit_config_defaults_and_public_live():
    with pytest.raises(ValueError):
        stripe_config(SimpleNamespace())
    e = SimpleNamespace(**dict(PUBLIC_LIVE, BOD_PUBLIC_ORIGIN=PUBLIC, BOD_LOCAL_ONLY='false'))
    assert runtime_mode(e) == 'public'
    assert stripe_config(e, public=True)['live'] is True


@pytest.mark.parametrize('change', [
    {'BOD_BILLING_MODE': 'test'}, {'BOD_BILLING_MODE': 'unavailable'},
    {'BOD_STRIPE_SECRET_KEY': 'sk_test_fixture'}, {'BOD_STRIPE_LIVEMODE': 'false'},
    {'BOD_STRIPE_ACCOUNT_ID': ''}, {'BOD_STRIPE_PRODUCT_ID': ''},
    {'BOD_STRIPE_WEBHOOK_SECRET': ''}, {'BOD_PUBLIC_PILOT': 'true'},
    {'BOD_TEST_STRIPE_ORIGIN': 'http://127.0.0.1:9999'}])
def test_public_live_rejects_mixed_config(change):
    e = SimpleNamespace(**dict(PUBLIC_LIVE, BOD_PUBLIC_ORIGIN=PUBLIC, BOD_LOCAL_ONLY='false') | change)
    with pytest.raises(ValueError):
        runtime_mode(e)


@pytest.fixture(scope='module')
def live(tmp_path_factory):
    oidc, stripe = Provider(), StripeFixture(live=True)
    runtime = Runtime(tmp_path_factory.mktemp('live-runtime'), dict(LIVE,
        BOD_OIDC_CLIENT_ID='fixture-client', BOD_OIDC_CLIENT_SECRET='fixture-secret',
        BOD_TEST_OIDC_ORIGIN=oidc.origin, BOD_TEST_STRIPE_ORIGIN=stripe.origin))
    runtime.start()
    try:
        yield runtime, oidc, stripe
    finally:
        runtime.stop(); oidc.stop(); stripe.stop()


def live_event(customer, name):
    obj = event(customer, name.replace('-', '_'))
    obj['livemode'] = True
    obj['data']['object']['livemode'] = True
    return obj


def test_live_checkout_signed_reconciliation_and_fail_closed(live):
    runtime, oidc, stripe = live
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'live-fixture-parent'); token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        path, family = record(runtime, c)
        customer = family['customer']
        assert family['paid_until'] == 0  # redirects are not payments
        assert post(c, runtime, '/billing/portal', token).status_code == 303
        end = int(time.time()) + 86400
        sub = subscription(customer, end=end)
        sub['livemode'] = True
        sub['items']['data'][0]['price'] = copy.deepcopy(stripe.price)
        stripe.subs[customer] = [sub]
        paid_invoice(stripe, sub)
        assert webhook(runtime, event(customer, 'sandbox')).status_code == 400
        assert webhook(runtime, live_event(customer, 'bad-signature'), signature='bad').status_code == 400
        for field in ('account', 'context'):
            wrong = live_event(customer, 'connect-' + field); wrong[field] = 'acct_Other'
            assert webhook(runtime, wrong).status_code == 400
        wrong_object = live_event(customer, 'wrong_object_mode')
        wrong_object['data']['object']['livemode'] = False
        assert webhook(runtime, wrong_object).status_code == 400
        paid = live_event(customer, 'live-paid')
        assert webhook(runtime, paid).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == end
        with sqlite3.connect(path) as sql:
            scope, old, new = sql.execute("SELECT scope,old_until,paid_until FROM _bod_billing_audit WHERE event='evt_live_paid'").fetchone()
            assert scope == 'live|acct_Fixture|prod_Fixture|price_fixture'
            assert old == 0 and new == end
            count = sql.execute('SELECT COUNT(*) FROM _bod_billing_audit').fetchone()[0]
        assert webhook(runtime, paid).status_code == 200
        with sqlite3.connect(path) as sql:
            assert sql.execute('SELECT COUNT(*) FROM _bod_billing_audit').fetchone()[0] == count
        # Incorrect account, price, product, customer and subscription modes cannot reconcile.
        changes = [(stripe, 'account', 'acct_Other'), (stripe.price, 'currency', 'eur'),
            (stripe.price, 'unit_amount', 1000), (stripe.price, 'livemode', False),
            (stripe.price, 'product', 'prod_Other'), (stripe.price['recurring'], 'interval', 'month'),
            (stripe.product, 'livemode', False), (stripe.customers[customer], 'livemode', False),
            (sub, 'livemode', False), (sub, 'customer', 'cus_Other'),
            (sub['items']['data'][0]['price'], 'livemode', False)]
        for index, (obj, key, value) in enumerate(changes):
            before = obj[key] if isinstance(obj, dict) else getattr(obj, key)
            if isinstance(obj, dict): obj[key] = value
            else: setattr(obj, key, value)
            assert webhook(runtime, live_event(customer, 'invalid-' + str(index))).status_code == 503
            assert record(runtime, c)[1]['paid_until'] == end
            if isinstance(obj, dict): obj[key] = before
            else: setattr(obj, key, before)
        # Cancellation reconciles current state; trial remains exactly as created.
        sub['status'] = 'canceled'
        assert webhook(runtime, live_event(customer, 'live-canceled')).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        assert record(runtime, c)[1]['trial_until'] == family['trial_until']


def test_https_live_webhook_requires_matching_signature_and_mode(tmp_path):
    runtime = PublicRuntime(tmp_path, PUBLIC_LIVE)
    runtime.start()
    try:
        # Real HTTPS and Host, no Origin/cookie. No real API request is made.
        import httpx, json, hmac, hashlib
        def send(obj, valid=True):
            raw = json.dumps(obj).encode(); stamp = str(int(time.time()))
            sig = hmac.new(SECRET.encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
            return httpx.post(runtime.origin + '/billing/webhook', content=raw, verify=False,
                headers={'Host': 'thebankofdad.app', 'CF-Connecting-IP': '192.0.2.42',
                         'Stripe-Signature': f't={stamp},v1={sig if valid else "wrong"}'}, timeout=45)
        assert send(live_event('cus_Fixture', 'https-bad'), valid=False).status_code == 400
        assert send(event('cus_Fixture', 'https_test')).status_code == 400
        assert send(live_event('cus_Unmapped', 'https_receipt')).status_code == 200
        # Signed unmapped receipt is not fulfillment and makes no provider call.
        assert runtime.call('/')[0] == 200
    finally:
        runtime.stop()


def test_public_never_reuses_sandbox_entitlement(tmp_path):
    # Existing local state, no identities or provider credentials needed.
    local = Runtime(tmp_path)
    local.start()
    cookie, _ = local.guest(); path = local.database(cookie)
    local.stop()
    with sqlite3.connect(path) as sql:
        sql.execute('UPDATE _bod_family SET paid_until=?,trial_until=0', (int(time.time()) + 86400,))
        sql.execute('CREATE TABLE _bod_billing(singleton INTEGER PRIMARY KEY, scope TEXT)')
        sql.execute("INSERT INTO _bod_billing VALUES(1,'test||prod_Fixture|price_fixture')")
    public = PublicRuntime(tmp_path, PUBLIC_LIVE)
    public.start()
    try:
        assert public.call('/api/state', cookie=cookie)[0] == 402
        assert public.call('/billing', cookie=cookie)[0] == 200
    finally:
        public.stop()
