"""Real disposable SQLite DO / Chromium parent-label and PIN regressions."""
import sqlite3
from test_runtime import runtime, state, snapshot


def enter_pin(page, value):
    page.locator('#parentPinInput').fill(value)
    page.locator('.pin-card [data-k="ok"]').click()


def test_parent_controls_first_use_isolation_restart_browser_and_push(runtime):
    from playwright.sync_api import sync_playwright, expect
    a, token = runtime.guest()
    b, btoken = runtime.guest()
    path = runtime.database(a)
    sibling = runtime.database(b)
    sibling_before = snapshot(sibling)
    def post(route, body, pin=None, cookie=a, csrf=token):
        return runtime.call('/api/admin/' + route, cookie=cookie, csrf=csrf, body=body,
                            headers={'X-Dad-Pin': pin} if pin else {})
    assert runtime.call('/api/admin/status', cookie=a)[1] == {'pin_set': False}
    for route, body in [('label', {'parent_label': 'Mom'}), ('gift', {'kid_id': 1, 'amount_cents': 50})]:
        assert post(route, body)[0] == 403
    assert post('pin', {'new_pin': ''})[0] == 403
    # No PIN setup barrier on guest landing; first parent open requires two entries.
    with sync_playwright() as pw, pw.chromium.launch() as browser:
        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        context.add_cookies([{'name': 'bod-local-session', 'value': a.split('=',1)[1], 'url': runtime.origin}])
        context.add_init_script("""const old = window.setInterval;
            window.setInterval = (fn,ms,...args) => ms === 30000 ? 0 : old(fn,ms,...args);
            window.__sockets=[]; const Native=WebSocket;
            window.WebSocket=class extends Native {constructor(...args){super(...args);window.__sockets.push(this)}};""")
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(runtime.origin)
        page.locator('#tryGuestBank').click()
        page.locator('#tourSkip').click()
        page.wait_for_function('window.__sockets.some(s=>s.readyState===1)')
        page.locator('#dadBtn').click()
        expect(page.locator('#pinTitle')).to_have_text('🔒 Set Dad PIN')
        page.keyboard.press('Escape')
        expect(page.locator('#dadPanel')).to_be_hidden()
        page.locator('#dadBtn').click()
        enter_pin(page, '12345678')
        expect(page.locator('#pinTitle')).to_have_text('🔒 Confirm PIN')
        enter_pin(page, '12345679')
        expect(page.locator('#pinHelp')).to_contain_text("didn't match")
        enter_pin(page, '12345678')
        enter_pin(page, '12345678')
        expect(page.locator('#dadPanel')).to_be_visible()
        page.locator('#parentLabel').fill('Mom')
        page.locator('#parentLabelForm button').click()
        expect(page.locator('#bankTitle')).to_have_text('🏦 Bank of Mom')
        expect(page.locator('#parentPanelTitle')).to_have_text('👨‍👧 Mom Controls')
        page.locator('#closeDad').click()
        page.locator('#dadBtn').click()
        expect(page.locator('#pinTitle')).to_have_text('🔒 Mom PIN')
        enter_pin(page, '11111111')
        expect(page.locator('#pinHelp')).to_contain_text('Wrong PIN')
        enter_pin(page, '12345678')
        expect(page.locator('#dadPanel')).to_be_visible()
        # Same-family push updates escaped labels without polling; XSS is literal.
        attack = '<img src=x onerror=alert(1)>'
        assert post('label', {'parent_label': attack}, '12345678')[1]['ok']
        expect(page.locator('#bankTitle')).to_have_text('🏦 Bank of ' + attack)
        assert page.locator('#bankTitle img').count() == 0
        page.locator('#closeDad').click()
        page.locator('#dadBtn').click()
        expect(page.locator('#pinTitle')).to_have_text('🔒 ' + attack + ' PIN')
        assert page.locator('#pinTitle img').count() == 0
        enter_pin(page, '12345678')
        assert post('label', {'parent_label': 'Alex 🧑🏽'}, '12345678')[1]['ok']
        expect(page.locator('#bankTitle')).to_have_text('🏦 Bank of Alex 🧑🏽')
        page.reload()
        page.locator('#dadBtn').click()
        expect(page.locator('#pinTitle')).to_have_text('🔒 Alex 🧑🏽 PIN')
        enter_pin(page, '12345678')
        expect(page.locator('#dadPanel')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1280, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
        context.close()
    for invalid in ('', ' '*4, 'x'*41, 'Mom\n', 42, None):
        assert post('label', {'parent_label': invalid}, '12345678')[0] == 400
    assert post('pin', {'new_pin': '9999'})[0] == 403
    assert post('pin', {'new_pin': '', 'current_pin': '12345678'})[0] == 403
    assert post('label', {'parent_label': 'Foreign'}, '12345678', b, btoken)[0] == 403
    assert state(runtime, b)['config']['parent_label'] == 'Dad'
    assert runtime.call('/api/admin/label', body={'parent_label': 'Foreign'})[0] == 401
    assert runtime.call('/api/admin/label', cookie=a, csrf=token, body={'parent_label': 'Foreign'},
                        origin='https://foreign.invalid', headers={'X-Dad-Pin':'12345678'})[0] == 403
    with sqlite3.connect(path) as conn:
        original_hash = conn.execute("SELECT value FROM settings WHERE key='dad_pin_hash'").fetchone()[0]
        assert original_hash != '12345678'
    runtime.stop()
    runtime.start()
    assert state(runtime, a)['config']['parent_label'] == 'Alex 🧑🏽'
    assert runtime.call('/api/admin/status', cookie=a)[1]['pin_set']
    assert post('label', {'parent_label': 'Mom'}, '12345678')[1]['ok']
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='dad_pin_hash'").fetchone()[0] == original_hash
    # Durable throttle applies to verification, direct writes and PIN replacement.
    for _ in range(5):
        assert not post('verify', {'pin': '0000'})[1]['ok']
    assert not post('verify', {'pin': '12345678'})[1]['ok']
    assert post('label', {'parent_label': 'Bypass'}, '12345678')[0] == 403
    runtime.stop()
    runtime.start()
    assert not post('verify', {'pin': '12345678'})[1]['ok']
    assert snapshot(sibling) == sibling_before


def test_parent_label_socket_delivery_stays_in_family(runtime):
    import json
    import pytest
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus
    a, token = runtime.guest()
    b, _ = runtime.guest()
    assert runtime.call('/api/admin/pin', cookie=a, csrf=token, body={'new_pin':'8765'})[1]['ok']
    url = runtime.origin.replace('http:', 'ws:') + '/api/updates'
    with pytest.raises(InvalidStatus):
        with connect(url, origin=runtime.origin, additional_headers={'X-Dad-Pin':'8765'}):
            pass  # A PIN is not a family capability.
    with connect(url, origin=runtime.origin, additional_headers={'Cookie':a}, close_timeout=1) as same, \
         connect(url, origin=runtime.origin, additional_headers={'Cookie':b}, close_timeout=1) as other:
        assert json.loads(same.recv(timeout=5))['type'] == 'connected'
        assert json.loads(other.recv(timeout=5))['type'] == 'connected'
        assert runtime.call('/api/admin/label', cookie=a, csrf=token,
            body={'parent_label':'Mom'}, headers={'X-Dad-Pin':'8765'})[1]['ok']
        assert json.loads(same.recv(timeout=5)) == {'type':'state_changed'}
        with pytest.raises(TimeoutError):
            other.recv(timeout=1)
        assert state(runtime, b)['config']['parent_label'] == 'Dad'
