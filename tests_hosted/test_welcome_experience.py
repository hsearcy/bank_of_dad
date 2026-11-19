"""First-visit experience on disposable banks and real Chromium only."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import sqlite3
from pathlib import Path

import pytest

from test_hosted import hosted_app, call, login, post
from test_guest import current, snapshot
from test_guest_browser import browser_bank, context, capture


def test_sample_interest_is_real_persisted_and_only_new_guest_banks(hosted_app):
    app, host, providers, origin = hosted_app
    owner = app.test_client()
    login(owner, providers, origin)
    post(owner, '/api/admin/kid', origin, json={'name': 'Existing family'})
    existing_id = current(owner, origin)['family']
    existing = snapshot(host, existing_id)
    guest = app.test_client()
    call(guest, '/', origin)
    family = current(guest, origin)['family']
    before = call(guest, '/api/state', origin).json['kids'][0]
    assert before['pending_interest_cents'] == 1
    assert before['pending_allowance_cents'] == before['pending_bonds_cents'] == 0
    with sqlite3.connect(host.family_path(family)) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='time_offset'").fetchone() is None
        assert conn.execute("SELECT value FROM settings WHERE key='savings_weekly_pct'").fetchone() is None
        kid = conn.execute('SELECT created_ts, savings_accrued_ts FROM kids').fetchone()
        assert kid[0] <= kid[1]
        assert conn.execute("SELECT MIN(ts) FROM transactions WHERE kind='save'").fetchone()[0] == kid[1]
    result = post(guest, '/api/collect', origin, json={'kid_id': before['id'], 'kind': 'interest'})
    assert result.json['ok']
    call(guest, '/', origin)
    after = call(guest, '/api/state', origin).json['kids'][0]
    assert after['savings_cents'] == 101 and after['owned_cents'] == 701
    assert after['pending_interest_cents'] == 0
    saved = snapshot(host, family)
    assert [t[4] for t in saved['transactions'] if t[3] == 'interest'] == [1]
    assert saved['history'][-1][3] == 701
    assert snapshot(host, existing_id) == existing
    # Loading an old guest never reseeds or resets its accrual anchor.
    call(guest, '/', origin)
    assert snapshot(host, family) == saved
    assert not providers['customers'] and not providers['checkouts']


@pytest.mark.parametrize('rate', [0, 0.00001])
def test_custom_sample_rates_do_not_change_clock_or_create_other_rewards(hosted_app, monkeypatch, rate):
    import config
    monkeypatch.setattr(config, 'SAVINGS_WEEKLY_RATE', rate)
    app, host, providers, origin = hosted_app
    guest = app.test_client()
    call(guest, '/', origin)
    kid = call(guest, '/api/state', origin).json['kids'][0]
    assert kid['pending_cents'] == 0
    assert kid['bonds'][0]['days_left'] >= 24
    assert kid['next_allowance_ts'] > __import__('time').time() + 13 * 86400


@pytest.mark.parametrize('width', [390, 1280])
def test_welcome_tour_real_targets_claim_reload_and_keyboard(browser_bank, width):
    browser, host, providers, origin, api = browser_bank
    ctx = context(browser, width)

    page = ctx.new_page()
    page.set_viewport_size({'width': width, 'height': 844})
    page.emulate_media(reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin)
    welcome = page.locator('#guestWelcome')
    api.expect(welcome).to_be_visible()
    api.expect(welcome).to_contain_text('keep the lights on')
    api.expect(welcome).to_contain_text('31 days')
    api.expect(welcome).to_contain_text('No automatic charge')
    api.expect(welcome).to_contain_text('not required')
    api.expect(welcome).to_contain_text('my kids')
    with host.connect() as conn:
        family = conn.execute('SELECT id FROM families').fetchone()['id']
    before = snapshot(host, family)
    capture(page, 'warm-welcome', width)
    page.locator('#tryGuestBank').click()
    api.expect(page.locator('#productTour')).to_be_visible()
    page.get_by_role('button', name='Show me around').click()
    for selector in ['#dadBtn', '.reward-btn[data-kind="interest"]', '.btn-save', '.btn-bond', '.wallet-actions']:
        target = page.locator(selector).first
        # Real polling/live reads rebuild the cards; highlights must follow them.
        page.evaluate('backgroundRefresh()')
        api.expect(target).to_have_class(__import__('re').compile('tour-target'))
        assert target.evaluate('(el) => { const r=el.getBoundingClientRect(); const p=document.querySelector("#productTour").getBoundingClientRect(); return r.top >= 0 && r.bottom <= p.top - 4; }')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        capture(page, 'tour-' + selector.replace('#', '').split('[')[0].replace('.', ''), width)
        page.locator('#tourNext').focus()
        page.keyboard.press('Enter')
    api.expect(page.locator('#tourTitle')).to_contain_text('A few things to know')
    page.locator('#tourNext').click()
    api.expect(page.locator('#productTour')).not_to_be_visible()
    assert snapshot(host, family) == before  # A tour never collects or moves money.
    assert page.locator('#dadBtn').get_attribute('aria-describedby') is None
    page.locator('.reward-btn[data-kind="interest"]').click(force=True)
    api.expect(page.locator('#kids .big')).to_have_text('$7.01')
    page.reload()
    api.expect(page.locator('.s-val')).to_have_text('$1.01')
    assert page.locator('.reward-btn[data-kind="interest"]').count() == 0
    assert page.locator('#guestWelcome').count() == 0
    assert snapshot(host, family)['history'][-1][3] == 701
    open_parent(page)
    page.locator('#replayTour').click()
    page.keyboard.press('Escape')
    api.expect(page.locator('#productTour')).not_to_be_visible()
    assert page.evaluate('document.activeElement.id') == 'dadBtn'
    assert page.locator('.tour-target').count() == 0
    open_parent(page)
    api.expect(page.locator('#keepAwake')).not_to_be_checked()
    assert errors == []
    ctx.close()


def test_short_phone_welcome_starts_at_heading_with_accessible_focus(browser_bank):
    browser, host, providers, origin, api = browser_bank
    ctx = context(browser, 390)
    page = ctx.new_page()
    page.set_viewport_size({'width': 390, 'height': 667})
    page.goto(origin)
    welcome = page.locator('#guestWelcome')
    heading = page.get_by_role('heading', name='Welcome to Bank of Dad')
    api.expect(welcome).to_be_visible()
    assert welcome.evaluate('(el) => el.scrollHeight > el.clientHeight')
    assert welcome.evaluate('(el) => el.scrollTop') == 0
    api.expect(heading).to_be_focused()
    assert heading.evaluate('(el) => { const r=el.getBoundingClientRect(); const d=el.closest("dialog").getBoundingClientRect(); return r.top >= d.top && r.bottom <= d.bottom && r.bottom <= innerHeight; }')
    assert page.locator('#guestWelcome .welcome-icon').evaluate('(el) => el.getBoundingClientRect().top >= el.closest("dialog").getBoundingClientRect().top')
    # Tabbing still reaches the dialog's links and CTA, not the bank underneath.
    page.keyboard.press('Tab')
    api.expect(page.locator('#guestWelcome a').first).to_be_focused()
    page.locator('#tryGuestBank').focus()
    page.keyboard.press('Enter')
    api.expect(welcome).not_to_be_visible()
    api.expect(page.locator('#productTour')).to_be_visible()
    ctx.close()


@pytest.mark.parametrize('width', [390, 1280])
@pytest.mark.parametrize('controlled', [False, True])
def test_hosted_awake_control_and_skip_offer(browser_bank, width, controlled):
    browser, host, providers, origin, api = browser_bank
    ctx = context(browser, width)
    if controlled:
        ctx.add_init_script((Path(__file__).resolve().parents[1] / 'tests/fixtures/wake_lock.js').read_text())
    page = ctx.new_page()
    page.goto(origin)
    page.locator('#tryGuestBank').click()
    page.get_by_role('button', name='Skip tour').click()
    api.expect(page.locator('#productTour')).not_to_be_visible()
    open_parent(page)
    toggle = page.get_by_role('switch', name='Keep screen awake')
    api.expect(toggle).not_to_be_checked()
    with host.connect() as conn:
        family = conn.execute('SELECT id FROM families').fetchone()['id']
    before = snapshot(host, family)
    if controlled or page.evaluate('!!navigator.wakeLock?.request'):
        toggle.focus()
        page.keyboard.press('Space')
        api.expect(page.locator('#awakeStatus')).to_have_text('')
        api.expect(page.locator('#awakeStatus')).not_to_be_visible()
        print(f'Hosted Chromium wake lock ({width}px, controlled={controlled}): ' + page.locator('#awakeStatus').inner_text())
        page.locator('#closeDad').click()  # Keep working after closing controls.
        if controlled:
            assert page.evaluate('__wake.locks.filter(lock => !lock.released).length') == 1
        open_parent(page)
        toggle.uncheck()
        api.expect(page.locator('#awakeStatus')).to_have_text('')
        api.expect(page.locator('#awakeStatus')).not_to_be_visible()
    else:
        api.expect(toggle).to_be_disabled()
    assert snapshot(host, family) == before
    page.reload()
    open_parent(page)
    api.expect(toggle).not_to_be_checked()
    api.expect(page.locator('#productTour')).not_to_be_visible()
    assert not providers['customers'] and not providers['checkouts']
    ctx.close()
