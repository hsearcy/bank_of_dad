"""Actual workerd/DO + Flask HTTP with disposable OAuth and Stripe fixtures.

No Stripe network/credentials, purchases or real-provider acceptance implied.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_runtime import Runtime, INIT
from websockets.sync.client import ClientConnection
from websockets.frames import Frame, Opcode
from test_oauth import Provider, client, login, csrf

SECRET = 'whsec_disposable_fixture'


class CloseFrameClient(ClientConnection):
    """Observe the wire Close, independently of TCP EOF/recv() completion."""
    def __init__(self, *args, **kwargs):
        self.close_received = threading.Event()
        super().__init__(*args, **kwargs)

    def process_event(self, event):
        super().process_event(event)
        if isinstance(event, Frame) and event.opcode is Opcode.CLOSE:
            self.close_received.set()


class StripeFixture:
    def __init__(self, live=False, key=None):
        self.live = live
        self.key = key or ('sk_live_local_fixture' if live else 'sk_test_fixture')
        self.account = 'acct_Fixture'
        self.price = dict(id='price_fixture', active=True, livemode=live, product='prod_Fixture',
            currency='usd', unit_amount=999, type='recurring', billing_scheme='per_unit',
            recurring=dict(interval='year', interval_count=1, usage_type='licensed'))
        self.product = dict(id='prod_Fixture', active=True, livemode=live)
        self.calls, self.customers, self.subs, self.checkouts = [], {}, {}, {}
        self.invoices, self.payments, self.intents = {}, {}, {}
        self.keys = {}
        self.fail = False
        self.checkout_failures = 0
        self.slow = False
        self.entered, self.release = threading.Event(), threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self, data, status=200):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            def do_GET(self):
                self.route('GET')
            def do_POST(self):
                self.route('POST')
            def route(self, method):
                assert self.headers['Authorization'] == 'Bearer ' + owner.key
                assert self.headers['Stripe-Version'] == '2025-03-31.basil'
                path = urlsplit(self.path)
                values = parse_qs(path.query if method == 'GET' else self.rfile.read(int(self.headers['Content-Length'])).decode())
                q = {k: v[0] for k, v in values.items()}
                key = self.headers.get('Idempotency-Key')
                owner.calls.append((method, path.path, q, key))
                if owner.fail:
                    return self.reply({'error': {'message': 'disposable failure'}}, 503)
                if key and key in owner.keys:
                    return self.reply(owner.keys[key])
                if path.path == '/v1/account':
                    obj = dict(id=owner.account)
                elif path.path == '/v1/products/prod_Fixture':
                    obj = owner.product
                elif path.path == '/v1/customers':
                    assert not q  # no browser metadata/customer/email
                    obj = dict(id='cus_Fixture' + str(len(owner.customers) + 1), livemode=owner.live)
                    owner.customers[obj['id']] = obj
                elif path.path.startswith('/v1/customers/'):
                    obj = owner.customers[path.path.rsplit('/', 1)[1]]
                elif path.path == '/v1/subscriptions':
                    rows = json.loads(json.dumps(owner.subs.get(q['customer'], [])))
                    if owner.slow:
                        owner.slow = False
                        owner.entered.set()
                        assert owner.release.wait(20)
                    # Force actual pagination when there are multiple subscriptions.
                    if q.get('starting_after'):
                        rows = rows[1:]
                        obj = dict(data=rows, has_more=False)
                    else:
                        obj = dict(data=rows[:1], has_more=len(rows) > 1)
                elif path.path == '/v1/invoices':
                    rows = [i for i in owner.invoices.get(q['customer'], [])
                            if i['parent']['subscription_details']['subscription'] == q['subscription']
                            and i['status'] == q['status']]
                    if q.get('starting_after'):
                        rows = rows[[i['id'] for i in rows].index(q['starting_after']) + 1:]
                    obj = dict(data=rows[:1], has_more=len(rows) > 1)
                elif path.path == '/v1/invoice_payments':
                    obj = dict(data=owner.payments.get(q['invoice'], []), has_more=False)
                elif path.path.startswith('/v1/charges/'):
                    obj = next(i['latest_charge'] for i in owner.intents.values()
                               if i['latest_charge']['id'] == path.path.rsplit('/', 1)[1])
                elif path.path.startswith('/v1/payment_intents/'):
                    assert q['expand[]'] == 'latest_charge'
                    obj = owner.intents[path.path.rsplit('/', 1)[1]]
                elif path.path == '/v1/prices/price_fixture':
                    obj = owner.price
                elif path.path == '/v1/checkout/sessions' and method == 'POST':
                    assert self.headers['Content-Type'] == 'application/x-www-form-urlencoded'
                    if q.get('managed_payments[enabled]') != 'false' or owner.checkout_failures:
                        owner.checkout_failures = max(0, owner.checkout_failures - 1)
                        return self.reply({'error': {'type': 'invalid_request_error', 'message':
                            'Invalid line_items[0]: the product tax code is missing. '
                            'Product tax code is required for Managed Payments, which is enabled '
                            'by default on your account. If you want to disable Managed Payments '
                            'on this session, you can pass managed_payments[enabled]=false.'}}, 400)
                    assert q['mode'] == 'subscription' and q['line_items[0][price]'] == 'price_fixture'
                    assert q['line_items[0][quantity]'] == '1'
                    assert not any('trial' in k or 'metadata' in k for k in q)
                    obj = dict(id='cs_fixture' + str(len(owner.checkouts) + 1), customer=q['customer'],
                        livemode=owner.live, status='open', expires_at=int(time.time()) + 1800,
                        url='https://checkout.stripe.com/c/pay/fixture')
                    owner.checkouts[obj['id']] = obj
                elif path.path.startswith('/v1/checkout/sessions/'):
                    obj = owner.checkouts[path.path.rsplit('/', 1)[1]]
                elif path.path == '/v1/billing_portal/sessions':
                    assert q['customer'] in owner.customers
                    obj = dict(url='https://billing.stripe.com/p/session/fixture')
                else:
                    return self.reply({}, 404)
                if key:
                    owner.keys[key] = obj
                return self.reply(obj)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.origin = f'http://127.0.0.1:{self.http.server_port}'
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.release.set()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


@pytest.fixture(scope='module')
def billing(tmp_path_factory):
    oidc, stripe = Provider(), StripeFixture()
    runtime = Runtime(tmp_path_factory.mktemp('stripe-runtime'), {
        'BOD_OIDC_CLIENT_ID': 'fixture-client', 'BOD_OIDC_CLIENT_SECRET': 'fixture-secret',
        'BOD_TEST_OIDC_ORIGIN': oidc.origin, 'BOD_STRIPE_SECRET_KEY': 'sk_test_fixture',
        'BOD_STRIPE_PRICE_ID': 'price_fixture', 'BOD_STRIPE_WEBHOOK_SECRET': SECRET,
        'BOD_BILLING_MODE': 'test', 'BOD_STRIPE_LIVEMODE': 'false', 'BOD_TEST_STRIPE_ORIGIN': stripe.origin})
    runtime.start()
    try:
        yield runtime, oidc, stripe
    finally:
        runtime.stop()
        oidc.stop()
        stripe.stop()


def post(c, runtime, path, token, **fields):
    return c.post(path, data=dict(csrf_token=token, **fields), headers={'Origin': runtime.origin})


def event(customer, name, **changes):
    return dict(id='evt_' + name, type='customer.subscription.updated', livemode=False,
                data=dict(object=dict(customer=customer, metadata={'family': 'attacker'})), **changes)


def webhook(runtime, value, stamp=None, signature=None, raw=None):
    raw = raw or json.dumps(value).encode()
    stamp = int(time.time()) if stamp is None else stamp
    signed = hmac.new(SECRET.encode(), str(stamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
    return httpx.post(runtime.origin + '/billing/webhook', content=raw,
        headers={'Stripe-Signature': signature or f't={stamp},v1=wrong,v1={signed}'}, timeout=45)


def subscription(customer, status='active', end=None, price='price_fixture', id='sub_fixture'):
    return dict(id=id, customer=customer, livemode=False, status=status,
        items=dict(has_more=False, data=[dict(id='si_' + id, price=dict(id=price), quantity=1,
            current_period_end=end or int(time.time()) + 86400)]))


def paid_invoice(stripe, sub, *, id='in_fixture', start=None, end=None):
    """Explicit Basil invoice/payment/settled-charge fixture; never auto-pay a sub."""
    customer = sub['customer']
    item = sub['items']['data'][0]
    end = end or item['current_period_end']
    invoice = dict(id=id, customer=customer, livemode=stripe.live, currency='usd',
        status='paid', total=999, amount_paid=999, amount_remaining=0,
        parent=dict(type='subscription_details', subscription_details=dict(subscription=sub['id'])),
        lines=dict(has_more=False, data=[dict(id='il_' + id, quantity=1, amount=999,
            period=dict(start=start or int(time.time()) - 100, end=end),
            parent=dict(type='subscription_item_details', subscription_item_details=dict(
                subscription=sub['id'], subscription_item=item['id'], proration=False)),
            pricing=dict(type='price_details', price_details=dict(price='price_fixture', product='prod_Fixture')))]))
    stripe.invoices.setdefault(customer, []).append(invoice)
    intent_id = 'pi_' + id
    stripe.payments[id] = [dict(id='inpay_' + id, invoice=id, livemode=stripe.live,
        currency='usd', status='paid', amount_paid=999,
        payment=dict(type='payment_intent', payment_intent=intent_id))]
    stripe.intents[intent_id] = dict(id=intent_id, customer=customer, livemode=stripe.live,
        currency='usd', status='succeeded', amount_received=999,
        latest_charge=dict(id='ch_' + id, customer=customer, payment_intent=intent_id,
            livemode=stripe.live, currency='usd', status='succeeded', paid=True,
            captured=True, amount_captured=999, amount_refunded=0, disputed=False))
    return invoice


def record(runtime, c):
    path = runtime.database('bod-local-session=' + c.cookies['bod-local-session'])
    with sqlite3.connect(path) as sql:
        sql.row_factory = sqlite3.Row
        return path, dict(sql.execute('SELECT * FROM _bod_family').fetchone())


def test_checkout_portal_and_authority(billing):
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).headers['location'] == '/auth/login'
        assert not stripe.calls
        login(c, oidc, 'billing-parent')
        token = csrf(c)
        assert c.post('/billing/checkout', data={'csrf_token': token}).status_code == 403
        assert post(c, runtime, '/billing/checkout', 'wrong').status_code == 403
        before = record(runtime, c)[1]
        result = post(c, runtime, '/billing/checkout', token, customer='cus_Attacker', family='attacker')
        assert result.status_code == 303, result.text
        assert result.headers['location'].startswith('https://checkout.stripe.com/')
        path, family = record(runtime, c)
        assert family['customer'] == 'cus_Fixture1' and family['paid_until'] == 0
        assert family['trial_until'] == before['trial_until']
        assert len(stripe.checkouts) == 1
        with ThreadPoolExecutor(2) as pool:
            retries = [pool.submit(post, c, runtime, '/billing/checkout', token) for _ in range(2)]
            assert all(result.result().status_code == 303 for result in retries)
        assert len(stripe.checkouts) == 1
        assert post(c, runtime, '/billing/portal', token).headers['location'].startswith('https://billing.stripe.com/')
        assert 'Manage billing / cancel in Stripe' in c.get('/billing').text
        cookie = c.cookies['bod-local-session']
        assert webhook(runtime, event(family['customer'], 'unknown'), signature='bad').status_code == 400
        assert webhook(runtime, event(family['customer'], 'future'), stamp=int(time.time()) + 600).status_code == 400
        assert webhook(runtime, event(family['customer'], 'past'), stamp=int(time.time()) - 600).status_code == 400
        wrong = event(family['customer'], 'live'); wrong['livemode'] = True
        assert webhook(runtime, wrong).status_code == 400
        # Neither unsigned browser return nor foreign customer metadata grants access.
        assert c.get('/billing?session_id=cs_fake&family=attacker').status_code == 200
        assert webhook(runtime, event('cus_Unmapped', 'unmapped')).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        # Current provider snapshot, not event object status or timestamp, owns access.
        end = int(time.time()) + 86400
        stripe.subs[family['customer']] = [subscription(family['customer'], end=end)]
        paid_invoice(stripe, stripe.subs[family['customer']][0])
        value = event(family['customer'], 'paid')
        assert webhook(runtime, value).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == end
        assert c.cookies['bod-local-session'] == cookie
        count = len(stripe.calls)
        assert webhook(runtime, value).status_code == 200
        assert len(stripe.calls) == count
        value['data']['object']['metadata']['family'] = 'changed'
        assert webhook(runtime, value).status_code == 400
        # A delayed old event reconciles current cancellation and revokes paid only.
        stripe.subs[family['customer']][0]['status'] = 'canceled'
        assert webhook(runtime, event(family['customer'], 'old')).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        assert record(runtime, c)[1]['trial_until'] == before['trial_until']
        # Recovery across all pages, even after Checkout has expired.
        stripe.checkouts['cs_fixture1']['expires_at'] = int(time.time()) - 100
        stripe.subs[family['customer']] = [subscription(family['customer'], 'canceled', id='sub_terminal'),
                                          subscription(family['customer'], 'past_due', id='sub_retry')]
        assert post(c, runtime, '/billing/checkout', token).headers['location'].startswith('https://billing.stripe.com/')
        assert len(stripe.checkouts) == 1
        stripe.subs[family['customer']][1]['status'] = 'incomplete_expired'
        assert post(c, runtime, '/billing/checkout', token).headers['location'].startswith('https://checkout.stripe.com/')
        assert len(stripe.checkouts) == 2
        # Provider trials, wrong price and past_due with no paid invoice never grant access.
        stripe.invoices[family['customer']] = []
        for status, price in [('trialing', 'price_fixture'), ('active', 'price_other'), ('past_due', 'price_fixture')]:
            stripe.subs[family['customer']] = [subscription(family['customer'], status, price=price)]
            assert post(c, runtime, '/billing/refresh', token).status_code == 303
            assert record(runtime, c)[1]['paid_until'] == 0


def test_managed_payments_retry_preserves_existing_customer(billing):
    """A rejected Session leaves a durable customer/attempt, not a charge."""
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'managed-payments-retry-parent'); token = csrf(c)
        initial = record(runtime, c)[1]
        before_customers, before_checkouts = len(stripe.customers), len(stripe.checkouts)
        stripe.checkout_failures = 1
        try:
            assert post(c, runtime, '/billing/checkout', token).status_code == 503
        finally:
            stripe.checkout_failures = 0
        path, rejected = record(runtime, c)
        assert rejected['customer'] in stripe.customers
        assert len(stripe.customers) == before_customers + 1
        assert len(stripe.checkouts) == before_checkouts
        with sqlite3.connect(path) as sql:
            attempt, checkout_id = sql.execute('SELECT attempt,checkout_id FROM _bod_billing').fetchone()
            assert attempt and checkout_id is None
        calls = len(stripe.calls)
        result = post(c, runtime, '/billing/checkout', token)
        assert result.status_code == 303, result.text
        assert result.headers['location'].startswith('https://checkout.stripe.com/')
        assert len(stripe.customers) == before_customers + 1
        assert len(stripe.checkouts) == before_checkouts + 1
        assert not any(method == 'POST' and endpoint == '/v1/customers'
                       for method, endpoint, _, _ in stripe.calls[calls:])
        checkouts = [(form, key) for method, endpoint, form, key in stripe.calls
                     if method == 'POST' and endpoint == '/v1/checkout/sessions'
                     and form.get('customer') == rejected['customer']]
        assert len(checkouts) == 2
        assert checkouts[0] == checkouts[1]  # same serialized payload and idempotency key
        assert checkouts[1][0]['managed_payments[enabled]'] == 'false'
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        assert len(stripe.checkouts) == before_checkouts + 1
        after = record(runtime, c)[1]
        assert after['customer'] == rejected['customer']
        assert after['paid_until'] == 0 and after['trial_until'] == initial['trial_until']
        assert not stripe.subs.get(after['customer'])
        assert not stripe.invoices.get(after['customer'])
        assert all(intent['customer'] != after['customer'] for intent in stripe.intents.values())


def test_ordering_failure_retry_and_restart(billing):
    from websockets.sync.client import connect
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'ordering-parent'); token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        path, family = record(runtime, c)
        customer = family['customer']
        stripe.subs[customer] = [subscription(customer)]
        paid_invoice(stripe, stripe.subs[customer][0], id='in_' + customer + '_' + str(len(stripe.invoices.get(customer, []))))
        stripe.slow = True
        stripe.entered.clear(); stripe.release.clear()
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(webhook, runtime, event(customer, 'slow'))
            assert stripe.entered.wait(15)
            stripe.subs[customer][0]['status'] = 'canceled'
            second = pool.submit(webhook, runtime, event(customer, 'newer'))
            # Ordinary bank requests still execute during provider I/O.
            assert c.get('/api/state').status_code == 200
            stripe.release.set()
            assert first.result().status_code == second.result().status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        value = event(customer, 'retry')
        stripe.fail = True
        assert webhook(runtime, value).status_code == 503
        with sqlite3.connect(path) as sql:
            assert not sql.execute("SELECT 1 FROM _bod_stripe_events WHERE id='evt_retry'").fetchone()
        stripe.fail = False
        stripe.subs[customer] = [subscription(customer)]
        paid_invoice(stripe, stripe.subs[customer][0], id='in_' + customer + '_' + str(len(stripe.invoices.get(customer, []))))
        assert webhook(runtime, value).status_code == 200
        paid = record(runtime, c)[1]['paid_until']
        runtime.stop()
        with sqlite3.connect(path) as sql:
            sql.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON _bod_stripe_events BEGIN SELECT RAISE(ABORT,'rollback receipt'); END")
            sql.execute('UPDATE _bod_family SET trial_until=0')
        runtime.start()
        stripe.subs[customer][0]['status'] = 'canceled'
        assert webhook(runtime, event(customer, 'rollback')).status_code == 503
        assert record(runtime, c)[1]['paid_until'] == paid
        runtime.stop()
        with sqlite3.connect(path) as sql:
            assert not sql.execute("SELECT 1 FROM _bod_stripe_events WHERE id='evt_rollback'").fetchone()
            sql.execute('DROP TRIGGER fail_receipt')
        runtime.start()
        url = runtime.origin.replace('http:', 'ws:') + '/api/updates'
        cookie = 'bod-local-session=' + c.cookies['bod-local-session']
        with connect(url, origin=runtime.origin, additional_headers={'Cookie': cookie},
                     create_connection=CloseFrameClient) as ws:
            assert json.loads(ws.recv(timeout=5))['type'] == 'connected'
            assert webhook(runtime, event(customer, 'rollback')).status_code == 200
            assert record(runtime, c)[1]['paid_until'] == 0
            assert record(runtime, c)[1]['trial_until'] == 0
            # recv() waits for TCP EOF, not just a Close frame. Observe the
            # actual protocol event within the unchanged five-second budget.
            assert isinstance(ws, CloseFrameClient)
            assert ws.close_received.wait(5), 'Cancellation Close frame not received'
            assert ws.protocol.close_rcvd is not None
            assert ws.protocol.close_rcvd.code == 1008
            assert ws.protocol.close_rcvd.reason == 'Access ended'
            assert ws.protocol.close_sent == ws.protocol.close_rcvd
        assert record(runtime, c)[1]['paid_until'] == 0
        assert c.get('/api/state').status_code == 402
        assert c.get('/billing').status_code == 200
        assert post(c, runtime, '/billing/portal', token).status_code == 303
        assert post(c, runtime, '/billing/refresh', token).status_code == 303
        # Original session/csrf are still valid; no webhook cookie takeover.
        assert post(c, runtime, '/auth/logout', token).status_code == 302
        assert c.get('/api/state').status_code == 401


def test_cancellation_closes_actual_idle_browser_without_polling(billing):
    from playwright.sync_api import sync_playwright, expect
    runtime, oidc, stripe = billing
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'idle-browser-parent'); token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        path, family = record(runtime, c)
        customer = family['customer']
        stripe.subs[customer] = [subscription(customer)]
        paid_invoice(stripe, stripe.subs[customer][0], id='in_' + customer + '_' + str(len(stripe.invoices.get(customer, []))))
        assert webhook(runtime, event(customer, 'browser_paid')).status_code == 200
        runtime.stop()
        with sqlite3.connect(path) as sql:
            sql.execute('UPDATE _bod_family SET trial_until=0')
        runtime.start()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_cookies([dict(name='bod-local-session',
                value=c.cookies['bod-local-session'], url=runtime.origin)])
            context.add_init_script(INIT)
            page = context.new_page()
            page.goto(runtime.origin)
            # Synchronize on server acknowledgement, not just page load or OPEN.
            page.wait_for_function('window.__frames.some(f => JSON.parse(f).type === "connected")')
            page.evaluate('''window.__sockets.at(-1).addEventListener('close', e =>
                sessionStorage.setItem('billingClose', JSON.stringify({code:e.code, clean:e.wasClean})))''')
            other = browser.new_context()
            other.add_init_script(INIT)
            untouched = other.new_page()
            untouched.goto(runtime.origin)
            untouched.wait_for_function('window.__frames.some(f => JSON.parse(f).type === "connected")')
            stripe.subs[customer][0]['status'] = 'canceled'
            assert webhook(runtime, event(customer, 'browser_canceled')).status_code == 200
            page.wait_for_url(runtime.origin + '/billing', timeout=5000)
            assert page.evaluate('JSON.parse(sessionStorage.getItem("billingClose"))') == dict(code=1008, clean=True)
            assert c.get('/api/state').status_code == 402
            expect(untouched.locator('#kids .big')).to_have_text('$7.00')
            assert untouched.evaluate('window.__sockets.at(-1).readyState') == 1
            assert not untouched.evaluate('window.__frames.some(f => JSON.parse(f).type === "state_changed")')
            browser.close()


def test_actual_browser_billing_forms(billing):
    from playwright.sync_api import sync_playwright
    runtime, oidc, stripe = billing
    oidc.subject = 'browser-billing-parent'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(runtime.origin)
        page.goto(runtime.origin + '/auth/login')
        page.wait_for_url(runtime.origin + '/')
        page.goto(runtime.origin + '/billing')
        for width in (390, 768, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.locator('button', has_text='Subscribe with Stripe').is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        observed = []
        def capture(route):
            # Exercise the real same-origin form POST but STOP its redirect chain.
            # Never navigate to or claim acceptance by the real hosted provider.
            result = route.fetch(max_redirects=0)
            observed.append((result.status, result.headers.get('location')))
            route.fulfill(status=200, content_type='text/plain', body='Disposable test: provider redirect observed')
        page.route('**/billing/checkout', capture)
        page.get_by_role('button', name='Subscribe with Stripe').click()
        page.wait_for_url('**/billing/checkout')
        assert observed == [(303, 'https://checkout.stripe.com/c/pay/fixture')]
        page.goto(runtime.origin + '/billing')
        page.route('**/billing/portal', capture)
        page.get_by_role('button', name='Manage billing / cancel in Stripe').click()
        page.wait_for_url('**/billing/portal')
        assert observed[-1] == (303, 'https://billing.stripe.com/p/session/fixture')
        context.close()
        browser.close()
