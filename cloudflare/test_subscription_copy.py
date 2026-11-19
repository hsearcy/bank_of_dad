"""Responsive real workerd UI; disposable SQL and signed loopback OIDC only."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from test_runtime import Runtime, INIT
from test_oauth import Provider, client, csrf, login
from test_owner import OWNER
from test_public import PublicRuntime, PUBLIC

FOUNDER = ("I built this just for my own kids, but friends kept asking if they could use it so I finally gave in and put it up here. "
           "If you're enjoying it, your small yearly contribution will help me pay server costs and do maintenance.")
FREE = 'If you need free access for your family, let me know and we can work something out.'


def test_responsive_subscription_copy_guest_saved_owner_and_pilot(tmp_path):
    provider = Provider()
    runtime = Runtime(tmp_path, {'BOD_OIDC_CLIENT_ID': 'fixture-client',
        'BOD_OIDC_CLIENT_SECRET': 'fixture-secret', 'BOD_TEST_OIDC_ORIGIN': provider.origin,
        'BOD_OWNER_IDENTITIES': '[{"issuer":"https://accounts.google.com","subject":"' + OWNER + '"}]'})
    runtime.start()
    try:
        with client(runtime) as guest, client(runtime) as saved:
            csrf(guest)
            csrf(saved)
            login(saved, provider, OWNER)
            sessions = {'guest': guest.cookies['bod-local-session'], 'saved-owner': saved.cookies['bod-local-session']}
        with sync_playwright() as pw, pw.chromium.launch(executable_path='/usr/bin/chromium') as browser:
            def check(server, pilot):
                for width in (390, 1280):
                    for kind, cookie in sessions.items():
                        context = browser.new_context(ignore_https_errors=True, viewport={'width': width, 'height': 844}, reduced_motion='reduce')
                        context.add_init_script(INIT)
                        # Public ingress remains real but all requests stay on the owned
                        # loopback TLS listener. Never resolve/contact the production host.
                        def forward(route):
                            assert route.request.url.startswith(server.origin + '/')
                            if not pilot:
                                return route.continue_()
                            headers = route.request.all_headers()
                            if pilot:
                                headers.update({'host': 'thebankofdad.app', 'origin': PUBLIC, 'cf-connecting-ip': '192.0.2.42'})
                            response = route.fetch(headers=headers, max_redirects=0)
                            route.fulfill(response=response)
                        context.route('**/*', forward)
                        context.add_cookies([{'name': 'bod-local-session', 'value': cookie, 'url': server.origin}])
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(server.origin)
                        if kind == 'guest':
                            welcome = page.locator('#guestWelcome')
                            if welcome.count():
                                expect(welcome).to_be_visible()
                                expect(welcome).to_contain_text('31 days')
                                expect(welcome).to_contain_text('No automatic charge')
                                page.locator('#tryGuestBank').click()
                                page.locator('#tourSkip').click()
                        open_parent(page)
                        page.locator('.dad-account summary').click()
                        account = page.locator('.dad-account')
                        expect(account).to_contain_text(FREE)
                        expect(account).to_contain_text('Please include your Family ID when you get in touch.')
                        expect(account.locator('.family-reference code')).to_have_text(cookie.split('.')[0])
                        expect(account.locator('.account-contact')).to_have_attribute('href', 'mailto:support@thebankofdad.app')
                        expect(account.locator('.account-open-source')).to_have_text('The Bank of Dad is also Open Source if you want to run it yourself for free!')
                        expect(account.locator('.account-open-source a')).to_have_text('run it yourself for free')
                        expect(account.locator('.account-open-source a')).to_have_attribute('href', 'https://github.com/hsearcy/bank_of_dad')
                        assert 'No card needed, and no automatic charge when it ends.' not in account.inner_text()
                        if pilot:
                            expect(account).to_contain_text('Private pilot — payments unavailable.')
                            expect(account).to_contain_text('31-day trial')
                            assert account.locator('.account-price').count() == 0
                        else:
                            expect(account.locator('.account-contribution')).to_have_text(FOUNDER)
                            expect(account.locator('.account-price')).to_contain_text('$9.99 USD per year')
                        assert account.locator('form[action="/billing/checkout"]').count() == (not pilot and kind != 'guest')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert account.evaluate('(el) => el.scrollWidth <= el.clientWidth')
                        assert page.locator('#dadPanel .panel-card').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                        account.locator('.account-contribution' if not pilot else '.account-help').scroll_into_view_if_needed()
                        artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
                        if artifacts:
                            Path(artifacts).mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(Path(artifacts) / f'subscription-{kind}-{width}-pilot-{pilot}.png'))
                            account.locator('.account-open-source').scroll_into_view_if_needed()
                            page.screenshot(path=str(Path(artifacts) / f'subscription-help-{kind}-{width}-pilot-{pilot}.png'))
                        assert not errors
                        context.unroute_all(behavior='wait')
                        context.close()
            check(runtime, False)
            runtime.stop()
            public = PublicRuntime(tmp_path)
            public.start()
            try:
                check(public, True)
            finally:
                public.stop()
    finally:
        runtime.stop()
        provider.stop()
