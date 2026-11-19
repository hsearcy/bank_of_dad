"""Flask boundary for the Cloudflare guest and signed-in family bank.

The Durable Object authenticates first, then supplies transaction-local state.
No filesystem sessions, fake Google identity or pretend payment success.
"""
import hmac
from urllib.parse import urlsplit

from flask import abort, g, jsonify, redirect, render_template, request, session
from flask.sessions import SessionInterface, SecureCookieSession

import db
from hosted_access import access_status, support_destination


class ObjectSession(SessionInterface):
    def open_session(self, app, req):
        family = req.environ['bod.family']
        return SecureCookieSession({'family': family['id'], 'csrf': family['csrf'],
                                    **({'device_bank': True} if family.get('device_bank') else {})})

    def save_session(self, app, current, response):
        # Cookie installation/revocation belongs to the authenticated DO gateway.
        pass


class CloudflareGuest:
    def __init__(self, app):
        app.session_interface = ObjectSession()
        # A swallowed domain/SQL error must not commit a partial bank mutation.
        app.config.update(PROPAGATE_EXCEPTIONS=True, MAX_CONTENT_LENGTH=64 * 1024)
        app.before_request(self.guard)
        app.after_request(self.headers)
        app.context_processor(self.account_context)
        app.add_url_rule('/auth/welcome', 'cloud_welcome', self.welcome, methods=['POST'])
        app.add_url_rule('/auth/logout', 'cloud_logout', self.logout, methods=['POST'])
        app.add_url_rule('/billing', 'cloud_billing', lambda: render_template('billing.html'))
        # Network-bound auth/billing requests are intercepted by the DO gateway
        # before this synchronous Flask transaction.
        for path in ('/auth/login', '/auth/callback', '/auth/choose-bank', '/auth/device-bank'):
            app.add_url_rule(path, 'unavailable_' + path, self.unavailable, methods=['GET', 'POST'])

    def guard(self):
        family = request.environ['bod.family']
        g.hosted_family = family
        origin = request.environ['bod.origin']
        if request.host != urlsplit(origin).netloc:
            abort(400)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if (not hmac.compare_digest(token, session['csrf'])
                    or request.headers.get('Origin') != origin):
                abort(403)
        if (request.path.startswith('/api/') or request.endpoint == 'index') and not access_status(family)['active']:
            if request.path.startswith('/api/'):
                return jsonify(error='Subscription required', billing='/billing'), 402
            return redirect('/billing')
        if request.endpoint == 'api_admin_status':
            return jsonify(pin_set=False, hosted_parent=True)
        if request.endpoint == 'api_admin_verify':
            return jsonify(ok=True)
        if request.endpoint == 'api_admin_set_pin':
            return jsonify(ok=False, message='This device bank uses its browser token, not a PIN.'), 403
        if request.path.startswith('/api/') and request.method == 'POST':
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify(error='A JSON object is required'), 400
            for field, table in (('kid_id', 'kids'), ('tx_id', 'transactions')):
                if field in body:
                    try:
                        key = int(body[field])
                    except (ValueError, TypeError, OverflowError):
                        return jsonify(error='Invalid object ID'), 400
                    if not db.get_conn().execute(f'SELECT 1 FROM {table} WHERE id=?', (key,)).fetchone():
                        return jsonify(error='Object not found'), 404

    def account_context(self):
        family = request.environ['bod.family']
        return dict(hosted_account=access_status(family), hosted_family=family,
                    hosted_guest=family['is_guest'],
                    support_url=support_destination(request.environ.get('bod.support_url', '')),
                    cloudflare_incomplete=request.environ.get('bod.local_preview', False),
                    public_pilot=request.environ.get('bod.public_pilot', False))

    def welcome(self):
        db.get_conn().execute('UPDATE _bod_family SET welcome_dismissed=1 WHERE singleton=1')
        return '', 204

    def logout(self):
        if request.environ['bod.family']['is_guest']:
            db.get_conn().execute('UPDATE _bod_family SET revoked=1 WHERE singleton=1')
        else:
            import hashlib
            from http.cookies import SimpleCookie
            jar = SimpleCookie(request.headers.get('Cookie', ''))
            capability = jar['bod-local-session'].value.split('.')[1]
            db.get_conn().execute('DELETE FROM _bod_sessions WHERE hash=?',
                                  (hashlib.sha256(capability.encode()).hexdigest(),))
        request.environ['bod.logged_out'] = True
        return redirect('/auth/signed-out')

    def unavailable(self):
        return ('<h1>Not available in this local Cloudflare port yet</h1>'
                '<p>This request must use the identity gateway. '
                'No account was linked and no payment was started.</p><a href="/">Back to your bank</a>', 503)

    def headers(self, response):
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
                                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'})
        return response
