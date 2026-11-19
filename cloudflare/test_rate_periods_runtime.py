"""Editable terms in actual workerd/DO SQLite and the real phone UI; no providers."""
import sqlite3
import time

from test_runtime import runtime, state, snapshot, INIT  # shared isolated harness


def bank_rows(path):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return {table: [dict(r) for r in conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in ('kids', 'bonds', 'transactions', 'history', 'settings', 'sqlite_sequence')}


def test_legacy_migration_noop_validation_financial_invariants_and_rollback(runtime):
    cookie, csrf = runtime.guest()
    other, _ = runtime.guest()
    untouched = state(runtime, other)
    runtime.stop()
    path = runtime.database(cookie)
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE kids DROP COLUMN savings_interest_carry')
        conn.execute('UPDATE kids SET savings_cents=10000, savings_accrued_ts=?, allowance_enabled=0',
                     (time.time() - 3.25 * 86400,))
    legacy = bank_rows(path)
    runtime.start()
    before = state(runtime, cookie)
    migrated = bank_rows(path)
    assert migrated['kids'][0].pop('savings_interest_carry') == 0
    assert migrated == legacy
    original = bank_rows(path)
    cfg = before['config']
    no_op = dict(savings_weekly_pct=cfg['savings_weekly_pct'], savings_period_days=cfg['savings_period_days'],
                 returns={b['key']: b['return_pct'] for b in cfg['bonds']},
                 bond_period_days={b['key']: b['days'] for b in cfg['bonds']})
    assert runtime.call('/api/admin/rates', cookie=cookie, csrf=csrf, body=no_op)[1]['ok']
    assert bank_rows(path) == original
    for payload in ({'savings_period_days': 0}, {'savings_period_days': 3651},
                    {'bond_period_days': {'1mo': -1}}, {'bond_period_days': {'3mo': 1.5}},
                    {'savings_weekly_pct': -1}, {'returns': []}):
        assert runtime.call('/api/admin/rates', cookie=cookie, csrf=csrf, body=payload)[0] == 400
        assert bank_rows(path) == original
    for kwargs in ({}, {'csrf': csrf, 'origin': 'https://foreign.invalid'}):
        assert runtime.call('/api/admin/rates', cookie=cookie, body={'savings_period_days': 1}, **kwargs)[0] == 403
        assert bank_rows(path) == original
    payload = dict(savings_weekly_pct=2.123456789, savings_period_days=2,
                   returns={'1mo': 50}, bond_period_days={'1mo': 3, '3mo': 3650})
    assert runtime.call('/api/admin/rates', cookie=cookie, csrf=csrf, body=payload)[1]['ok']
    after = state(runtime, cookie)
    for key in ('wallet_cents', 'savings_cents', 'owned_cents', 'pending_interest_cents'):
        assert after['kids'][0][key] == before['kids'][0][key]
    changed = bank_rows(path)
    assert changed['bonds'] == original['bonds']
    assert changed['transactions'] == original['transactions']
    assert changed['history'] == original['history']
    assert changed['kids'][0]['savings_interest_carry'] > 185
    assert after['config']['savings_weekly_pct'] == 2.123456789
    assert after['config']['savings_period_days'] == 2
    assert state(runtime, other)['config'] == untouched['config']
    assert runtime.call('/api/bond', cookie=cookie, csrf=csrf, body={'kid_id': 1, 'term': '1mo', 'amount_cents': 100})[1]['ok']
    bonds = bank_rows(path)['bonds']
    assert bonds[-1]['mature_ts'] - bonds[-1]['buy_ts'] == 3 * 86400
    assert bonds[-1]['payout_cents'] == 150
    assert bonds[0] == original['bonds'][0]
    assert {b['term'] for b in state(runtime, cookie)['kids'][0]['bonds']} == {'1 Month', '3 Days'}
    # A real settings failure happens after accrual-boundary updates; all roll back.
    runtime.stop()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_period BEFORE INSERT ON settings BEGIN SELECT RAISE(ABORT, 'period rollback'); END")
    before_failure = snapshot(path)
    runtime.start()
    assert runtime.call('/api/admin/rates', cookie=cookie, csrf=csrf, body={'savings_period_days': 1})[0] == 500
    runtime.stop()
    assert snapshot(path) == before_failure
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER fail_period')
    runtime.start()
    assert state(runtime, cookie)['config']['savings_period_days'] == 2
    assert runtime.call('/api/admin/fastforward', cookie=cookie, csrf=csrf, body={'days': 3})[1]['ok']
    assert runtime.call('/api/collect', cookie=cookie, csrf=csrf, body={'kid_id': 1, 'kind': 'invest'})[1]['collected_cents'] == 50
    rows = bank_rows(path)['bonds']
    assert rows[0]['collected'] == 0 and rows[-1]['collected'] == 1
    # Workers use the existing family capability, not a PIN; anonymous writes fail.
    auth_before = bank_rows(path)
    assert runtime.call('/api/admin/rates', csrf=csrf, body={'savings_period_days': 7})[0] == 401
    assert bank_rows(path) == auth_before


def test_carry_collection_and_transfers_after_rewind_do_not_reearn_time(runtime):
    # Exercise the unchanged domain code through real HTTP + transactionSync.
    # Fresh guests retain their original investment contracts throughout.
    for action in ('interest', 'all', 'save', 'withdraw'):
        cookie, csrf = runtime.guest()

        def post(path, **body):
            code, result, _ = runtime.call(path, cookie=cookie, csrf=csrf, body=body)
            assert code == 200 and result['ok'], result
            return result

        post('/api/admin/kid/allowance', kid_id=1, enabled=False, amount_cents=0, period_days=7)
        # The sample has historical savings interest; empty it first rather than
        # accidentally capitalizing that fixture carry into our exact $100 setup.
        post('/api/withdraw', kid_id=1, amount_cents=100000)
        post('/api/admin/gift', kid_id=1, amount_cents=10000)
        post('/api/save', kid_id=1, amount_cents=10000)
        path = runtime.database(cookie)
        contracts = bank_rows(path)['bonds']
        assert state(runtime, cookie)['kids'][0]['savings_cents'] == 10000
        post('/api/admin/fastforward', days=7)
        post('/api/admin/rates', savings_weekly_pct=4, savings_period_days=1)
        boundary = bank_rows(path)['kids'][0]['savings_accrued_ts']
        post('/api/admin/fastforward', days=-7)
        if action in ('interest', 'all'):
            assert post('/api/collect', kid_id=1, kind=action)['collected_cents'] == 400
            expected = 10400
        else:
            post('/api/' + action, kid_id=1, amount_cents=100)
            expected = 10500 if action == 'save' else 10300
        row = bank_rows(path)['kids'][0]
        assert row['savings_accrued_ts'] == boundary
        assert row['savings_interest_carry'] == 0
        assert row['savings_cents'] == expected
        post('/api/admin/fastforward', days=7)
        assert state(runtime, cookie)['kids'][0]['pending_interest_cents'] == 0
        assert post('/api/collect', kid_id=1, kind='interest')['collected_cents'] == 0
        post('/api/admin/fastforward', days=1)
        assert post('/api/collect', kid_id=1, kind='interest')['collected_cents'] == round(expected * .04)
        assert bank_rows(path)['bonds'] == contracts


def test_phone_rates_roundtrip_and_websocket_updates_without_polling(runtime):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            a_ctx = browser.new_context(viewport={'width': 390, 'height': 844})
            a_ctx.add_init_script(INIT)
            a = a_ctx.new_page()
            errors = []
            a.on('pageerror', lambda error: errors.append(str(error)))
            a.goto(runtime.origin)
            a.locator('#tryGuestBank').click()
            a.locator('#tourSkip').click()
            b_ctx = browser.new_context(storage_state=a_ctx.storage_state())
            b_ctx.add_init_script(INIT)
            b = b_ctx.new_page()
            b.goto(runtime.origin)
            c_ctx = browser.new_context()
            c_ctx.add_init_script(INIT)
            c = c_ctx.new_page()
            c.goto(runtime.origin)
            for page in (a, b, c):
                page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
            a.locator('#dadBtn').click()
            a.locator('.period-sav').fill('0')
            a.locator('#saveRates').click()
            assert not a.locator('.period-sav').evaluate('(i) => i.checkValidity()')
            a.locator('.period-sav').fill('2')
            a.locator('.rate-sav').fill('3.123456789')
            a.locator('.period-bond[data-key="1mo"]').fill('12')
            a.locator('.rate-bond[data-key="1mo"]').fill('33.125')
            a.locator('.period-bond[data-key="3mo"]').fill('45')
            assert a.locator('#dadRates').evaluate('(e) => e.scrollWidth <= e.clientWidth')
            assert a.evaluate('document.documentElement.scrollWidth <= innerWidth')
            a.set_viewport_size({'width': 320, 'height': 740})
            assert a.locator('#dadRates').evaluate('(e) => e.scrollWidth <= e.clientWidth')
            a.set_viewport_size({'width': 390, 'height': 844})
            a.locator('#saveRates').click()
            expect(a.locator('.rate-status')).to_have_text('Saved ✓')
            b.wait_for_function('STATE.config.savings_period_days === 2 && STATE.config.bonds[0].days === 12')
            assert b.evaluate('window.__frames.some(f => JSON.parse(f).type === "state_changed")')
            assert c.evaluate('STATE.config.savings_period_days') == 7
            assert not c.evaluate('window.__frames.some(f => JSON.parse(f).type === "state_changed")')
            expect(b.locator('.savings .pool-badge')).to_contain_text('3.123456789% / 2 days')
            # Old contract labels are from purchased duration, not the new offer.
            expect(b.locator('.bond-top').first).to_contain_text('1 Month lock')
            a.locator('#closeDad').click()
            a.locator('#dadBtn').click()
            expect(a.locator('.period-sav')).to_have_value('2')
            expect(a.locator('.rate-sav')).to_have_value('3.123456789')
            expect(a.locator('.period-bond[data-key="1mo"]')).to_have_value('12')
            a.locator('#closeDad').click()
            a.locator('.btn-bond').click()
            expect(a.locator('.bond-pick')).to_contain_text('12 Days')
            a.locator('.custom-amt').fill('1')
            expect(a.locator('#bondPreview')).to_contain_text('12 days')
            assert errors == []
        finally:
            browser.close()
