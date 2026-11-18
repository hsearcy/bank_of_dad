"""Actual responsive owner UI, synthetic signed provider, disposable HTTPS/DB."""
import os
from pathlib import Path
import threading

import pytest
from werkzeug.serving import make_server

from test_owner import owner_app, family
from test_hosted import hosted_app, login


@pytest.mark.parametrize('width', [390, 1280])
def test_owner_browser_login_search_grant_revoke_and_denial(owner_app, width):
    from playwright.sync_api import sync_playwright, expect
    app, host, state, _ = owner_app
    # Create an expired target through the actual signed synthetic login first.
    login(app.test_client(), state, host.origin, 'parent-a')
    fid = family(host, 'parent-a')['id']
    with host.connect() as conn:
        conn.execute('UPDATE families SET trial_until=0 WHERE id=?', (fid,))
    server = make_server('127.0.0.1', 0, app, threaded=True, ssl_context='adhoc')
    origin = 'https://localhost:' + str(server.server_port)
    host.origin = origin
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium'),
                                        headless=True, args=['--no-sandbox'])
            context = browser.new_context(ignore_https_errors=True, viewport={'width': width, 'height': 900})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            response = page.goto(origin + '/owner')
            assert response.status == 401
            state['subject'] = 'owner-synthetic'
            state['claims'] = {'iss': 'accounts.google.com'}  # Google's documented alias, verified/canonicalized.
            page.get_by_role('link', name='Sign in to owner controls').click()
            expect(page).to_have_url(origin + '/owner')
            expect(page.get_by_role('heading', name='Families & access')).to_be_visible()
            assert state['token_calls'] == 2
            expect(page.locator('article[data-family]')).to_have_count(2)
            expect(page.locator('.owner-status-trial')).to_be_visible()
            expect(page.locator('.owner-status-expired')).to_be_visible()
            search = page.get_by_label('Find a Family ID', exact=False)
            search.fill(fid)
            page.get_by_role('button', name='Search', exact=True).click()
            cards = page.locator('article[data-family]')
            expect(cards).to_have_count(1)
            card = page.locator(f'article[data-family="{fid}"]')
            expect(card.locator('.owner-status')).to_have_text('Expired')
            card.get_by_label('Audit reason', exact=False).fill('Synthetic browser grant')
            card.get_by_role('button', name='Grant free access', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('change saved')
            expect(card.locator('.owner-status')).to_have_text('Free access')
            assert family(host, 'parent-a')['free_access'] == 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
            if artifacts:
                Path(artifacts).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(artifacts) / f'owner-{width}.png'), full_page=True)
            card.get_by_label('Audit reason', exact=False).fill('Synthetic browser revoke')
            card.get_by_label('Remove only the free override', exact=False).check()
            card.get_by_role('button', name='Revoke free access', exact=True).click()
            expect(card.locator('.owner-status')).to_have_text('Expired')
            assert family(host, 'parent-a')['free_access'] == 0
            # Valid server session but missing CSRF, missing/wrong Origin: real HTTP denied.
            assert context.request.post(origin + '/owner/access', form={'family': fid, 'action': 'grant',
                                        'reason': 'forged'}).status == 403
            page.get_by_role('button', name='Sign out of Bank of Dad', exact=True).click()
            expect(page.get_by_text('You are signed out of Bank of Dad.')).to_be_visible()
            assert context.request.get(origin + '/owner').status == 401
            # Normal signed-in parent cannot read owner data, even with spoofed URL/body values.
            state['subject'] = 'parent-a'
            page.goto(origin + '/auth/login?owner=1')
            expect(page.get_by_role('heading', name='Forbidden')).to_be_visible()
            assert context.request.get(origin + '/api/owner/families').status == 403
            assert context.request.post(origin + '/owner/access', form={'family': fid, 'action': 'grant',
                                        'reason': 'forged', 'role': 'owner'}).status == 403
            guest_context = browser.new_context(ignore_https_errors=True)
            guest_page = guest_context.new_page()
            guest_page.goto(origin + '/')
            assert guest_context.request.get(origin + '/owner').status == 403
            assert guest_context.request.get(origin + '/api/owner/families').status == 403
            assert family(host, 'parent-a')['free_access'] == 0
            with host.connect() as conn:
                audit = list(conn.execute('SELECT action, reason FROM access_audit ORDER BY id'))
            assert [tuple(row) for row in audit] == [('grant', 'Synthetic browser grant'), ('revoke', 'Synthetic browser revoke')]
            assert state['subscription_calls'] == 0 and not state['customers']
            assert not errors
            guest_context.close()
            context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join()
