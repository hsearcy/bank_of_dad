"""Actual workerd, durable SQLite, idle alarm, concurrent sessions and real UI."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
import pytest
from test_runtime import runtime, state, snapshot, INIT


def test_immediate_concurrent_catchup_restart_and_isolation(runtime):
    cookie, csrf = runtime.parent_guest()
    other, _ = runtime.guest()
    def post(path, **body):
        status, result, _ = runtime.parent_call(path, cookie=cookie, csrf=csrf, body=body)
        assert status == 200 and result['ok'], result
        return result
    initial = state(runtime, cookie)['kids'][0]
    assert not state(runtime, cookie)['config']['auto_interest_enabled']
    assert runtime.call('/api/admin/rates', cookie=cookie, body={'auto_interest_enabled': True})[0] == 403
    post('/api/admin/rates', auto_interest_enabled=True)
    enabled = state(runtime, cookie)['kids'][0]
    assert enabled['savings_cents'] == initial['savings_cents']+initial['pending_interest_cents']
    path = runtime.database(cookie)
    with sqlite3.connect(path) as c:
        anchor, due = c.execute('SELECT savings_accrued_ts,auto_interest_due_ts FROM kids').fetchone()
        assert due-anchor == 7*86400
    # Simulate an offline demo-clock advance while the runtime is stopped;
    # next access must replay the saved schedule, not claim once at access time.
    runtime.stop()
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO settings(key,value) VALUES('time_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(28*86400),))
    runtime.start()
    expected = enabled['savings_cents']
    # The sample has fractional pre-enable carry; compare exact ledger sum too.
    for _ in range(8): state(runtime, cookie)
    before = snapshot(path)
    with ThreadPoolExecutor(4) as pool:
        responses = list(pool.map(lambda _: runtime.call('/api/collect', cookie=cookie, csrf=csrf,
                         body={'kid_id': 1, 'kind': 'interest'}), range(8)))
    assert all(r[0] == 200 and r[1]['collected_cents'] == 0 for r in responses)
    assert snapshot(path) == before
    with sqlite3.connect(path) as c:
        rows = c.execute("SELECT amount_cents FROM transactions WHERE kind='interest'").fetchall()
        assert len(rows) == 5
        assert state(runtime, cookie)['kids'][0]['savings_cents'] == initial['savings_cents']+sum(r[0] for r in rows)
    runtime.stop(); runtime.start()
    assert snapshot(path) == before
    assert state(runtime, cookie)['kids'][0]['savings_cents'] == enabled['savings_cents']+sum(r[0] for r in rows[1:])
    assert not state(runtime, other)['config']['auto_interest_enabled']
    post('/api/admin/rates', auto_interest_enabled=False)
    disabled = state(runtime, cookie)['kids'][0]['savings_cents']
    post('/api/admin/fastforward', days=7)
    assert state(runtime, cookie)['kids'][0]['savings_cents'] == disabled


def test_idle_alarm_and_websocket_without_dashboard_activity(runtime):
    from websockets.sync.client import connect
    cookie, csrf = runtime.parent_guest()
    runtime.parent_call('/api/admin/rates', cookie=cookie, csrf=csrf, body={'auto_interest_enabled': True})
    path = runtime.database(cookie)
    runtime.stop()
    due = time.time()+12
    with sqlite3.connect(path) as c:
        c.execute('UPDATE kids SET savings_cents=10000,savings_interest_carry=0,savings_accrued_ts=?,auto_interest_due_ts=?', (due-7*86400,due))
        c.execute("DELETE FROM transactions WHERE kind='interest'")
    runtime.start()
    # The socket only arms the existing alarm. There are no reads/writes after
    # connection until the persisted interest row proves idle settlement.
    with connect(runtime.origin.replace('http:', 'ws:')+'/api/updates', origin=runtime.origin,
                 additional_headers={'Cookie':cookie}, open_timeout=15) as ws:
        assert 'connected' in ws.recv(timeout=15)
        assert 'state_changed' in ws.recv(timeout=25)
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT savings_cents FROM kids').fetchone()[0] == 10400
            assert c.execute("SELECT count(*) FROM transactions WHERE kind='interest'").fetchone()[0] == 1
        assert state(runtime,cookie)['kids'][0]['savings_cents'] == 10400


@pytest.mark.parametrize('width,height', [(390,844),(1440,1000)])
def test_parent_toggle_real_browser(runtime,width,height):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        try:
            context = browser.new_context(viewport={'width':width,'height':height})
            context.add_init_script(INIT)
            page = context.new_page()
            errors=[]
            page.on('pageerror',lambda e: errors.append(str(e)))
            page.goto(runtime.origin)
            page.wait_for_function('STATE.kids.length === 1')
            page.locator('#tryGuestBank').click()
            page.locator('#tourSkip').click()
            from parent_pin_ui import open_parent
            open_parent(page)
            toggle=page.get_by_role('checkbox',name='Automatically collect savings interest for every child')
            expect(toggle).not_to_be_checked()
            expect(page.locator('#dadRates')).to_contain_text('Background collection is supported')
            toggle.check()
            page.locator('#saveRates').click()
            expect(page.locator('.rate-status')).to_have_text('Saved ✓')
            page.wait_for_function('STATE.config?.auto_interest_enabled === true')
            page.reload()
            page.wait_for_function('STATE.config?.auto_interest_enabled === true')
            from parent_pin_ui import open_parent
            open_parent(page)
            expect(page.get_by_role('checkbox',name='Automatically collect savings interest for every child')).to_be_checked()
            assert page.locator('#dadRates').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
            assert errors == []
        finally: browser.close()
