"""Actual Chromium UI against the real app on disposable loopback HTTPS.

Identity and Stripe endpoints remain the explicitly synthetic HTTP fixtures.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import os
import threading

import pytest
from werkzeug.serving import make_server

from test_hosted import hosted_app, signed_event, subscription


def test_browser_parent_to_family_bank_and_signout(hosted_app):
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
            browser = p.chromium.launch(executable_path=executable, headless=True,
                                        args=['--no-sandbox'])
            context = browser.new_context(ignore_https_errors=True)
            # This is not a real Stripe payment. Assert actual Checkout SDK calls in
            # the backend fixture, and stop the browser at a labeled synthetic page.
            def stop_at_checkout(route):
                response = route.fetch(max_redirects=0)
                assert response.status == 303
                assert response.headers['location'] == 'https://checkout.stripe.com/c/pay/synthetic'
                route.fulfill(status=200, content_type='text/html',
                              body='<h1>Synthetic Stripe Checkout fixture</h1>')
            context.route('**/billing/checkout', stop_at_checkout)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/auth/login')
            sync_api.expect(page.locator('#dadBtn')).to_be_visible()
            open_parent(page)
            page.locator('.dad-account summary').click()
            sync_api.expect(page.locator('.access-badge')).to_contain_text('Free trial')
            page.get_by_role('button', name='Subscribe with Stripe').click()
            sync_api.expect(page.get_by_role('heading', name='Synthetic Stripe Checkout fixture')).to_be_visible()
            assert len(state['checkouts']) == 1
            assert state['checkouts'][0]['customer'] == 'cus_1'
            state['subscriptions']['cus_1'] = [subscription()]
            assert signed_event(app.test_client(), origin).status_code == 200
            page.goto(origin + '/billing')
            page.get_by_role('link', name='Open your family bank').click()
            sync_api.expect(page.locator('#dadBtn')).to_be_visible()
            assert page.locator('meta[name="csrf-token"]').count() == 1
            # Hosted identity still requires the additional shared-device PIN.
            # A bare Dad click leaves #newKidName hidden behind that real gate.
            open_parent(page)
            page.locator('#newKidName').fill('Browser Nickname')
            page.locator('#addKidBtn').click()
            sync_api.expect(page.locator('.dk-head')).to_contain_text('Browser Nickname')
            page.locator('.gift-amt').fill('2')
            page.locator('.gift-note').fill('Synthetic chores')
            page.locator('.gift-btn').click()
            sync_api.expect(page.locator('.dk-head')).to_contain_text('$7.00')
            page.locator('#closeDad').click()
            page.locator('.btn-save').click()
            page.locator('#modalCard .custom-amt').fill('1')
            page.locator('[data-act="save"]').click()
            sync_api.expect(page.locator('#modal')).to_have_class('panel hidden')
            snapshot = page.evaluate("async () => (await fetch('/api/state')).json()")
            assert snapshot['kids'][0]['wallet_cents'] == 600
            assert snapshot['kids'][0]['savings_cents'] == 100
            other = browser.new_context(ignore_https_errors=True)
            denied = other.request.get(origin + '/api/state')
            assert denied.status == 401
            open_parent(page)
            page.locator('.dad-account summary').click()
            page.get_by_role('button', name='Sign out of Bank of Dad').click()
            sync_api.expect(page.get_by_text('You are signed out of Bank of Dad.')).to_be_visible()
            assert context.request.get(origin + '/api/state').status == 401
            assert not errors
            other.close()
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join()
