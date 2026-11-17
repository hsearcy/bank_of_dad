"""Opt-in hosted identity, isolated family storage and Stripe subscriptions.

No payment or identity dependencies are imported by the free self-hosted mode.
One OIDC parent identity owns one family. Email is neither stored nor an identity key.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from datetime import timedelta
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

from flask import abort, g, jsonify, redirect, render_template, request, session

import db


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Hosted:
    def __init__(self, app):
        from authlib.integrations.flask_client import OAuth
        from flask_session import Session
        from flask_sqlalchemy import SQLAlchemy
        import stripe

        self.app = app
        self.stripe_module = stripe
        self.origin = os.environ['BOD_PUBLIC_ORIGIN'].rstrip('/')
        self.issuer = os.environ['BOD_OIDC_ISSUER']
        for url in (self.origin, self.issuer):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError('Hosted origin and OIDC issuer must be HTTPS URLs')
        if urlsplit(self.origin).path:
            raise ValueError('BOD_PUBLIC_ORIGIN must not contain a path')
        self.root = Path(os.environ['BOD_HOSTED_DATA']).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = self.root / 'directory.sqlite'
        self.price = os.environ['BOD_STRIPE_PRICE_ID']
        self.webhook_secret = os.environ['BOD_STRIPE_WEBHOOK_SECRET']
        mode = os.environ['BOD_STRIPE_LIVEMODE']
        if mode not in ('true', 'false'):
            raise ValueError('BOD_STRIPE_LIVEMODE must be true or false')
        self.live = mode == 'true'
        secret = os.environ['BOD_SESSION_SECRET']
        if len(secret) < 32:
            raise ValueError('BOD_SESSION_SECRET must be at least 32 characters')
        app.config.update(
            SECRET_KEY=secret, SESSION_TYPE='sqlalchemy',
            SQLALCHEMY_DATABASE_URI='sqlite:///' + str(self.root / 'sessions.sqlite'),
            SESSION_COOKIE_NAME='__Host-bod-session', SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
            PERMANENT_SESSION_LIFETIME=timedelta(days=7), SESSION_REFRESH_EACH_REQUEST=False,
            SESSION_PERMANENT=True, MAX_CONTENT_LENGTH=1024 * 1024,
        )
        app.config['SESSION_SQLALCHEMY'] = SQLAlchemy(app)
        Session(app)
        self.oauth = OAuth(app).register(
            'parent', client_id=os.environ['BOD_OIDC_CLIENT_ID'],
            client_secret=os.environ['BOD_OIDC_CLIENT_SECRET'],
            server_metadata_url=self.issuer.rstrip('/') + '/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid', 'code_challenge_method': 'S256', 'default_timeout': 15},
            id_token_signing_alg_values_supported=['RS256'],
        )
        self.stripe = stripe.StripeClient(
            os.environ['BOD_STRIPE_SECRET_KEY'], max_network_retries=1,
            http_client=stripe.RequestsClient(timeout=15),
        )
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS families (
                    id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    customer TEXT UNIQUE, paid_until REAL NOT NULL DEFAULT 0,
                    checkout_id TEXT, checkout_url TEXT, checkout_expires REAL,
                    UNIQUE(issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS login_states (
                    state TEXT PRIMARY KEY, browser TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stripe_events (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, received REAL NOT NULL
                );
            ''')
        app.before_request(self.guard)
        app.after_request(self.headers)
        app.add_url_rule('/auth/login', 'hosted_login', self.login)
        app.add_url_rule('/auth/callback', 'hosted_callback', self.callback)
        app.add_url_rule('/auth/logout', 'hosted_logout', self.logout, methods=['POST'])
        app.add_url_rule('/auth/signed-out', 'hosted_signed_out', self.signed_out)
        app.add_url_rule('/billing', 'hosted_billing', self.billing)
        app.add_url_rule('/billing/checkout', 'hosted_checkout', self.checkout, methods=['POST'])
        app.add_url_rule('/billing/portal', 'hosted_portal', self.portal, methods=['POST'])
        app.add_url_rule('/billing/refresh', 'hosted_refresh', self.refresh, methods=['POST'])
        app.add_url_rule('/billing/webhook', 'hosted_webhook', self.webhook, methods=['POST'])
        app.extensions['bod_hosted'] = self

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.directory, timeout=35)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def family_path(self, family):
        if not re.fullmatch(r'[0-9a-f]{32}', family):
            raise ValueError('Invalid server-owned family identifier')
        return str(self.root / ('family-' + family + '.sqlite'))

    def guard(self):
        # Trust only the configured origin, never forwarded Host headers.
        if request.host != urlsplit(self.origin).netloc:
            abort(400)
        public = request.endpoint in ('hosted_login', 'hosted_callback', 'hosted_signed_out', 'hosted_webhook', 'static', 'service_worker')
        if public:
            return None
        family = session.get('family')
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone()
        if not row:
            if request.path.startswith('/api/'):
                return jsonify(error='Sign in required', login='/auth/login'), 401
            return redirect('/auth/login')
        g.hosted_family = row
        g.bank_db_path = self.family_path(row['id'])
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not hmac.compare_digest(token, session.get('csrf', 'invalid')):
                abort(403)
            if request.headers.get('Origin') not in (None, self.origin):
                abort(403)
        if request.path.startswith('/api/') or request.endpoint == 'index':
            if row['paid_until'] <= time.time():
                if request.path.startswith('/api/'):
                    return jsonify(error='Subscription required', billing='/billing'), 402
                return redirect('/billing')
        if request.endpoint == 'api_admin_status':
            return jsonify(pin_set=False, hosted_parent=True)
        if request.endpoint == 'api_admin_verify':
            return jsonify(ok=True)
        if request.endpoint == 'api_admin_set_pin':
            return jsonify(ok=False, message='Hosted controls use your parent sign-in, not a PIN.'), 403
        if request.path.startswith('/api/') and request.method == 'POST':
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify(error='A JSON object is required'), 400
            # Object IDs are local to the authenticated family, never global.
            for field, table in (('kid_id', 'kids'), ('tx_id', 'transactions')):
                if field in body:
                    try:
                        object_id = int(body[field])
                    except (TypeError, ValueError):
                        return jsonify(error='Invalid object ID'), 400
                    conn = db.get_conn()
                    try:
                        exists = conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (object_id,)).fetchone()
                    finally:
                        conn.close()
                    if not exists:
                        return jsonify(error='Object not found'), 404
        return None

    def headers(self, response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    def login(self):
        session.clear()
        session['browser'] = secrets.token_urlsafe(32)
        session.permanent = True
        self.app.session_interface.regenerate(session)
        response = self.oauth.authorize_redirect(self.origin + '/auth/callback')
        state = parse_qs(urlsplit(response.location).query)['state'][0]
        with self.connect() as conn:
            conn.execute('DELETE FROM login_states WHERE expires < ?', (time.time(),))
            conn.execute('INSERT INTO login_states VALUES (?, ?, ?)',
                         (digest(state), digest(session['browser']), time.time() + 600))
        return response

    def callback(self):
        # Atomically consume state bound to this browser before token exchange.
        if any(len(request.args.getlist(k)) != 1 for k in ('state', 'code')) or request.args.get('error'):
            return 'Sign-in failed. Please try again.', 400
        with self.connect() as conn:
            result = conn.execute('DELETE FROM login_states WHERE state=? AND browser=? AND expires>?',
                                  (digest(request.args['state']), digest(session.get('browser', '')), time.time()))
            if result.rowcount != 1:
                return 'Sign-in failed. Please try again.', 400
        try:
            token = self.oauth.authorize_access_token(
                claims_options={'iss': {'essential': True, 'value': self.issuer},
                                'sub': {'essential': True}, 'iat': {'essential': True},
                                'exp': {'essential': True}}, leeway=30)
            claims = token['userinfo']
            now = time.time()
            if (claims['iss'] != self.issuer or not isinstance(claims['sub'], str) or not claims['sub']
                    or claims.get('azp', os.environ['BOD_OIDC_CLIENT_ID']) != os.environ['BOD_OIDC_CLIENT_ID']
                    or not now - 600 <= claims['iat'] <= now + 30):
                raise ValueError('Invalid identity claims')
        except Exception:
            # Do not expose provider errors, tokens or personal claims.
            session.clear()
            return 'Sign-in failed. Please try again.', 400
        with self.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO families(id, issuer, subject) VALUES (?, ?, ?)',
                         (secrets.token_hex(16), self.issuer, claims['sub']))
            family = conn.execute('SELECT id FROM families WHERE issuer=? AND subject=?',
                                  (self.issuer, claims['sub'])).fetchone()['id']
        db.init_db(path=self.family_path(family))
        session.clear()
        # regenerate only acts on nonempty sessions in Flask-Session.
        session['family'] = family
        session['csrf'] = secrets.token_urlsafe(32)
        session.permanent = True
        self.app.session_interface.regenerate(session)
        return redirect('/')

    def logout(self):
        session.clear()  # Flask-Session deletes the server-side session and cookie.
        return redirect('/auth/signed-out')

    def signed_out(self):
        return '<p>You are signed out of Bank of Dad.</p><a href="/auth/login">Sign in</a>'

    def billing(self):
        return render_template('billing.html', family=g.hosted_family,
                               active=g.hosted_family['paid_until'] > time.time())

    def customer(self, conn):
        family = conn.execute('SELECT * FROM families WHERE id=?', (g.hosted_family['id'],)).fetchone()
        if not family['customer']:
            customer = self.stripe.v1.customers.create(
                {}, options={'idempotency_key': 'bod-customer-' + family['id']})
            conn.execute('UPDATE families SET customer=? WHERE id=?', (customer.id, family['id']))
            return customer.id
        return family['customer']

    def reconcile(self, conn, customer):
        # Caller holds SQLite's writer lock across the bounded API read and write.
        # No concurrent webhook/refresh can overwrite a newer snapshot with an older fetch.
        until = 0
        subscriptions = self.stripe.v1.subscriptions.list({'customer': customer, 'status': 'all', 'limit': 100})
        for sub in subscriptions.auto_paging_iter():
            sub = sub.to_dict()
            if sub['status'] != 'active' or sub['livemode'] != self.live:
                continue
            for item in sub['items']['data']:
                if item['price']['id'] == self.price and item.get('quantity', 1) == 1:
                    end = item['current_period_end']
                    if sub.get('cancel_at'):
                        end = min(end, sub['cancel_at'])
                    until = max(until, end)
        conn.execute('UPDATE families SET paid_until=? WHERE customer=?', (until, customer))
        return until

    def checkout(self):
        try:
            # Validate the operator's configured Price, not anything supplied by a browser.
            price = self.stripe.v1.prices.retrieve(self.price)
            if (not price['active'] or price['livemode'] != self.live or price['currency'] != 'usd'
                    or price['unit_amount'] != 999 or price['recurring']['interval'] != 'year'
                    or price['recurring']['interval_count'] != 1):
                return 'Configure an active USD $9.99/year Stripe Price.', 503
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                customer = self.customer(conn)
            # Customer association is durable before Checkout can emit any webhook.
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if self.reconcile(conn, customer) > time.time():
                    return redirect('/billing')
                family = conn.execute('SELECT * FROM families WHERE customer=?', (customer,)).fetchone()
                if family['checkout_expires'] and family['checkout_expires'] > time.time() + 60:
                    existing = self.stripe.v1.checkout.sessions.retrieve(family['checkout_id'])
                    if existing.status == 'open':
                        return redirect(existing.url, code=303)
                    if existing.status == 'complete':
                        prior = self.stripe.v1.subscriptions.retrieve(existing.subscription)
                        if prior.status not in ('canceled', 'incomplete_expired'):
                            return redirect('/billing')  # Payment processing/failure: use the portal.
                # Stable per time window for retry after an uncertain API response.
                window = int(time.time() // 1800)
                checkout = self.stripe.v1.checkout.sessions.create({
                    'mode': 'subscription', 'customer': customer,
                    'line_items': [{'price': self.price, 'quantity': 1}],
                    'success_url': self.origin + '/billing', 'cancel_url': self.origin + '/billing',
                    'client_reference_id': family['id'],
                }, options={'idempotency_key': 'bod-checkout-' + family['id'] + '-' + str(family['checkout_id']) + '-' + str(window)})
                conn.execute('UPDATE families SET checkout_id=?, checkout_url=?, checkout_expires=? WHERE id=?',
                             (checkout.id, checkout.url, checkout.expires_at, family['id']))
            return redirect(checkout.url, code=303)
        except self.stripe_module.StripeError:
            return 'Billing is temporarily unavailable. Please try again.', 503

    def portal(self):
        if not g.hosted_family['customer']:
            return redirect('/billing')
        try:
            portal = self.stripe.v1.billing_portal.sessions.create({
                'customer': g.hosted_family['customer'], 'return_url': self.origin + '/billing'})
            return redirect(portal.url, code=303)
        except self.stripe_module.StripeError:
            return 'Billing is temporarily unavailable. Please try again.', 503

    def refresh(self):
        if g.hosted_family['customer']:
            try:
                with self.connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    self.reconcile(conn, g.hosted_family['customer'])
            except self.stripe_module.StripeError:
                return 'Billing is temporarily unavailable. Please try again.', 503
        return redirect('/billing')

    def webhook(self):
        raw = request.get_data()
        try:
            # SDK validates past tolerance; explicitly bound future timestamps as well.
            signature = request.headers.get('Stripe-Signature', '')
            stamp = int(next(p[2:] for p in signature.split(',') if p.startswith('t=')))
            if abs(time.time() - stamp) > 300:
                raise ValueError('Signature timestamp outside tolerance')
            event = self.stripe_module.Webhook.construct_event(raw, signature, self.webhook_secret, tolerance=300).to_dict()
            if event['livemode'] != self.live or event.get('account') or event.get('context'):
                raise ValueError('Wrong mode or account')
        except (ValueError, KeyError, StopIteration, self.stripe_module.SignatureVerificationError):
            return '', 400
        payload_hash = hashlib.sha256(raw).hexdigest()
        try:
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                old = conn.execute('SELECT digest FROM stripe_events WHERE id=?', (event['id'],)).fetchone()
                if old:
                    return ('', 200) if old['digest'] == payload_hash else ('', 400)
                relevant = event['type'].startswith('customer.subscription.') or event['type'] in (
                    'checkout.session.completed', 'checkout.session.async_payment_succeeded',
                    'invoice.paid', 'invoice.payment_failed', 'invoice.payment_action_required')
                if relevant:
                    customer = event['data']['object'].get('customer')
                    if isinstance(customer, str) and conn.execute(
                            'SELECT 1 FROM families WHERE customer=?', (customer,)).fetchone():
                        # Never trust a success URL, client reference, email or event metadata for entitlement.
                        self.reconcile(conn, customer)
                conn.execute('INSERT INTO stripe_events VALUES (?, ?, ?)', (event['id'], payload_hash, time.time()))
            return '', 200
        except (self.stripe_module.StripeError, sqlite3.Error):
            # No receipt/entitlement is committed: Stripe must retry.
            return '', 503


def enabled():
    mode = os.environ.get('BANK_OF_DAD_MODE', 'self-hosted')
    if mode not in ('self-hosted', 'hosted'):
        raise ValueError('BANK_OF_DAD_MODE must be self-hosted or hosted')
    return mode == 'hosted'
