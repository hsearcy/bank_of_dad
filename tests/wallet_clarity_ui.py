"""Real dashboard acceptance shared by self-hosted and local workerd tests."""
import os
from pathlib import Path
import re

from playwright.sync_api import expect


INIT = """(() => {
  window.__sockets = [];
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) { super(...args); window.__sockets.push(this); }
  };
  const interval = window.setInterval;
  window.setInterval = (fn, ms, ...args) => ms === 30000 ? 0 : interval(fn, ms, ...args);
})();"""


def money(cents):
    return f'${cents / 100:.2f}'


def exercise_wallet(a, b, post, state, prefix):
    """Use only real API responses and two independent Chromium processes."""
    kid = state()['kids'][0]['id']
    def action(path, **body):
        result = post(path, {'kid_id': kid, **body})
        assert result['ok'], result

    # Build deliberately different values through real ledger operations.
    action('/api/save', amount_cents=100)
    action('/api/bond', amount_cents=200, term='1mo')
    action('/api/admin/gift', amount_cents=734)
    expected = state()['kids'][0]
    assert (expected['wallet_cents'], expected['savings_cents'], expected['invest_cents'], expected['owned_cents']) == (934, 100, 200, 1234)

    def check(page, wallet, total):
        expect(page.locator('.wallet-value').first).to_have_text(money(wallet))
        expect(page.locator('.total .big').first).to_have_text(money(total))
        assert not re.search(r'\bcoins?\b', page.locator('body').inner_text(), re.I)
        assert page.locator('.wallet .btn-spend').count() == page.locator('#kids .card').count()
        assert page.locator('.wallet-actions button').count() == 4 * page.locator('#kids .card').count()

    shots = os.environ.get('BOD_WALLET_SCREENSHOTS')
    for page, width, label in ((a, 390, 'phone'), (b, 1440, 'desktop')):
        page.set_viewport_size({'width': width, 'height': 900})
        check(page, 934, 1234)
        expect(page.locator('.wallet').first).to_have_accessible_name("Synthetic's wallet")
        expect(page.get_by_role('button', name="Spend from Synthetic's wallet")).to_be_visible()
        assert page.locator('.wallet-value').first.evaluate('(e) => parseFloat(getComputedStyle(e).fontSize)') >= 30
        assert page.locator('.total .big').first.evaluate('(e) => parseFloat(getComputedStyle(e).fontSize)') > 30
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('.btn-spend').first.bounding_box()['height'] >= 44
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / f'{prefix}-{label}.png'), full_page=True)

    a.locator('.btn-save').first.click()
    expect(a.locator('#modalCard')).to_contain_text('Your wallet has $9.34 available to save.')
    a.locator('.custom-amt').fill('1')
    a.locator('[data-act="save"]').click()
    for page in (a, b):
        check(page, 834, 1234)
    a.locator('.btn-spend').first.click()
    expect(a.locator('#modalCard')).to_contain_text('Spend from wallet')
    a.locator('.spend-amt').fill('0.34')
    a.locator('.spend-reason').fill('Synthetic snack')
    a.locator('#modal .modal-go').click()
    for page in (a, b):
        check(page, 800, 1200)

    # Render/range changes must never substitute total for wallet.
    a.locator('.rng[data-rng="1m"]').first.click()
    check(a, 800, 1200)
    action('/api/withdraw', amount_cents=100)
    for page in (a, b):
        check(page, 900, 1200)
    action('/api/admin/kid/update', name='Alexandertheverylongname')
    for page in (a, b):
        expect(page.locator('.wallet').first).to_have_accessible_name("Alexandertheverylongname's wallet")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

    # No polling: a replacement WebSocket must reconcile the missed write.
    reconnect_errors = []
    def reconnect_console(message):
        if message.type == 'error':
            reconnect_errors.append(message.text)
    b.on('console', reconnect_console)
    b.evaluate('window.__sockets.at(-1).close(1000)')
    action('/api/admin/gift', amount_cents=25)
    check(b, 925, 1225)
    b.wait_for_function('window.__sockets.length >= 2 && window.__sockets.at(-1).readyState === 1')
    b.remove_listener('console', reconnect_console)

    action('/api/admin/fastforward', days=30)
    expect(a.locator('.reward-btn[data-kind="allowance"]').first).to_be_visible()
    check(a, 925, 1225)  # Uncollected income is not available to spend.
    before = state()['kids'][0]
    a.locator('.reward-btn[data-kind="allowance"]').first.click(force=True)
    after_wallet = before['wallet_cents'] + before['pending_allowance_cents']
    after_total = before['owned_cents'] + before['pending_allowance_cents']
    for page in (a, b):
        check(page, after_wallet, after_total)
    assert a.locator('#confetti .confetti-bit').count() > 0

    # Invalid requests do not make the spendable number negative.
    assert not post('/api/spend', {'kid_id': kid, 'amount_cents': -100})['ok']
    assert state()['kids'][0]['wallet_cents'] == after_wallet
    # Existing domain behavior caps overspending at the wallet (not rejection).
    action('/api/spend', amount_cents=after_wallet + 1)
    remaining = state()['kids'][0]['owned_cents']
    for page in (a, b):
        check(page, 0, remaining)
    a.locator('.btn-bond').first.click()
    expect(a.locator('#modalCard')).to_contain_text('Not enough money in your wallet yet')
    assert not re.search(r'\bcoins?\b', a.locator('#modalCard').inner_text(), re.I)
    a.locator('#modal .close').click()

    # An empty bank has no phantom wallet, and is not silently reseeded.
    action('/api/admin/kid/remove', confirm_name='Alexandertheverylongname')
    for page in (a, b):
        expect(page.locator('.wallet-value')).to_have_count(0)
        expect(page.locator('#kids')).to_contain_text('add your first kid')
    return reconnect_errors
