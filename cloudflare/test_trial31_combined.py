"""Integrated duration/copy/claim contract on disposable real workerd state."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import sqlite3
import time

from playwright.sync_api import sync_playwright, expect

from test_runtime import Runtime, INIT
from test_oauth import Provider, client, csrf, login

DAY = 86400


def dates(path):
    with sqlite3.connect(path) as sql:
        return sql.execute('SELECT trial_started,trial_until,trial_policy,customer FROM _bod_family').fetchone()


def test_legacy_trial_browser_claim_reload_restart_and_sample_maturity(tmp_path):
    provider = Provider()
    runtime = Runtime(tmp_path, {'BOD_OIDC_CLIENT_ID': 'fixture-client',
        'BOD_OIDC_CLIENT_SECRET': 'fixture-secret', 'BOD_TEST_OIDC_ORIGIN': provider.origin})
    runtime.start()
    try:
        with client(runtime) as parent:
            csrf(parent)
            guest = parent.cookies['bod-local-session']
            path = runtime.database('bod-local-session=' + guest)
            start, end, policy, customer = dates(path)
            assert end == start + 31 * DAY and policy == 1 and customer is None
            # Real default/sample investment is 30 days, not changed to fit the trial.
            with sqlite3.connect(path) as sql:
                buy, maturity = sql.execute('SELECT buy_ts,mature_ts FROM bonds').fetchone()
                assert maturity - buy == 30 * DAY
                assert maturity < end
            runtime.stop()
            # Previously persisted seven-day family, including old schema without marker.
            start = time.time() - 10 * DAY
            with sqlite3.connect(path) as sql:
                sql.execute('ALTER TABLE _bod_family DROP COLUMN trial_policy')
                sql.execute('UPDATE _bod_family SET trial_started=?,trial_until=?', (start, start + 7 * DAY))
            runtime.start()
            with sync_playwright() as pw, pw.chromium.launch(executable_path='/usr/bin/chromium') as browser:
                context = browser.new_context(viewport={'width': 390, 'height': 844})
                context.add_init_script(INIT)
                context.add_cookies([{'name': 'bod-local-session', 'value': guest, 'url': runtime.origin}])
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(runtime.origin)
                expect(page.locator('#guestWelcome')).to_be_visible()
                expect(page.locator('#guestWelcome')).to_contain_text('31 days')
                expect(page.locator('#guestWelcome')).to_contain_text('No automatic charge')
                page.locator('#tryGuestBank').click()
                page.locator('#tourSkip').click()
                page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
                open_parent(page)
                expect(page.locator('.dad-account summary')).to_contain_text('21d left')
                page.locator('.dad-account summary').click()
                expect(page.locator('.account-status').filter(has_text='21 days left')).to_have_count(1)
                for route in ('/', '/billing'):
                    page.goto(runtime.origin + route)
                    text = page.locator('body').inner_text()
                    assert 'seven-day trial' not in text and '7-day trial' not in text and 'free week' not in text
                expected = (start, start + 31 * DAY, 1, None)
                assert dates(path) == expected
                page.reload()
                assert dates(path) == expected
                assert not errors
                context.close()
            # Signed loopback provider claims the same original bank, never a fresh trial.
            login(parent, provider, 'combined-trial-parent')
            saved = parent.cookies['bod-local-session']
            assert saved != guest and saved.split('.')[0] == guest.split('.')[0]
            assert parent.get('/api/state').status_code == 200
            assert '21 days left' in parent.get('/billing').text
            assert dates(path) == expected
            runtime.stop()
            runtime.start()
            assert parent.get('/api/state').status_code == 200
            assert dates(path) == expected
            # A fresh browser reopens the saved family without resetting start/expiry.
            with client(runtime) as other:
                login(other, provider, 'combined-trial-parent')
                assert other.cookies['bod-local-session'].split('.')[0] == guest.split('.')[0]
                assert dates(path) == expected
            # Advance the bank's educational clock only, not entitlement wall time.
            token = csrf(parent)
            result = parent.post('/api/admin/fastforward', json={'days': 30},
                                 headers={'Origin': runtime.origin, 'X-CSRF-Token': token, 'X-Dad-Pin': '1234'})
            assert result.status_code == 200
            state = parent.get('/api/state').json()
            assert state['kids'][0]['bonds'][0]['ready'] is True
            assert dates(path) == expected
    finally:
        runtime.stop()
        provider.stop()
