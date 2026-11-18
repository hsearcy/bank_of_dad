"""Opt-in hosted identity, isolated family storage and Stripe subscriptions.

No payment or identity dependencies are imported by the free self-hosted mode.
One OIDC parent identity owns one family; anonymous banks use a device capability
until claimed. Email is neither stored nor an identity key.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

from flask import abort, g, jsonify, redirect, render_template, request, session

import db
from hosted_access import TRIAL_SECONDS, access_status, migrate, support_destination


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Hosted:
    def __init__(self, app):
        from authlib.integrations.flask_client import OAuth
        from flask_session.sqlalchemy import SqlAlchemySessionInterface
        from flask_sqlalchemy import SQLAlchemy
        import stripe

        self.app = app
        self.support_url = support_destination()
        self.stripe_module = stripe
        self.origin = os.environ['BOD_PUBLIC_ORIGIN'].rstrip('/')
        self.issuer = os.environ.get('BOD_OIDC_ISSUER', 'https://accounts.google.com')
        # Google documents both spellings in signed ID tokens. Validate against
        # this fixed allowlist, then store the canonical issuer so one Google
        # subject cannot accidentally acquire two families. Other issuers remain exact.
        google = self.issuer == 'https://accounts.google.com'
        self.valid_issuers = [self.issuer, 'accounts.google.com'] if google else [self.issuer]
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
        class DeviceSessions(SqlAlchemySessionInterface):
            def get_expiration_time(self, app, current):
                if 'guest' in current:
                    # Ask the browser to retain the opaque token, without an app
                    # recovery deadline. Browsers may cap/evict persistent cookies.
                    return datetime.max.replace(tzinfo=timezone.utc)
                return super().get_expiration_time(app, current)

            def _retrieve_session_data(self, store_id):
                record = self.sql_session_model.query.filter_by(session_id=store_id).first()
                if record:
                    data = self.serializer.decode(record.data)
                    # Also accept retained tokens from the old fixed-lifetime
                    # guest sessions. Claim/logout still revoke the capability/SID.
                    if 'guest' in data:
                        return data
                return super()._retrieve_session_data(store_id)

            def _upsert_session(self, lifetime, current, store_id):
                if 'guest' not in current:
                    return super()._upsert_session(lifetime, current, store_id)
                # NULL means no anonymous recovery deadline; normal authenticated
                # sessions still use Flask-Session's seven-day expiration policy.
                try:
                    record = self.sql_session_model.query.filter_by(session_id=store_id).first()
                    if record is None:
                        record = self.sql_session_model(session_id=store_id, data=b'', expiry=None)
                        self.client.session.add(record)
                    record.data = self.serializer.encode(current)
                    record.expiry = None
                    self.client.session.commit()
                except Exception:
                    self.client.session.rollback()
                    raise

        app.config['SESSION_SQLALCHEMY'] = SQLAlchemy(app)
        app.session_interface = DeviceSessions(app, client=app.config['SESSION_SQLALCHEMY'])
        # Upgrade retained guest sessions before the normal session_cleanup CLI
        # can delete them under the former recovery deadline. Never extend an
        # authenticated session or restore a deleted token/session.
        with app.app_context():
            interface = app.session_interface
            for saved in interface.sql_session_model.query.filter(interface.sql_session_model.expiry.isnot(None)):
                if 'guest' in interface.serializer.decode(saved.data):
                    saved.expiry = None
            interface.client.session.commit()
        self.oauth = OAuth(app).register(
            'parent', client_id=os.environ['BOD_OIDC_CLIENT_ID'],
            client_secret=os.environ['BOD_OIDC_CLIENT_SECRET'],
            server_metadata_url=self.issuer.rstrip('/') + '/.well-known/openid-configuration',
            # Google requires openid plus profile or email. Do not request email,
            # offline access or other APIs; profile claims are discarded after validation.
            client_kwargs={'scope': 'openid profile' if google else 'openid',
                           'code_challenge_method': 'S256', 'default_timeout': 15},
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
            migrate(conn)
            columns = {row[1] for row in conn.execute('PRAGMA table_info(families)')}
            if 'guest_hash' not in columns:
                conn.execute('ALTER TABLE families ADD COLUMN guest_hash TEXT')
                # Legacy column retained for additive schema compatibility only;
                # anonymous recovery no longer has a time-based cutoff.
                conn.execute('ALTER TABLE families ADD COLUMN guest_expires REAL')
                conn.execute('ALTER TABLE families ADD COLUMN welcome_dismissed INTEGER NOT NULL DEFAULT 0')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(login_states)')}
            if 'guest_family' not in columns:
                conn.execute('ALTER TABLE login_states ADD COLUMN guest_family TEXT')
                conn.execute('ALTER TABLE login_states ADD COLUMN guest_hash TEXT')
        app.before_request(self.guard)
        app.after_request(self.headers)
        app.context_processor(self.account_context)
        app.add_url_rule('/auth/login', 'hosted_login', self.login)
        app.add_url_rule('/auth/callback', 'hosted_callback', self.callback)
        app.add_url_rule('/auth/logout', 'hosted_logout', self.logout, methods=['POST'])
        app.add_url_rule('/auth/signed-out', 'hosted_signed_out', self.signed_out)
        app.add_url_rule('/auth/welcome', 'hosted_welcome', self.dismiss_welcome, methods=['POST'])
        app.add_url_rule('/auth/choose-bank', 'hosted_choose_bank', self.choose_bank, methods=['GET', 'POST'])
        app.add_url_rule('/auth/device-bank', 'hosted_device_bank', self.device_bank, methods=['POST'])
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

    def valid_session(self, row, current):
        if not row:
            return False
        if row['guest_hash'] is not None:
            return hmac.compare_digest(row['guest_hash'], digest(current.get('guest', '')))
        # A claimed guest capability must never become an authenticated session.
        return 'guest' not in current

    def replace_session(self, values):
        session.clear()
        session.update(values)
        session['csrf'] = secrets.token_urlsafe(32)
        session.permanent = True
        self.app.session_interface.regenerate(session)

    def start_guest(self):
        import bank
        family, capability = secrets.token_hex(16), secrets.token_urlsafe(32)
        now = time.time()
        path = self.family_path(family)
        # Nothing can address this bank until initialization succeeds and the
        # directory association commits. Use normal domain actions, not fixtures.
        try:
            db.init_db(path=path)
            conn = db.get_conn(path)
            try:
                bank.add_kid(conn, 'Sample Kid')
                bank.gift(conn, 1, 200, 'Sample money — try your bank')
                bank.move_to_savings(conn, 1, 100)
                bank.buy_bond(conn, 1, '1mo', 100)
                # This brand-new sample has a short, honest history: the $1
                # deposit has already earned about a cent at the normal rate.
                # Age its recorded inception together (including ledger/history
                # and both bond dates), never the bank clock or any existing kid.
                # Allowance starts today. Cap age so custom low/zero rates don't
                # accidentally deliver mature investments or weeks of allowance.
                rate = bank.savings_weekly_rate(conn)
                age = min(7 * 86400 / (100 * rate), 6 * 86400) if rate > 0 else 0
                with conn:
                    conn.execute('UPDATE kids SET created_ts=created_ts-?, savings_accrued_ts=savings_accrued_ts-? WHERE id=1', (age, age))
                    conn.execute('UPDATE transactions SET ts=ts-? WHERE kid_id=1', (age,))
                    conn.execute('UPDATE history SET ts=ts-? WHERE kid_id=1', (age,))
                    conn.execute('UPDATE bonds SET buy_ts=buy_ts-?, mature_ts=mature_ts-? WHERE kid_id=1', (age, age))
            finally:
                conn.close()
            with self.connect() as conn:
                conn.execute('''INSERT INTO families
                    (id, issuer, subject, trial_started, trial_until, guest_hash)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                    (family, 'urn:bank-of-dad:guest', family, now, now + TRIAL_SECONDS,
                     digest(capability)))
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        self.replace_session({'family': family, 'guest': capability})

    def guard(self):
        # Trust only the configured origin, never forwarded Host headers.
        if request.host != urlsplit(self.origin).netloc:
            abort(400)
        public = request.endpoint in ('hosted_login', 'hosted_callback', 'hosted_signed_out', 'hosted_webhook', 'static', 'service_worker')
        if public:
            return None
        family = session.get('family')
        # Only the real dashboard entry creates a trial; API probes, static files,
        # expired sessions and sign-out do not silently manufacture a new bank.
        if not family and request.endpoint == 'index' and request.method == 'GET':
            self.start_guest()
            family = session['family']
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone()
        if not self.valid_session(row, session):
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
            if not access_status(row)['active']:
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

    def socket_authorized(self, family):
        # Flask's request session is a handshake snapshot. Re-read the server-side
        # session so logout/expiry also revoke an already-open idle socket. Do not
        # save/refresh it: a long-lived socket must not extend the session lifetime.
        current = self.app.session_interface.open_session(self.app, request)
        if not current or current.get('family') != family:
            return False
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM families WHERE id=?', (family,)).fetchone()
        return bool(self.valid_session(row, current) and access_status(row)['active'])

    def headers(self, response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    def login(self):
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM families WHERE id=?', (session.get('family'),)).fetchone()
        values = {}
        if 'device_bank' in session:
            values['device_bank'] = session['device_bank']
        if self.valid_session(row, session) and 'guest' in session:
            values = {key: session[key] for key in ('family', 'guest') if key in session}
        # Keep the current guest SID/CSRF usable throughout provider startup.
        # Revoking it here races the dashboard's idle WebSocket recheck/refresh,
        # which can start a second login without the guest before this responds.
        # Rotation belongs at the verified guest callback (privilege transition).
        # Preserve the existing reset behavior for ordinary authenticated login.
        if 'guest' not in values:
            self.replace_session(values)
        if 'browser' not in session:
            session['browser'] = secrets.token_urlsafe(32)
        # In particular after an existing-family collision, let a Google parent
        # choose a different identity rather than silently reusing provider SSO.
        options = {'prompt': 'select_account'} if 'guest' in values and self.issuer == 'https://accounts.google.com' else {}
        response = self.oauth.authorize_redirect(self.origin + '/auth/callback', **options)
        state = parse_qs(urlsplit(response.location).query)['state'][0]
        with self.connect() as conn:
            conn.execute('DELETE FROM login_states WHERE expires < ?', (time.time(),))
            conn.execute('INSERT INTO login_states(state, browser, expires, guest_family, guest_hash) VALUES (?, ?, ?, ?, ?)',
                         (digest(state), digest(session['browser']), time.time() + 600,
                          values.get('family') if 'guest' in values else None,
                          digest(values['guest']) if 'guest' in values else None))
        return response

    def callback(self):
        # Atomically consume state bound to this browser before token exchange.
        if any(len(request.args.getlist(k)) != 1 for k in ('state', 'code')) or request.args.get('error'):
            return 'Sign-in failed. Please try again.', 400
        with self.connect() as conn:
            login_state = conn.execute('DELETE FROM login_states WHERE state=? AND browser=? AND expires>? RETURNING *',
                                      (digest(request.args['state']), digest(session.get('browser', '')), time.time())).fetchone()
            if login_state is None:
                return 'Sign-in failed. Please try again.', 400
        try:
            token = self.oauth.authorize_access_token(
                claims_options={'iss': {'essential': True, 'values': self.valid_issuers},
                                'sub': {'essential': True}, 'iat': {'essential': True},
                                'exp': {'essential': True}}, leeway=30)
            claims = token['userinfo']
            now = time.time()
            if (claims['iss'] not in self.valid_issuers or not isinstance(claims['sub'], str) or not claims['sub']
                    or claims.get('azp', os.environ['BOD_OIDC_CLIENT_ID']) != os.environ['BOD_OIDC_CLIENT_ID']
                    or not now - 600 <= claims['iat'] <= now + 30):
                raise ValueError('Invalid identity claims')
        except Exception:
            # Do not expose provider errors, tokens or personal claims.
            self.replace_session({key: session[key] for key in ('family', 'guest', 'device_bank') if key in session})
            return 'Sign-in failed. Please try again.', 400
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            guest = conn.execute('SELECT * FROM families WHERE id=?', (login_state['guest_family'],)).fetchone()
            can_claim = (guest is not None and guest['guest_hash'] is not None
                         and guest['guest_hash'] == login_state['guest_hash']
                         and session.get('family') == guest['id'] and self.valid_session(guest, session))
            existing = conn.execute('SELECT id FROM families WHERE issuer=? AND subject=?',
                                    (self.issuer, claims['sub'])).fetchone()
            if login_state['guest_family'] and not can_claim and (existing is None or existing['id'] != login_state['guest_family']):
                # Expiry/another claimant must not silently open a blank bank and
                # imply that guest progress was imported. A same-owner retry is safe.
                return ('This device bank is no longer available to save. Nothing was imported. '
                        '<a href="/auth/login">Sign in again to open your saved family bank</a>.', 409)
            if can_claim and existing is not None:
                # Never merge, replace or delete either bank. The authenticated
                # identity permits opening the existing bank only by explicit choice.
                self.replace_session({'family': guest['id'], 'guest': session['guest'],
                                      'bank_choice': {'family': existing['id'], 'expires': now + 600}})
                return redirect('/auth/choose-bank')
            if can_claim:
                # One-time, writer-serialized ownership transfer: no bank file
                # copying, no partial import, and no new trial or payment customer.
                conn.execute('UPDATE families SET issuer=?, subject=?, guest_hash=NULL, guest_expires=NULL WHERE id=?',
                             (self.issuer, claims['sub'], guest['id']))
                family = guest['id']
            else:
                # Only the first creation starts a trial. Login and client inputs
                # can never reset these server-owned timestamps.
                conn.execute('INSERT OR IGNORE INTO families(id, issuer, subject, trial_started, trial_until) VALUES (?, ?, ?, ?, ?)',
                             (secrets.token_hex(16), self.issuer, claims['sub'], now, now + TRIAL_SECONDS))
                family = conn.execute('SELECT id FROM families WHERE issuer=? AND subject=?',
                                      (self.issuer, claims['sub'])).fetchone()['id']
        db.init_db(path=self.family_path(family))
        values = {'family': family}
        if 'device_bank' in session:
            values['device_bank'] = session['device_bank']
        self.replace_session(values)
        return redirect('/')

    def dismiss_welcome(self):
        if g.hosted_family['guest_hash'] is not None:
            with self.connect() as conn:
                conn.execute('UPDATE families SET welcome_dismissed=1 WHERE id=?', (g.hosted_family['id'],))
        return '', 204

    def choose_bank(self):
        choice = session.get('bank_choice', {})
        if not choice or choice['expires'] <= time.time() or 'guest' not in session:
            return redirect('/')
        if request.method == 'GET':
            return render_template('choose_bank.html')
        if request.form.get('choice') == 'guest':
            session.pop('bank_choice', None)
            return redirect('/')
        if request.form.get('choice') != 'existing':
            abort(400)
        device = {'family': session['family'], 'guest': session['guest']}
        self.replace_session({'family': choice['family'], 'device_bank': device})
        return redirect('/')

    def device_bank(self):
        device = session.get('device_bank')
        if not device:
            return redirect('/')
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM families WHERE id=?', (device['family'],)).fetchone()
        if not self.valid_session(row, device):
            session.pop('device_bank', None)
            return redirect('/')
        self.replace_session(device)
        return redirect('/')

    def logout(self):
        session.clear()  # Flask-Session deletes the server-side session and cookie.
        return redirect('/auth/signed-out')

    def signed_out(self):
        return '<p>You are signed out of Bank of Dad.</p><a href="/auth/login">Sign in</a>'

    def billing(self):
        return render_template('billing.html')

    def account_context(self):
        family = getattr(g, 'hosted_family', None)
        if family is None:
            return {}
        return {'hosted_account': access_status(family), 'hosted_family': family,
                'hosted_guest': family['guest_hash'] is not None, 'support_url': self.support_url}

    def customer(self, conn):
        family = conn.execute('SELECT * FROM families WHERE id=?', (g.hosted_family['id'],)).fetchone()
        if not family['customer']:
            customer = self.stripe.v1.customers.create(
                {}, options={'idempotency_key': 'bod-customer-' + family['id']})
            conn.execute('UPDATE families SET customer=? WHERE id=?', (customer.id, family['id']))
            return customer.id
        return family['customer']

    def reconcile(self, conn, customer, subscriptions=None):
        # Caller holds SQLite's writer lock across the bounded API read and write.
        # No concurrent webhook/refresh can overwrite a newer snapshot with an older fetch.
        until = 0
        if subscriptions is None:
            subscriptions = self.stripe.v1.subscriptions.list(
                {'customer': customer, 'status': 'all', 'limit': 100}).auto_paging_iter()
        for sub in subscriptions:
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
        if g.hosted_family['guest_hash'] is not None:
            return redirect('/auth/login')
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
                subscriptions = list(self.stripe.v1.subscriptions.list(
                    {'customer': customer, 'status': 'all', 'limit': 100}).auto_paging_iter())
                if self.reconcile(conn, customer, subscriptions) > time.time():
                    return redirect('/billing')
                # Checkout expiry is not subscription expiry: past_due/unpaid
                # subscriptions can keep retrying payment long after Checkout.
                # Use the same current snapshot, including all pages, fail closed
                # for unknown statuses and recover existing billing in the portal.
                if any(sub.status not in ('canceled', 'incomplete_expired') for sub in subscriptions):
                    portal = self.stripe.v1.billing_portal.sessions.create({
                        'customer': customer, 'return_url': self.origin + '/billing'})
                    return redirect(portal.url, code=303)
                family = conn.execute('SELECT * FROM families WHERE customer=?', (customer,)).fetchone()
                if family['checkout_expires'] and family['checkout_expires'] > time.time() + 60:
                    existing = self.stripe.v1.checkout.sessions.retrieve(family['checkout_id'])
                    if existing.status == 'open':
                        return redirect(existing.url, code=303)
                    if existing.status == 'complete':
                        prior = self.stripe.v1.subscriptions.retrieve(existing.subscription)
                        if prior.status not in ('canceled', 'incomplete_expired'):
                            portal = self.stripe.v1.billing_portal.sessions.create({
                                'customer': customer, 'return_url': self.origin + '/billing'})
                            return redirect(portal.url, code=303)
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
