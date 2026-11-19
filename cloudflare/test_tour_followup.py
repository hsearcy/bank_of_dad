"""Actual tour controls on disposable workerd families, never remote providers."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import sys
from pathlib import Path
import sqlite3
import pytest

from test_runtime import runtime, snapshot, state

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from tour_browser_flow import save_and_finish_tour


@pytest.mark.parametrize('automatic', [False, True])
def test_save_tour_real_workerd_mobile_desktop(runtime, automatic):
    from playwright.sync_api import sync_playwright
    sibling, _ = runtime.guest()
    sibling_before = snapshot(runtime.database(sibling))
    with sync_playwright() as pw, pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox']) as browser:
        for width in (390, 1280):
            cookie, _ = runtime.guest()
            path = runtime.database(cookie)
            before = state(runtime, cookie)['kids'][0]
            with sqlite3.connect(path) as conn:
                tx_before = conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
            context = browser.new_context(viewport={'width': width, 'height': 667}, reduced_motion='reduce', service_workers='block')
            name, value = cookie.split('=', 1)
            context.add_cookies([{'name': name, 'value': value, 'url': runtime.origin}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(runtime.origin)
            page.locator('#tryGuestBank').click()
            page.locator('#tourSkip').click()
            open_parent(page)
            page.locator('#editParentName').click()
            page.locator('#parentLabel').fill('Mom')
            page.locator('#parentLabelForm button[type="submit"]').click()
            from playwright.sync_api import expect
            expect(page.locator('#bankTitle')).to_have_text('🏦 Bank of Mom')
            page.reload()
            expect(page.locator('#bankTitle')).to_have_text('🏦 Bank of Mom')
            expect(page.locator('#dadBtn')).to_have_attribute('title', 'Mom controls')
            expect(page.locator('.wallet-value')).to_have_text('$5.00')
            expect(page.locator('.total .big')).to_have_text('$7.00')
            expect(page.locator('.pin-card')).not_to_be_visible()
            open_parent(page)
            if automatic:
                page.get_by_role('checkbox', name='Automatically collect savings interest for every child').check()
                page.locator('#saveRates').click()
                page.wait_for_function('STATE.config?.auto_interest_enabled === true')
                enabled = state(runtime, cookie)['kids'][0]
                assert enabled['savings_cents'] == before['savings_cents'] + before['pending_interest_cents']
                assert enabled['wallet_cents'] == before['wallet_cents']
            save_and_finish_tour(page, width)
            after = state(runtime, cookie)['kids'][0]
            # Saving also capitalizes genuinely accrued sample interest.
            assert after['owned_cents'] == before['owned_cents'] + before['pending_interest_cents']
            assert after['wallet_cents'] == before['wallet_cents'] - 100
            assert after['savings_cents'] == before['savings_cents'] + 100 + before['pending_interest_cents']
            with sqlite3.connect(path) as conn:
                # Manual saving now audits its capitalized interest separately;
                # automatic mode audits the same credit when the setting enables.
                assert conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == tx_before + 2
                assert conn.execute("SELECT kind,amount_cents FROM transactions ORDER BY id DESC LIMIT 2").fetchall() == [('save', 100), ('interest', before['pending_interest_cents'])]
                assert conn.execute('SELECT kind,amount_cents FROM transactions ORDER BY id DESC LIMIT 1').fetchone() == ('save', 100)
            assert all('Failed to fetch' in error for error in errors), errors
            if automatic:
                csrf = page.locator('meta[name="csrf-token"]').get_attribute('content')
                assert runtime.parent_call('/api/admin/fastforward', cookie=cookie, csrf=csrf, body={'days':7})[1]['ok']
                due = state(runtime, cookie)['kids'][0]
                assert due['savings_cents'] > after['savings_cents']
                assert due['wallet_cents'] == after['wallet_cents']
                assert runtime.call('/api/collect', cookie=cookie, csrf=csrf, body={'kid_id':1,'kind':'interest'})[1]['collected_cents'] == 0
                page.reload()
                page.wait_for_function('STATE.config?.auto_interest_enabled === true')
            open_parent(page)
            page.locator('.dad-account summary').click()
            page.locator('.account-signout button').click()
            page.wait_for_url('**/auth/signed-out')
            assert runtime.call('/api/state', cookie=cookie)[0] == 401
            context.close()
    assert snapshot(runtime.database(sibling)) == sibling_before
