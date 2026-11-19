"""Actual hosted UI + wss push in isolated Chromium contexts, polling disabled."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import os
import threading

import pytest
from werkzeug.serving import make_server

from test_hosted import hosted_app, signed_event, subscription


INIT = """(() => {
  window.__sockets = []; window.__frames = [];
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args); window.__sockets.push(this);
      this.addEventListener('message', e => window.__frames.push(e.data));
    }
  };
  const interval = window.setInterval;
  window.setInterval = (fn, ms, ...args) => ms === 30000 ? 0 : interval(fn, ms, ...args);
})();"""


def test_hosted_browser_push_isolation_reconnect_and_expiry(hosted_app):
    sync_api = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium')
    if not os.path.exists(executable):
        pytest.skip('Set BOD_TEST_CHROMIUM to an installed Chromium executable')
    app, host, state, _ = hosted_app
    server = make_server('127.0.0.1', 0, app, threaded=True, ssl_context='adhoc')
    origin = 'https://localhost:' + str(server.server_port)
    host.origin = origin
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
            errors = []
            def context(**kwargs):
                ctx = browser.new_context(ignore_https_errors=True, **kwargs)
                ctx.add_init_script(INIT)
                # No unrelated missing favicon may trigger provider auto-SSO.
                ctx.route('**/favicon.ico', lambda route: route.fulfill(status=204))
                return ctx
            def page_for(ctx):
                page = ctx.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                return page
            def wallet(page, amount):
                sync_api.expect(page.locator('#kids .big')).to_have_text(amount)
            def subscribed(page):
                page.wait_for_function('window.__sockets.some(ws => ws.readyState === 1) && window.__frames.length > 0')
            def parent(subject, customer, name):
                state['subject'] = subject
                ctx = context()
                def stop_at_checkout(route):
                    response = route.fetch(max_redirects=0)
                    assert response.status == 303
                    assert response.headers['location'] == 'https://checkout.stripe.com/c/pay/synthetic'
                    route.fulfill(status=200, content_type='text/html', body='<h1>Synthetic Checkout</h1>')
                ctx.route('**/billing/checkout', stop_at_checkout)
                page = page_for(ctx)
                page.goto(origin + '/auth/login')  # Real browser OIDC code/PKCE callback.
                page.goto(origin + '/billing')
                page.get_by_role('button', name='Subscribe with Stripe').click()
                sync_api.expect(page.get_by_role('heading', name='Synthetic Checkout')).to_be_visible()
                state['subscriptions'][customer] = [subscription()]
                assert signed_event(app.test_client(), origin, 'evt_' + customer, customer).status_code == 200
                page.goto(origin + '/')
                subscribed(page)
                open_parent(page)
                page.locator('#newKidName').fill(name)
                page.locator('#addKidBtn').click()
                sync_api.expect(page.locator('.dk-head')).to_contain_text(name)
                page.locator('#closeDad').click()
                wallet(page, '$5.00')
                return ctx, page
            a_ctx, a = parent('parent-a', 'cus_1', 'Only A')
            # A second device shares A's authenticated parent session, not B's.
            a2_ctx = context(storage_state=a_ctx.storage_state())
            a2 = page_for(a2_ctx)
            a2.goto(origin + '/')
            subscribed(a2)
            wallet(a2, '$5.00')
            b_ctx, b = parent('parent-b', 'cus_2', 'Only B')
            subscribed(b)
            b_frames = b.evaluate('window.__frames.length')
            open_parent(a)
            a.locator('.gift-amt').fill('2')
            a.locator('.gift-note').fill('Synthetic gift A')
            a.locator('.gift-btn').click()
            wallet(a2, '$7.00')
            assert a2.evaluate("window.__frames.every(f => JSON.stringify(JSON.parse(f)) === '{\"type\":\"state_changed\"}')")
            b.wait_for_timeout(500)
            assert b.evaluate('window.__frames.length') == b_frames
            wallet(b, '$5.00')
            assert b.locator('#kids').inner_text().find('Only A') == -1
            assert a2.locator('#kids').inner_text().find('Only B') == -1
            a2.evaluate('window.__sockets.at(-1).close()')
            a.locator('.gift-amt').fill('1')
            a.locator('.gift-btn').click()
            a2.wait_for_function('window.__sockets.length >= 2 && window.__sockets.at(-1).readyState === 1')
            wallet(a2, '$8.00')  # Reconnect, not the disabled 30-second poll.
            assert b.evaluate('window.__frames.length') == b_frames
            # Unauthenticated browser socket never upgrades (no auto-login here).
            anonymous = context()
            denied = page_for(anonymous)
            denied.goto(origin + '/auth/signed-out')
            result = denied.evaluate("""() => new Promise(resolve => {
              let opened = false;
              const ws = new WebSocket(location.origin.replace('https:', 'wss:') + '/api/updates');
              ws.onopen = () => { opened = true; ws.close(); };
              ws.onclose = () => resolve(opened);
              setTimeout(() => resolve('timeout'), 3000);
            })""")
            assert result is False
            with host.connect() as conn:
                conn.execute("UPDATE families SET paid_until=0, trial_until=0 WHERE subject='parent-a'")
            # Idle sockets close on expiry; the actual UI immediately leaves bank.
            sync_api.expect(a2).to_have_url(origin + '/billing')
            wallet(b, '$5.00')
            assert b.evaluate('window.__sockets.at(-1).readyState') == 1
            assert b.evaluate('window.__frames.length') == b_frames
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
