"""Cross-feature acceptance on disposable workerd state; signed local OIDC only."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import os
from pathlib import Path
import sqlite3

from test_runtime import snapshot
from test_oauth import client, login, csrf, bank
from test_owner import owner_runtime, OWNER, change


def test_combined_guest_edits_claim_owner_restart_and_responsive_ui(owner_runtime):
    from playwright.sync_api import sync_playwright, expect
    runtime, provider = owner_runtime
    with client(runtime) as parent, client(runtime) as other, client(runtime) as owner:
        token = csrf(parent)
        csrf(other)
        original_cookie = parent.cookies['bod-local-session']
        family = original_cookie.split('.')[0]
        path = runtime.database('bod-local-session=' + original_cookie)
        other_path = runtime.database('bod-local-session=' + other.cookies['bod-local-session'])
        sibling_before = snapshot(other_path)
        with sqlite3.connect(path) as conn:
            dates = conn.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone()
            contract = conn.execute('SELECT * FROM bonds').fetchall()
        headers = {'Origin': runtime.origin, 'X-CSRF-Token': token}
        def post(route, payload):
            response = parent.post('/api/' + route, json=payload, headers=headers)
            assert response.status_code == 200 and response.json()['ok'], response.text
        post('admin/kid/update', {'kid_id': 1, 'name': 'Combined Child'})
        post('admin/kid', {'name': 'Temporary'})
        removed = bank(parent)['kids'][-1]['id']
        post('admin/kid/remove', {'kid_id': removed, 'confirm_name': 'Temporary'})
        post('admin/fastforward', {'days': 3.5})
        earned = bank(parent)['kids'][0]['pending_interest_cents']
        post('admin/rates', {'savings_weekly_pct': 3, 'savings_period_days': 2,
                            'returns': {'1mo': 35}, 'bond_period_days': {'1mo': 12, '3mo': 45}})
        expected = bank(parent)
        assert expected['kids'][0]['pending_interest_cents'] == earned
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT * FROM bonds').fetchall() == contract
            assert conn.execute('SELECT savings_interest_carry FROM kids').fetchone()[0] > 0
        login(parent, provider, 'combined-polish-parent')
        assert parent.cookies['bod-local-session'].split('.')[0] == family
        assert bank(parent) == expected
        assert runtime.call('/api/state', cookie='bod-local-session=' + original_cookie)[0] == 401
        assert parent.get('/owner').status_code == 403
        login(owner, provider, OWNER)
        assert change(owner, runtime, family, 'grant').status_code == 303
        assert bank(parent) == expected
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT trial_started,trial_until FROM _bod_family').fetchone() == dates
            assert conn.execute('SELECT free_access FROM _bod_family').fetchone() == (1,)
        before = snapshot(path)
        runtime.stop()
        runtime.start()
        assert snapshot(path) == before
        assert bank(parent) == expected
        assert snapshot(other_path) == sibling_before
        with client(runtime) as returning:
            login(returning, provider, 'combined-polish-parent')
            assert bank(returning) == expected
        # All three features share the actual final template and JavaScript.
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            for width in (390, 1280):
                context = browser.new_context(viewport={'width': width, 'height': 844}, reduced_motion='reduce')
                context.add_cookies([{'name': 'bod-local-session', 'value': parent.cookies['bod-local-session'], 'url': runtime.origin}])
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(runtime.origin)
                page.locator('#tryGuestBank').wait_for(state='hidden')
                expect(page.locator('#kids .name')).to_have_text('Combined Child')
                for label in ('About', 'Privacy'):
                    page.get_by_role('contentinfo').get_by_role('button', name=label, exact=True).click()
                    dialog = page.get_by_role('dialog', name='About Bank of Dad' if label == 'About' else label, exact=True)
                    expect(dialog).to_be_visible()
                    page.keyboard.press('Escape')
                open_parent(page)
                expect(page.locator('.period-sav')).to_have_value('2')
                expect(page.locator('.period-bond[data-key="1mo"]')).to_have_value('12')
                page.locator('.rename-kid').click()
                page.locator('#kidName').fill('Combined Child')
                page.locator('#kidDialogSave').click()
                expect(page.locator('#kidDialog')).not_to_be_visible()
                expect(page.locator('.period-sav')).to_have_value('2')
                assert page.locator('.device-controls').bounding_box()['height'] < 150
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
                if artifacts:
                    Path(artifacts).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(artifacts) / f'combined-dad-{width}.png'), full_page=True)
                    page.locator('#dadRates').scroll_into_view_if_needed()
                    page.screenshot(path=str(Path(artifacts) / f'combined-rates-{width}.png'), full_page=True)
                assert errors == []
                context.close()
        assert bank(parent) == expected
        assert snapshot(other_path) == sibling_before
