"""Owner panel through real workerd, signed OIDC HTTP fixture and Chromium.

Only disposable SQL is edited while the owned runtime is stopped. No production
code has a fixture login, owner bypass or test grant endpoint.
"""
import json
import re
import sqlite3
import time

import pytest

from test_runtime import Runtime, snapshot, INIT
from test_oauth import Provider, client, login, csrf, bank

OWNER = 'disposable-configured-owner'


@pytest.fixture(scope='module')
def owner_runtime(tmp_path_factory):
    provider = Provider()
    runtime = Runtime(tmp_path_factory.mktemp('owner-runtime'), {
        'BOD_OIDC_CLIENT_ID': 'fixture-client', 'BOD_OIDC_CLIENT_SECRET': 'fixture-secret',
        'BOD_TEST_OIDC_ORIGIN': provider.origin,
        'BOD_OWNER_IDENTITIES': json.dumps([dict(issuer='https://accounts.google.com', subject=OWNER)])})
    runtime.start()
    try:
        yield runtime, provider
    finally:
        runtime.stop()
        provider.stop()


def form_token(c):
    result = c.get('/owner')
    assert result.status_code == 200, result.text
    return re.search('name="csrf_token" value="([0-9a-f]+)"', result.text)[1]


def change(c, runtime, family, action='grant', **values):
    data = dict(csrf_token=form_token(c), family=family, action=action,
                reason='Disposable verification', confirm_revoke='yes')
    data.update(values)
    return c.post('/owner/access', data=data, headers={'Origin': runtime.origin})


def test_owner_denial_guest_api_and_csrf_fail_closed(owner_runtime):
    runtime, provider = owner_runtime
    with client(runtime) as owner, client(runtime) as parent, client(runtime) as guest, client(runtime) as anon:
        assert anon.get('/owner').status_code == 401
        token = csrf(guest)
        family = guest.cookies['bod-local-session'].split('.')[0]
        login(parent, provider, 'ordinary-parent-not-owner')
        login(owner, provider, OWNER)
        assert guest.get('/owner').status_code == 403
        assert parent.get('/owner').status_code == 403
        data = dict(csrf_token=token, family=family, action='grant', reason='Forbidden')
        for c in (guest, parent, anon):
            assert c.post('/owner/access', data=data, headers={'Origin': runtime.origin}).status_code in (401, 403)
        # Public bank routes cannot dispatch owner RPCs or use client role claims.
        for path in ('/api/owner', '/api/owner/access', '/owner/anything'):
            assert guest.post(path, json=dict(role='owner', operation='access', **data),
                              headers={'Origin': runtime.origin, 'X-CSRF-Token': token}).status_code in (403, 404)
        data['csrf_token'] = form_token(owner)
        for origin in ('https://foreign.invalid', 'null', ''):
            assert owner.post('/owner/access', data=data, headers={'Origin': origin}).status_code == 403
        assert owner.post('/owner/access', data={**data, 'csrf_token': 'wrong'}, headers={'Origin': runtime.origin}).status_code == 403
        assert change(owner, runtime, family, reason=' ').status_code == 400
        assert change(owner, runtime, family, 'revoke', confirm_revoke='').status_code == 400
        assert change(owner, runtime, '0' * 32).status_code == 404
        with sqlite3.connect(runtime.database('bod-local-session=' + guest.cookies['bod-local-session'])) as sql:
            assert sql.execute('SELECT free_access FROM _bod_family').fetchone() == (0,)
        assert owner.get('/owner?family=%3Cscript%3E').status_code == 400
        assert owner.get('/owner?page=0').status_code == 400


def test_guest_hidden_from_list_prefix_exact_and_direct_access(owner_runtime):
    runtime, provider = owner_runtime
    with client(runtime) as owner, client(runtime) as guest:
        login(owner, provider, OWNER)
        csrf(guest)
        family = guest.cookies['bod-local-session'].split('.')[0]
        path = runtime.database('bod-local-session=' + guest.cookies['bod-local-session'])
        before = snapshot(path)
        for search in ('', family[:8], family):
            started = time.monotonic()
            result = owner.get('/owner', params={'family': search})
            assert result.status_code == 200
            print('LOCAL_OWNER_LIST_MS', round((time.monotonic() - started) * 1000, 1))
            assert 'data-family="' + family + '"' not in result.text
        assert 'data-family="' + owner.cookies['bod-local-session'].split('.')[0] + '"' in owner.get('/owner').text
        for action in ('grant', 'revoke'):
            assert change(owner, runtime, family, action).status_code == 404
        assert snapshot(path) == before
        assert guest.get('/api/state').status_code == 200


def test_expired_owner_can_grant_saved_family_and_audit_rollback_paid_unchanged(owner_runtime):
    runtime, provider = owner_runtime
    with client(runtime) as owner, client(runtime) as guest:
        login(owner, provider, OWNER)
        csrf(guest)
        login(guest, provider, 'saved-expired-target')
        family = guest.cookies['bod-local-session'].split('.')[0]
        guest_path = runtime.database('bod-local-session=' + guest.cookies['bod-local-session'])
        owner_path = runtime.database('bod-local-session=' + owner.cookies['bod-local-session'])
        runtime.stop()
        for path in (guest_path, owner_path):
            with sqlite3.connect(path) as sql:
                sql.execute('UPDATE _bod_family SET trial_until=0, paid_until=0, free_access=0')
        runtime.start()
        assert owner.get('/api/state').status_code == 402
        assert guest.get('/api/state').status_code == 402
        result = change(owner, runtime, family, reason='<script>not executable</script>')
        assert result.status_code == 303, result.text
        html = owner.get(result.headers['location']).text
        assert 'Free access' in html and '&lt;script&gt;not executable&lt;/script&gt;' in html
        assert OWNER not in html and 'guest_hash' not in html and 'wallet_cents' not in html
        assert guest.get('/api/state').status_code == 200
        assert 'mailto:support@thebankofdad.app' in guest.get('/billing').text
        runtime.stop()
        with sqlite3.connect(guest_path) as sql:
            audit = sql.execute('SELECT family,action,reason,operator,previous,current FROM _bod_access_audit').fetchall()
            assert audit == [(family, 'grant', '<script>not executable</script>', 'oidc:https://accounts.google.com:' + OWNER, 0, 1)]
            sql.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON _bod_access_audit BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END")
        before = snapshot(guest_path)
        runtime.start()
        assert change(owner, runtime, family, 'revoke').status_code == 503
        runtime.stop()
        assert snapshot(guest_path) == before
        with sqlite3.connect(guest_path) as sql:
            sql.execute('DROP TRIGGER fail_audit')
            paid = time.time() + 86400
            sql.execute('UPDATE _bod_family SET paid_until=?', (paid,))
        runtime.start()
        assert change(owner, runtime, family, 'revoke').status_code == 303
        assert guest.get('/api/state').status_code == 200  # paid access survives
        assert 'Paid access' in owner.get('/owner?family=' + family).text
        runtime.stop()
        with sqlite3.connect(guest_path) as sql:
            assert sql.execute('SELECT paid_until,trial_until,free_access FROM _bod_family').fetchone() == (paid, 0, 0)
            assert sql.execute('SELECT action,previous,current FROM _bod_access_audit ORDER BY id').fetchall() == [('grant', 0, 1), ('revoke', 1, 0)]
            assert sql.execute('SELECT wallet_cents FROM kids WHERE id=1').fetchone() == (500,)
        runtime.start()
        assert guest.get('/api/state').status_code == 200


def test_responsive_browser_grant_revoke_readback_and_idle_socket(owner_runtime):
    from playwright.sync_api import sync_playwright, expect
    runtime, provider = owner_runtime
    with client(runtime) as owner, client(runtime) as target:
        login(owner, provider, OWNER)
        csrf(target)
        login(target, provider, 'saved-browser-target')
        family = target.cookies['bod-local-session'].split('.')[0]
        path = runtime.database('bod-local-session=' + target.cookies['bod-local-session'])
        runtime.stop()
        with sqlite3.connect(path) as sql:
            sql.execute('UPDATE _bod_family SET trial_until=0')
        runtime.start()
        with sync_playwright() as p, p.chromium.launch() as browser:
            ctx = browser.new_context()
            ctx.add_cookies([dict(name='bod-local-session', value=owner.cookies['bod-local-session'], url=runtime.origin)])
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            for width, height in ((390, 844), (768, 1024), (1440, 1000)):
                page.set_viewport_size(dict(width=width, height=height))
                page.goto(runtime.origin + '/owner?family=' + family)
                expect(page.get_by_role('heading', name='Families & access')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size(dict(width=390, height=844))
            card = page.locator('[data-family="' + family + '"]')
            card.locator('[name=reason]').fill('Browser free-access grant')
            card.get_by_role('button', name='Grant free access').click()
            expect(page.get_by_role('status')).to_contain_text('Free-access change saved')
            expect(card.locator('.owner-status')).to_have_text('Free access')
            family_ctx = browser.new_context(viewport=dict(width=390, height=844))
            family_ctx.add_init_script(INIT)
            family_ctx.add_cookies([dict(name='bod-local-session', value=target.cookies['bod-local-session'], url=runtime.origin)])
            tab = family_ctx.new_page()
            tab.goto(runtime.origin)
            tab.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
            expect(tab.locator('#kids .big')).to_have_text('$7.00')
            card.locator('[name=reason]').fill('Browser free-access revocation')
            card.locator('[name=confirm_revoke]').check()
            card.get_by_role('button', name='Revoke free access').click()
            expect(card.locator('.owner-status')).to_have_text('Expired')
            tab.wait_for_url('**/billing')
            expect(tab.locator('.account-contact')).to_have_attribute('href', 'mailto:support@thebankofdad.app')
            assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert errors == []
        assert target.get('/api/state').status_code == 402
        with sqlite3.connect(path) as sql:
            assert sql.execute('SELECT action,reason FROM _bod_access_audit ORDER BY id').fetchall() == [
                ('grant', 'Browser free-access grant'), ('revoke', 'Browser free-access revocation')]
            assert sql.execute('SELECT wallet_cents FROM kids WHERE id=1').fetchone() == (500,)


def test_missing_and_malformed_allowlist_remain_closed(owner_runtime):
    runtime, provider = owner_runtime
    with client(runtime) as owner:
        login(owner, provider, OWNER)
        configured = runtime.variables['BOD_OWNER_IDENTITIES']
        try:
            for config, status in (('', 404), ('{}', 503), ('[{"issuer":"https://evil.invalid","subject":"x"}]', 503)):
                runtime.stop()
                runtime.variables['BOD_OWNER_IDENTITIES'] = config
                runtime.start()
                assert owner.get('/owner').status_code == status
                assert owner.post('/owner/access', data={}, headers={'Origin': runtime.origin}).status_code == status
        finally:
            runtime.stop()
            runtime.variables['BOD_OWNER_IDENTITIES'] = configured
            runtime.start()
