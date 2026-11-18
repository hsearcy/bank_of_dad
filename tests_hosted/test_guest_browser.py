"""Real frontend and WSS, real OIDC callback, disposable providers and banks."""
import os
from pathlib import Path
import threading

import pytest
from werkzeug.serving import make_server

from test_hosted import hosted_app
from test_hosted_realtime_browser import INIT
from test_guest import snapshot


@pytest.fixture
def browser_bank(hosted_app):
    sync_api = pytest.importorskip('playwright.sync_api')
    executable = os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium')
    if not os.path.exists(executable):
        pytest.skip('Set BOD_TEST_CHROMIUM to Chromium')
    app, host, state, _ = hosted_app
    host.support_url = 'https://support.example.test/contact'
    server = make_server('127.0.0.1', 0, app, threaded=True, ssl_context='adhoc')
    origin = 'https://localhost:' + str(server.server_port)
    host.origin = origin
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
            yield browser, host, state, origin, sync_api
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def context(browser, width):
    ctx = browser.new_context(ignore_https_errors=True, viewport={'width': width, 'height': 920})
    ctx.add_init_script(INIT)  # Disable polling; live updates must actually use WSS.
    ctx.route('**/favicon.ico', lambda route: route.fulfill(status=204))
    return ctx


def capture(page, name, width):
    artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(artifacts) / f'guest-{name}-{width}.png'), full_page=True)


@pytest.mark.parametrize('width', [390, 1280])
def test_dashboard_edit_reload_signup_all_data_sync_and_collision(browser_bank, width):
    browser, host, state, origin, api = browser_bank
    ctx = context(browser, width)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/')
    api.expect(page.locator('#guestWelcome')).to_be_visible()
    api.expect(page.locator('#kids .name')).to_have_text('Sample Kid')
    api.expect(page.locator('#kids .big')).to_have_text('$7.00')
    assert page.url == origin + '/'
    assert state['token_calls'] == 0 and not state['customers']
    assert page.locator('#guestWelcome').evaluate('(el) => el.scrollWidth <= el.clientWidth')
    assert page.locator('#guestWelcome a').last.get_attribute('href') == 'https://github.com/hsearcy/bank_of_dad'
    with host.connect() as conn:
        family = conn.execute('SELECT id, trial_until FROM families').fetchone()
    kid = ctx.request.get(origin + '/api/state').json()['kids'][0]
    assert kid['wallet_cents'] == 500 and kid['savings_cents'] == 100
    assert snapshot(host, family['id'])['bonds'][0][3] == 100
    capture(page, 'welcome', width)
    page.locator('#tryGuestBank').click()
    api.expect(page.locator('#guestWelcome')).not_to_be_visible()
    page.locator('.btn-spend').click()
    page.locator('.spend-reason').fill('A real trial snack')
    page.locator('.spend-amt').fill('1')
    page.locator('#modalCard .modal-go').click()
    api.expect(page.locator('#kids .big')).to_have_text('$6.00')
    page.locator('.btn-save').click()
    page.locator('.custom-amt').fill('1')
    page.locator('[data-act="save"]').click()
    api.expect(page.locator('#modal')).to_have_class('panel hidden')
    page.locator('#dadBtn').click()
    page.locator('#newKidName').fill('My real kid')
    page.locator('#addKidBtn').click()
    api.expect(page.locator('.dk-head').last).to_contain_text('My real kid')
    page.locator('#closeDad').click()
    page.reload()
    api.expect(page.locator('#kids .name').last).to_have_text('My real kid')
    assert page.locator('#guestWelcome').count() == 0
    # Two ordinary tabs receive the actual guest channel's committed changes.
    mirror = ctx.new_page()
    mirror.goto(origin + '/')
    mirror.wait_for_function('window.__sockets.some(ws => ws.readyState === 1)')
    page.locator('#dadBtn').click()
    page.locator('.gift-amt').first.fill('2')
    page.locator('.gift-note').first.fill('Trial gift')
    page.locator('.gift-btn').first.click()
    api.expect(mirror.locator('#kids .big').first).to_have_text('$8.00')
    mirror.close()
    before = snapshot(host, family['id'])
    capture(page, 'dad', width)
    page.locator('.dad-account summary').click()
    page.get_by_role('link', name='Save this bank / sign in').click()
    api.expect(page.locator('#kids .name').last).to_have_text('My real kid')
    assert snapshot(host, family['id']) == before
    with host.connect() as conn:
        claimed = conn.execute('SELECT * FROM families WHERE id=?', (family['id'],)).fetchone()
        assert claimed['guest_hash'] is None and claimed['trial_until'] == family['trial_until']
    assert not state['customers'] and not state['checkouts']
    # A new browser starts a different real anonymous bank, not the claimed one.
    other_ctx = context(browser, width)
    other = other_ctx.new_page()
    other.goto(origin + '/')
    api.expect(other.locator('#guestWelcome')).to_be_visible()
    with host.connect() as conn:
        second = conn.execute('SELECT id FROM families WHERE guest_hash IS NOT NULL').fetchone()['id']
    assert second != family['id']
    second_before = snapshot(host, second)
    other.locator('#guestWelcome .welcome-signup').click()
    api.expect(other.get_by_role('heading', name='You already have a family bank')).to_be_visible()
    assert snapshot(host, second) == second_before and snapshot(host, family['id']) == before
    assert other.evaluate('document.documentElement.scrollWidth <= innerWidth')
    capture(other, 'collision', width)
    other.get_by_role('button', name='Open my existing family bank').click()
    api.expect(other.locator('#kids .name').last).to_have_text('My real kid')
    other.locator('#dadBtn').click()
    other.locator('.dad-account summary').click()
    other.get_by_role('button', name='Return to this device’s trial').click()
    api.expect(other.locator('#guestWelcome')).to_be_visible()
    assert snapshot(host, second) == second_before
    other_ctx.close()
    # Original claimed bank expires while idle, with polling disabled.
    page.wait_for_function('window.__sockets.some(ws => ws.readyState === 1)')
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=0 WHERE id=?', (family['id'],))
    api.expect(page).to_have_url(origin + '/billing')
    api.expect(page.get_by_role('button', name='Subscribe with Stripe')).to_be_visible()
    api.expect(page.get_by_role('link', name='Contact about free access')).to_have_attribute('href', host.support_url)
    capture(page, 'claimed-expired', width)
    assert errors == []
    ctx.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_guest_idle_expiry_claim_then_subscription_and_browser_loss(browser_bank, width):
    browser, host, state, origin, api = browser_bank
    ctx = context(browser, width)
    page = ctx.new_page()
    page.goto(origin + '/')
    api.expect(page.locator('#guestWelcome')).to_be_visible()
    page.locator('#tryGuestBank').click()
    page.wait_for_function('window.__sockets.some(ws => ws.readyState === 1)')
    with host.connect() as conn:
        family = conn.execute('SELECT * FROM families').fetchone()
        conn.execute('UPDATE families SET trial_until=0 WHERE id=?', (family['id'],))
    before = snapshot(host, family['id'])
    api.expect(page).to_have_url(origin + '/billing')
    api.expect(page.get_by_role('link', name='Save this bank / sign in')).to_be_visible()
    api.expect(page.get_by_role('link', name='Contact about free access')).to_be_visible()
    assert page.get_by_role('button', name='Subscribe with Stripe').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    capture(page, 'anonymous-expired', width)
    page.get_by_role('link', name='Save this bank / sign in').click()
    api.expect(page.get_by_role('button', name='Subscribe with Stripe')).to_be_visible()
    assert snapshot(host, family['id']) == before
    with host.connect() as conn:
        row = conn.execute('SELECT * FROM families WHERE id=?', (family['id'],)).fetchone()
        assert row['trial_until'] == 0 and row['guest_hash'] is None
    # Subscription enters the existing real SDK path, stopped before external Stripe.
    def checkout(route):
        response = route.fetch(max_redirects=0)
        assert response.status == 303
        assert response.headers['location'] == 'https://checkout.stripe.com/c/pay/synthetic'
        route.fulfill(status=200, content_type='text/html', body='<h1>Synthetic Checkout</h1>')
    ctx.route('**/billing/checkout', checkout)
    page.get_by_role('button', name='Subscribe with Stripe').click()
    api.expect(page.get_by_role('heading', name='Synthetic Checkout')).to_be_visible()
    assert len(state['customers']) == len(state['checkouts']) == 1
    # Clearing browser data cannot expose the former bank in the next visit.
    ctx.clear_cookies()
    page.goto(origin + '/')
    api.expect(page.locator('#guestWelcome')).to_be_visible()
    with host.connect() as conn:
        assert conn.execute('SELECT count(*) FROM families').fetchone()[0] == 2
    ctx.close()
