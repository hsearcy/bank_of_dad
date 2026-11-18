"""Real onboarding/Dad modal/expired UI; synthetic providers, never real payments."""
import os
from pathlib import Path
import threading

import pytest
from werkzeug.serving import make_server

from test_hosted import hosted_app
from test_hosted_realtime_browser import INIT
from test_trial_access import family, expire, operator


@pytest.mark.parametrize('width', [390, 768, 1280])
def test_no_card_onboarding_dad_access_expiry_and_restore(hosted_app, width):
    sync_api = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium')
    if not os.path.exists(executable):
        pytest.skip('Set BOD_TEST_CHROMIUM to Chromium')
    app, host, state, _ = hosted_app
    # This is deliberately a labeled example destination, not the owner's email.
    host.support_url = 'https://support.example.test/contact' if width != 1280 else None
    server = make_server('127.0.0.1', 0, app, threaded=True, ssl_context='adhoc')
    origin = 'https://localhost:' + str(server.server_port)
    host.origin = origin
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
    def capture(page, name):
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(artifacts) / f'{name}-{width}.png'), full_page=True)
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
            context = browser.new_context(ignore_https_errors=True, viewport={'width': width, 'height': 920})
            context.add_init_script(INIT)  # No polling may masquerade as expiry handling.
            context.route('**/favicon.ico', lambda route: route.fulfill(status=204))
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/')  # Actual Authlib/PKCE signup through fixture HTTP.
            sync_api.expect(page.locator('#dadBtn')).to_be_visible()
            assert page.url == origin + '/'
            assert state['customers'] == state['checkouts'] == []
            assert page.locator('.topbar a').count() == 0
            page.locator('#dadBtn').click()
            sync_api.expect(page.locator('.dad-account summary')).to_contain_text('Free trial · 7d left')
            assert not page.locator('.dad-account').evaluate('(el) => el.open')
            page.locator('#newKidName').fill('Trial Nickname')
            page.locator('#addKidBtn').click()
            sync_api.expect(page.locator('.dk-head')).to_contain_text('Trial Nickname')
            page.locator('.dad-account summary').click()
            sync_api.expect(page.get_by_role('button', name='Subscribe with Stripe')).to_be_visible()
            sync_api.expect(page.get_by_text('No card needed', exact=False)).to_be_visible()
            assert page.locator('#dadPanel .panel-card').evaluate('(el) => el.scrollWidth <= el.clientWidth')
            capture(page, 'dad-trial')
            page.locator('#closeDad').click()
            sync_api.expect(page.locator('#kids .big')).to_have_text('$5.00')
            page.wait_for_function('window.__sockets.some(ws => ws.readyState === 1)')
            capture(page, 'onboarding')
            row = family(host)
            expire(host)
            sync_api.expect(page).to_have_url(origin + '/billing')  # Idle WSS revocation, poll disabled.
            sync_api.expect(page.get_by_text('Access paused', exact=True)).to_be_visible()
            sync_api.expect(page.get_by_role('button', name='Subscribe with Stripe')).to_be_visible()
            sync_api.expect(page.locator('.account-price')).to_contain_text('$9.99 USD per year')
            sync_api.expect(page.get_by_text('Your family’s bank data is still here.', exact=False)).to_be_visible()
            if host.support_url:
                sync_api.expect(page.get_by_role('link', name='Contact about free access')).to_have_attribute('href', host.support_url)
            else:
                assert page.get_by_role('link', name='Contact about free access').count() == 0
                sync_api.expect(page.get_by_text('A contact link hasn’t been set up yet.', exact=False)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            capture(page, 'expired')
            operator(host, 'grant', row['id'], '--reason', 'Synthetic browser grant')
            page.reload()
            sync_api.expect(page.get_by_text('Free access', exact=True)).to_be_visible()
            page.get_by_role('link', name='Open your family bank').click()
            sync_api.expect(page.locator('#kids .name')).to_have_text('Trial Nickname')
            sync_api.expect(page.locator('#kids .big')).to_have_text('$5.00')
            page.locator('#dadBtn').click()
            page.locator('.dad-account summary').click()
            sync_api.expect(page.locator('.access-badge')).to_contain_text('Free access')
            assert page.get_by_role('button', name='Subscribe with Stripe').count() == 0
            page.get_by_role('button', name='Sign out of Bank of Dad').click()
            sync_api.expect(page.get_by_text('You are signed out of Bank of Dad.')).to_be_visible()
            assert context.request.get(origin + '/api/state').status == 401
            assert not state['customers'] and not state['checkouts']
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
