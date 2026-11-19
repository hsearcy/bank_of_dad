"""Wallet UX against actual local workerd/DO SQLite; no external providers."""
import sys

from test_runtime import ROOT, runtime, state

sys.path.insert(0, str(ROOT / 'tests'))
from wallet_clarity_ui import INIT, exercise_wallet


def test_wallet_clarity_real_runtime(runtime):
    from playwright.sync_api import sync_playwright
    cookie, csrf = runtime.parent_guest()
    def post(path, body):
        return runtime.parent_call(path, cookie=cookie, csrf=csrf, body=body)[1]
    kid = state(runtime, cookie)['kids'][0]
    assert post('/api/admin/kid/remove', {'kid_id': kid['id'], 'confirm_name': kid['name']})['ok']
    assert post('/api/admin/kid', {'name': 'Synthetic'})['ok']
    with sync_playwright() as pw, pw.chromium.launch() as first, pw.chromium.launch() as second:
        errors = []
        pages = []
        for browser in (first, second):
            context = browser.new_context()
            context.add_init_script(INIT)
            name, value = cookie.split('=', 1)
            context.add_cookies([{'name': name, 'value': value, 'url': runtime.origin}])
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            # Expected invalid-amount HTTP responses are tested through the
            # independent API client, not hidden by console filtering.
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
            page.goto(runtime.origin)
            if not pages:
                page.locator('#tryGuestBank').click()
                page.locator('#tourSkip').click()
            page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
            pages.append(page)
        exercise_wallet(*pages, post, lambda: state(runtime, cookie), 'workerd')
        assert errors == []
