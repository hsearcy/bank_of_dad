"""Stripe REST on Workers. Network awaits never run inside transactionSync.

Billing is disabled unless explicitly configured for one Stripe mode.
Browser IDs and Stripe metadata are never used to select a family or establish paid access.
"""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import js
from workers import fetch
from hosted_access import migrate_family_trial
from cloudflare_auth import response
from cloudflare_security import runtime_mode, stripe_config

VERSION = '2025-03-31.basil'
TERMINAL = ('canceled', 'incomplete_expired')
DIRECTORY_SCHEMA = '''
CREATE TABLE IF NOT EXISTS stripe_customers(customer TEXT PRIMARY KEY, family TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS stripe_events(id TEXT PRIMARY KEY, digest TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, received REAL NOT NULL);
'''
FAMILY_SCHEMA = '''
CREATE TABLE IF NOT EXISTS _bod_billing(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 epoch INTEGER NOT NULL DEFAULT 0, reconciled REAL, checkout_id TEXT,
 checkout_expires REAL, attempt TEXT);
INSERT OR IGNORE INTO _bod_billing(singleton) VALUES (1);
CREATE TABLE IF NOT EXISTS _bod_stripe_events(id TEXT PRIMARY KEY, digest TEXT NOT NULL, received REAL NOT NULL);
'''


class Stripe:
    def __init__(self, env):
        public = runtime_mode(env) == 'public'
        config = stripe_config(env, public=public)
        self.env = env
        self.key, self.price, self.secret = (str(config[k]) for k in ('key', 'price', 'secret'))
        self.live = config['live'] is True
        self.account, self.product = str(config['account']), str(config['product'])
        self.scope = '|'.join((str(config['mode']), self.account, self.product, self.price))
        self.base = 'https://api.stripe.com'
        fixture = str(getattr(env, 'BOD_TEST_STRIPE_ORIGIN', ''))
        if fixture:
            parts = urlsplit(fixture)
            if (str(env.BOD_LOCAL_ONLY) != 'true' or parts.scheme != 'http'
                    or parts.hostname != '127.0.0.1' or parts.path or parts.query or parts.fragment
                    or self.key not in (('sk_live_local_fixture', 'rk_live_local_fixture') if self.live
                                        else ('sk_test_fixture', 'rk_test_fixture'))):
                raise ValueError('Invalid Stripe fixture configuration')
            self.base = fixture

    async def api(self, method, path, values=None, key=None):
        headers = {'Authorization': 'Bearer ' + self.key, 'Stripe-Version': VERSION}
        data = urlencode(values or {})
        if key:
            headers['Idempotency-Key'] = key
        if method == 'GET' and data:
            path += '?' + data
        if method == 'POST':
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        result = await fetch(self.base + '/v1/' + path, method=method, headers=headers,
            body=data if method == 'POST' else None, redirect='manual', signal=js.AbortSignal.timeout(15000))
        raw = await result.bytes()
        if not 200 <= result.status < 300 or len(raw) > 1024 * 1024:
            raise ValueError('Stripe request failed')
        return json.loads(raw)

    async def validate(self, *, checkout=False):
        # A platform webhook has no account field. Pin the secret's own account;
        # Connect/context events are never accepted. Never send Stripe-Account.
        if self.live:
            account = await self.api('GET', 'account')
            if account.get('id') != self.account:
                raise ValueError('Wrong Stripe account')
            price = await self.api('GET', 'prices/' + self.price)
            self.validate_price(price, checkout=checkout)
            product = await self.api('GET', 'products/' + self.product)
            if (product.get('id') != self.product or (checkout and product.get('active') is not True)
                    or product.get('livemode') is not self.live):
                raise ValueError('Wrong Stripe product')

    def validate_price(self, price, *, checkout=False):
        if (price.get('id') != self.price or (checkout and price.get('active') is not True)
                or price.get('livemode') is not self.live or price.get('currency') != 'usd'
                or price.get('unit_amount') != 999 or price.get('type') != 'recurring'
                or price.get('billing_scheme') != 'per_unit'
                or price.get('recurring', {}).get('interval') != 'year'
                or price.get('recurring', {}).get('interval_count') != 1
                or price.get('recurring', {}).get('usage_type') != 'licensed'
                or (self.product and price.get('product') != self.product)):
            raise ValueError('Configure active USD $9.99/year licensed price/product')

    async def customer(self, customer):
        obj = await self.api('GET', 'customers/' + customer)
        if obj.get('id') != customer or obj.get('deleted') or obj.get('livemode') is not self.live:
            raise ValueError('Wrong Stripe customer/mode')

    async def subscriptions(self, customer):
        result, after = [], None
        for _ in range(100):
            values = dict(customer=customer, status='all', limit=100)
            if after:
                values['starting_after'] = after
            page = await self.api('GET', 'subscriptions', values)
            rows = page['data']
            for sub in rows:
                if sub['customer'] != customer or sub['livemode'] is not self.live:
                    raise ValueError('Unexpected Stripe customer/mode')
            result.extend(rows)
            if page['has_more'] is False:
                return result
            if not rows or rows[-1]['id'] == after:
                break
            after = rows[-1]['id']
        raise ValueError('Incomplete Stripe pagination')

    async def pages(self, path, values):
        rows, after = [], None
        for _ in range(100):
            query = dict(values, limit=100)
            if after:
                query['starting_after'] = after
            page = await self.api('GET', path, query)
            batch = page['data']
            rows.extend(batch)
            if page['has_more'] is False:
                return rows
            if not batch or batch[-1]['id'] == after:
                break
            after = batch[-1]['id']
        raise ValueError('Incomplete Stripe pagination')

    async def invoice_settled(self, invoice, customer):
        # Basil removed invoice.paid/charge/payment_intent. A paid invoice can
        # also be zero-dollar or marked paid out of band: verify real settlement.
        if (invoice.get('status') != 'paid' or invoice.get('currency') != 'usd'
                or invoice.get('total', 0) <= 0 or invoice.get('amount_remaining') != 0
                or invoice.get('amount_paid', 0) < invoice['total']
                or invoice.get('post_payment_credit_notes_amount', 0)):
            return False
        allocated, seen = 0, set()
        for payment in await self.pages('invoice_payments', {'invoice': invoice['id']}):
            if payment['invoice'] != invoice['id'] or payment['livemode'] is not self.live:
                raise ValueError('Wrong invoice payment/mode')
            if payment['status'] != 'paid' or payment['payment']['type'] != 'payment_intent':
                continue
            intent_id = payment['payment']['payment_intent']
            if intent_id in seen:
                raise ValueError('Duplicate invoice payment intent')
            seen.add(intent_id)
            intent = await self.api('GET', 'payment_intents/' + intent_id,
                                    {'expand[]': 'latest_charge'})
            if (intent['id'] != intent_id or intent['customer'] != customer
                    or intent['livemode'] is not self.live or intent['currency'] != 'usd'):
                raise ValueError('Wrong payment customer/mode')
            charge = intent.get('latest_charge')
            if (intent['status'] != 'succeeded' or not isinstance(charge, dict)
                    or charge.get('status') != 'succeeded' or charge.get('paid') is not True
                    or charge.get('captured') is not True):
                continue
            if (charge['customer'] != customer or charge['payment_intent'] != intent_id
                    or charge['livemode'] is not self.live or charge['currency'] != 'usd'):
                raise ValueError('Wrong charge customer/mode')
            # Any partial/full refund or dispute invalidates this invoice's
            # coverage. Do not manufacture prorated access from refund amounts.
            if charge.get('amount_refunded', 0) or charge.get('disputed'):
                return False
            amount = payment.get('amount_paid', 0)
            if (payment.get('currency') != 'usd' or not isinstance(amount, int) or amount <= 0
                    or amount > intent['amount_received'] or amount > charge['amount_captured']):
                raise ValueError('Invalid invoice settlement amount')
            allocated += amount
        return allocated >= invoice['total']

    async def paid_until(self, subscriptions, customer):
        until, now = 0, time.time()
        for sub in subscriptions:
            # Failed renewals cannot extend access, but do not erase an already
            # paid, unexpired period. Immediate cancellation still revokes it.
            if sub['status'] not in ('active', 'past_due', 'unpaid'):
                continue
            if sub['items'].get('has_more', False):
                raise ValueError('Incomplete subscription items')
            items = {}
            for item in sub['items']['data']:
                if item['price']['id'] == self.price and item.get('quantity', 1) == 1:
                    if self.live:
                        self.validate_price(item['price'])
                    items[item['id']] = item
            if not items:
                continue
            invoices = await self.pages('invoices', dict(customer=customer, subscription=sub['id'], status='paid'))
            for invoice in invoices:
                parent = invoice.get('parent') or {}
                if (invoice['customer'] != customer or invoice['livemode'] is not self.live
                        or parent.get('type') != 'subscription_details'
                        or parent['subscription_details']['subscription'] != sub['id']):
                    raise ValueError('Wrong invoice customer/subscription/mode')
                lines = invoice['lines']['data']
                if invoice['lines']['has_more']:
                    lines = await self.pages('invoices/' + invoice['id'] + '/lines', {})
                ends = []
                for line in lines:
                    source, pricing = line.get('parent') or {}, line.get('pricing') or {}
                    details = source.get('subscription_item_details') or {}
                    price = pricing.get('price_details') or {}
                    if (source.get('type') != 'subscription_item_details'
                            or details.get('subscription') != sub['id']
                            or details.get('subscription_item') not in items
                            or details.get('proration') is not False
                            or pricing.get('type') != 'price_details'
                            or price.get('price') != self.price
                            or (self.product and price.get('product') != self.product)
                            or line.get('quantity') != 1 or line.get('amount', 0) <= 0):
                        continue
                    start, end = line['period']['start'], line['period']['end']
                    if not all(type(v) in (int, float) and 0 <= v < 2**53 for v in (start, end)) or end <= start:
                        raise ValueError('Invalid paid invoice period')
                    if start <= now < end:
                        ends.append(end)
                if ends and await self.invoice_settled(invoice, customer):
                    end = max(ends)
                    if sub.get('cancel_at'):
                        end = min(end, sub['cancel_at'])
                    until = max(until, end)
        return until

    async def portal(self, customer):
        values = dict(customer=customer, return_url=str(self.env.BOD_PUBLIC_ORIGIN) + '/billing')
        configuration = str(getattr(self.env, 'BOD_STRIPE_PORTAL_CONFIGURATION', ''))
        if configuration:
            values['configuration'] = configuration
        return await self.api('POST', 'billing_portal/sessions', values)

    def hosted_url(self, obj, portal=False):
        value = obj['url']
        parsed = urlsplit(value)
        host = 'billing.stripe.com' if portal else 'checkout.stripe.com'
        if parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password:
            raise ValueError('Invalid Stripe hosted URL')
        return value

    def verify(self, raw, signature):
        parts = [part.strip().split('=', 1) for part in signature.split(',')]
        stamps = [value for key, value in parts if key == 't']
        if len(stamps) != 1 or abs(time.time() - int(stamps[0])) > 300:
            raise ValueError('Invalid signature timestamp')
        expected = hmac.new(self.secret.encode(), stamps[0].encode() + b'.' + raw, hashlib.sha256).hexdigest()
        if not any(key == 'v1' and hmac.compare_digest(expected, value) for key, value in parts):
            raise ValueError('Invalid signature')
        event = json.loads(raw)
        if (event['livemode'] is not self.live or event.get('account') or event.get('context')
                or not re.fullmatch(r'evt_[A-Za-z0-9_]+', event['id'])):
            raise ValueError('Invalid event mode/account')
        obj = event['data']['object']
        if 'livemode' in obj and obj['livemode'] is not self.live:
            raise ValueError('Invalid event object mode')
        return event


class BillingDirectory:
    def billing_setup(self):
        self.transact(lambda c: c.executescript(DIRECTORY_SCHEMA))

    def map_customer(self, payload):
        data = json.loads(payload)
        stripe = Stripe(self.env)
        if data['scope'] != stripe.scope:
            raise ValueError('Wrong billing scope')
        data['customer'] = stripe.scope + ':' + data['customer']
        self.billing_setup()
        def save(c):
            # Binding-only registration; the family DO durably owns the customer.
            c.execute('INSERT OR IGNORE INTO stripe_customers VALUES (?,?)', (data['customer'], data['family']))
            row = c.execute('SELECT family FROM stripe_customers WHERE customer=?', (data['customer'],)).fetchone()
            if not row or row['family'] != data['family']:
                raise ValueError('Customer mapping conflict')
        self.transact(save)
        return True

    async def stripe_webhook(self, request):
        if request.method != 'POST':
            return response('Method not allowed', 405)
        try:
            stripe = Stripe(self.env)
        except Exception:
            return response('Billing is not configured', 503)
        raw = bytearray()
        if request.body:
            async for chunk in request.body:
                raw.extend(chunk.to_bytes())
                if len(raw) > 256 * 1024:
                    return response('Payload too large', 413)
        try:
            event = stripe.verify(bytes(raw), request.headers.get('Stripe-Signature', ''))
        except Exception:
            return response('Invalid webhook', 400)
        hashed = hashlib.sha256(raw).hexdigest()
        event_key = hashlib.sha256(stripe.scope.encode()).hexdigest() + ':' + event['id']
        try:
            # Receipt-only/unmapped events cannot grant access. Mapped family
            # reconciliation validates the pinned account/product/price itself.
            self.billing_setup()
            def reserve(c):
                old = c.execute('SELECT * FROM stripe_events WHERE id=?', (event_key,)).fetchone()
                if old and old['digest'] != hashed:
                    return 'conflict'
                if old and old['done']:
                    return 'done'
                c.execute('INSERT OR IGNORE INTO stripe_events(id,digest,received) VALUES (?,?,?)',
                          (event_key, hashed, time.time()))
                return 'pending'
            state = self.transact(reserve)
            if state == 'conflict':
                return response('Conflicting event', 400)
            if state == 'done':
                return response('', 200)
            relevant = event['type'].startswith('customer.subscription.') or event['type'] in (
                'checkout.session.completed', 'checkout.session.async_payment_succeeded',
                'invoice.paid', 'invoice.payment_failed', 'invoice.payment_action_required',
                'invoice.voided', 'invoice.marked_uncollectible', 'invoice.updated',
                'checkout.session.async_payment_failed', 'charge.refunded', 'charge.dispute.created',
                'charge.dispute.closed', 'credit_note.created', 'credit_note.updated', 'credit_note.voided')
            customer = event['data']['object'].get('customer')
            # Dispute objects have no customer. Resolve their charge under this
            # API key; never infer the family from metadata or the event itself.
            if event['type'].startswith('charge.dispute.'):
                charge_id = event['data']['object'].get('charge', '')
                if not isinstance(charge_id, str) or not re.fullmatch(r'ch_[A-Za-z0-9_]+', charge_id):
                    raise ValueError('Invalid dispute charge')
                charge = await stripe.api('GET', 'charges/' + charge_id)
                if charge['id'] != charge_id or charge['livemode'] is not stripe.live:
                    raise ValueError('Wrong dispute charge/mode')
                customer = charge.get('customer')
            mapped = self.transact(lambda c: c.execute('SELECT family FROM stripe_customers WHERE customer=?',
                (stripe.scope + ':' + customer if isinstance(customer, str) else '',)).fetchone())
            if relevant and mapped:
                await self.family(mapped['family']).billing(json.dumps(dict(
                    operation='webhook', customer=customer, event=event['id'], digest=hashed)))
            self.transact(lambda c: c.execute('UPDATE stripe_events SET done=1 WHERE id=? AND digest=?', (event_key, hashed)))
            return response('', 200)
        except Exception as error:
            print('Stripe webhook deferred:', type(error).__name__)
            return response('Webhook retry required', 503)


class FamilyBilling:
    def billing_family(self, c, family):
        """A cached sandbox/foreign-account entitlement is never public payment."""
        if not family:
            return family
        columns = {row['name'] for row in c.execute('PRAGMA table_info(_bod_family)')}
        if 'trial_policy' not in columns:
            c.execute('ALTER TABLE _bod_family ADD COLUMN trial_policy INTEGER NOT NULL DEFAULT 0')
        # Includes authentication, owner projections, idle publish and alarm reads.
        if 'trial_policy' not in family or family['trial_policy'] == 0:
            migrate_family_trial(c, '_bod_family', family['id'])
            family = c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()
        if runtime_mode(self.env) == 'public' or str(getattr(self.env, 'BOD_BILLING_MODE', '')) == 'live':
            valid = False
            try:
                stripe = Stripe(self.env)
                columns = {r['name'] for r in c.execute('PRAGMA table_info(_bod_billing)')}
                saved = c.execute('SELECT scope,paid_policy FROM _bod_billing').fetchone() if {'scope', 'paid_policy'} <= columns else None
                valid = stripe.live and saved and saved['scope'] == stripe.scope and saved['paid_policy'] == 'invoice-v1'
            except ValueError:
                pass
            if not valid:
                return dict(family, paid_until=0)
        return family

    def billing_setup(self):
        def setup(c):
            c.executescript(FAMILY_SCHEMA)
            if 'scope' not in {r['name'] for r in c.execute('PRAGMA table_info(_bod_billing)')}:
                c.execute('ALTER TABLE _bod_billing ADD COLUMN scope TEXT')
            if 'paid_policy' not in {r['name'] for r in c.execute('PRAGMA table_info(_bod_billing)')}:
                c.execute('ALTER TABLE _bod_billing ADD COLUMN paid_policy TEXT')
            c.execute('''CREATE TABLE IF NOT EXISTS _bod_billing_audit(
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, operation TEXT NOT NULL,
                event TEXT, customer TEXT NOT NULL, old_until REAL NOT NULL,
                paid_until REAL NOT NULL, reconciled REAL NOT NULL)''')
        self.transact(setup)

    async def billing_http(self, request, token, raw):
        if str(getattr(self.env, 'BOD_BILLING_MODE', 'unavailable')) == 'unavailable':
            return response('Payments are unavailable in this pilot. Your original 31-day trial is unchanged. '
                            '<a href="/billing">Family access and support</a>', 503)
        if request.method != 'POST':
            return response('Method not allowed', 405)
        principal = self.transact(lambda c: self.authenticate(c, token))
        if not principal:
            return response('Sign in required', 401)
        try:
            form = parse_qs(raw.decode())
            csrf = request.headers.get('X-CSRF-Token') or form.get('csrf_token', [''])[0]
            if (request.headers.get('Origin') != str(self.env.BOD_PUBLIC_ORIGIN)
                    or not hmac.compare_digest(csrf, principal['csrf'])):
                return response('Request denied', 403)
        except Exception:
            return response('Request denied', 403)
        if principal['is_guest']:
            return response(status=302, location='/auth/login')
        try:
            result = json.loads(await self.billing(json.dumps(dict(
                operation=urlsplit(request.url).path.rsplit('/', 1)[-1], token=token))))
            return response(status=303, location=result['location'])
        except Exception as error:
            print('Stripe billing unavailable:', type(error).__name__)
            return response('Billing is temporarily unavailable. Please try again. <a href="/billing">Back</a>', 503)

    async def billing(self, payload):
        data = json.loads(payload)
        if not hasattr(self, '_billing_lock'):
            self._billing_lock = asyncio.Lock()
        # One operation, including its network awaits, per family. A durable epoch
        # additionally fences commits; a restarted DO never resumes old promises.
        async with self._billing_lock:
            stripe = Stripe(self.env)
            await stripe.validate()
            self.billing_setup()
            def begin(c):
                if data['operation'] != 'webhook':
                    principal = self.authenticate(c, data['token'])
                    if not principal or principal['is_guest']:
                        raise ValueError('Signed-in parent required')
                family = c.execute('SELECT * FROM _bod_family WHERE singleton=1').fetchone()
                if not family or not family['issuer']:
                    raise ValueError('Unclaimed family')
                saved = c.execute('SELECT scope FROM _bod_billing').fetchone()
                if saved['scope'] != stripe.scope:
                    if family['customer'] or family['paid_until']:
                        raise ValueError('Billing scope migration requires operator reconciliation')
                    c.execute('UPDATE _bod_billing SET scope=? WHERE singleton=1', (stripe.scope,))
                if data['operation'] == 'webhook':
                    if family['customer'] != data['customer']:
                        raise ValueError('Wrong customer')
                    old = c.execute('SELECT digest FROM _bod_stripe_events WHERE id=?', (data['event'],)).fetchone()
                    if old:
                        if old['digest'] != data['digest']:
                            raise ValueError('Conflicting event')
                        return family, None
                c.execute('UPDATE _bod_billing SET epoch=epoch+1 WHERE singleton=1')
                return family, c.execute('SELECT epoch FROM _bod_billing').fetchone()['epoch']
            family, epoch = self.transact(begin)
            if epoch is None:
                await self.ctx.storage.setAlarm(int(time.time() * 1000) + 1)
                return json.dumps({'location': '/billing'})
            def write(operation):
                def fenced(c):
                    if c.execute('SELECT epoch FROM _bod_billing').fetchone()['epoch'] != epoch:
                        raise ValueError('Stale billing operation')
                    return operation(c)
                return self.transact(fenced)
            operation = data['operation']
            customer = family['customer']
            if not customer and operation == 'checkout':
                obj = await stripe.api('POST', 'customers', key='bod-customer-' + family['id'])
                customer = obj['id']
                if not re.fullmatch(r'cus_[A-Za-z0-9]+', customer) or obj.get('livemode') is not stripe.live:
                    raise ValueError('Invalid customer')
                write(lambda c: c.execute('UPDATE _bod_family SET customer=? WHERE singleton=1', (customer,)))
            if not customer:
                return json.dumps({'location': '/billing'})
            await stripe.customer(customer)
            directory = self.env.DIRECTORY.get(self.env.DIRECTORY.idFromName('google-v1'))
            await directory.map_customer(json.dumps(dict(customer=customer, family=family['id'], scope=stripe.scope)))
            if operation == 'portal':
                portal = await stripe.portal(customer)
                return json.dumps({'location': stripe.hosted_url(portal, portal=True)})
            subscriptions = await stripe.subscriptions(customer)
            until = await stripe.paid_until(subscriptions, customer)
            def reconcile(c):
                c.execute('''INSERT INTO _bod_billing_audit
                    (scope,operation,event,customer,old_until,paid_until,reconciled) VALUES (?,?,?,?,?,?,?)''',
                    (stripe.scope, operation, data.get('event'), customer, family['paid_until'], until, time.time()))
                c.execute('UPDATE _bod_family SET paid_until=? WHERE singleton=1', (until,))
                c.execute("UPDATE _bod_billing SET reconciled=?,paid_policy='invoice-v1' WHERE singleton=1", (time.time(),))
                if operation == 'webhook':
                    c.execute('INSERT INTO _bod_stripe_events VALUES (?,?,?)', (data['event'], data['digest'], time.time()))
            write(reconcile)  # receipt + entitlement commit together, or neither does
            # Schedule post-commit notification separately from provider I/O.
            # The alarm publishes and rearms the next entitlement/session expiry.
            await self.ctx.storage.setAlarm(int(time.time() * 1000) + 1)
            if operation != 'checkout' or until > time.time():
                return json.dumps({'location': '/billing'})
            if any(sub['status'] not in TERMINAL for sub in subscriptions):
                return json.dumps({'location': stripe.hosted_url(await stripe.portal(customer), portal=True)})
            price = await stripe.api('GET', 'prices/' + stripe.price)
            stripe.validate_price(price, checkout=True)
            await stripe.validate(checkout=True)
            saved = self.transact(lambda c: c.execute('SELECT * FROM _bod_billing').fetchone())
            if saved['checkout_id']:
                previous = await stripe.api('GET', 'checkout/sessions/' + saved['checkout_id'])
                if previous['customer'] != customer or previous['livemode'] is not stripe.live:
                    raise ValueError('Wrong Checkout customer/mode')
                if previous['status'] == 'open' and previous['expires_at'] > time.time() + 60:
                    return json.dumps({'location': stripe.hosted_url(previous)})
                if previous['status'] == 'complete':
                    sub = await stripe.api('GET', 'subscriptions/' + previous['subscription'])
                    if sub['customer'] != customer or sub['livemode'] is not stripe.live:
                        raise ValueError('Wrong subscription customer/mode')
                    if sub['status'] not in TERMINAL:
                        return json.dumps({'location': stripe.hosted_url(await stripe.portal(customer), portal=True)})
            # Persist the attempt BEFORE issuing a potentially ambiguous POST.
            # Retry an uncertain creation with exactly the same idempotency key.
            attempt = saved['attempt'] or secrets.token_hex(16)
            write(lambda c: c.execute('UPDATE _bod_billing SET attempt=? WHERE singleton=1', (attempt,)))
            checkout = await stripe.api('POST', 'checkout/sessions', {
                'mode': 'subscription', 'customer': customer, 'line_items[0][price]': stripe.price,
                # Keep this session on ordinary Stripe Payments, regardless of account default.
                'managed_payments[enabled]': 'false',
                'line_items[0][quantity]': 1, 'success_url': str(self.env.BOD_PUBLIC_ORIGIN) + '/billing',
                'cancel_url': str(self.env.BOD_PUBLIC_ORIGIN) + '/billing'}, key='bod-checkout-' + family['id'] + '-' + attempt)
            url = stripe.hosted_url(checkout)
            if checkout['customer'] != customer or checkout['livemode'] is not stripe.live:
                raise ValueError('Wrong Checkout customer/mode')
            write(lambda c: c.execute('UPDATE _bod_billing SET checkout_id=?,checkout_expires=?,attempt=NULL WHERE singleton=1',
                                      (checkout['id'], checkout['expires_at'])))
            return json.dumps({'location': url})
