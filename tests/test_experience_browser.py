"""Real self-hosted Chromium; synthetic banks, no hosted credentials or data."""
import os
from pathlib import Path
import unittest

import test_realtime


@unittest.skipUnless(os.environ.get('BANK_OF_DAD_BROWSER_TESTS') == '1', 'opt in to Chromium')
class ExperienceBrowserTests(test_realtime.AppServerCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.p = sync_playwright().start()
        self.addCleanup(self.p.stop)
        self.browser = self.p.chromium.launch(executable_path=os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium'), args=['--no-sandbox'])
        self.addCleanup(self.browser.close)

    def page(self, script=None, width=390):
        ctx = self.browser.new_context(viewport={'width': width, 'height': 844})
        self.addCleanup(ctx.close)
        if script:
            ctx.add_init_script(script)
        page = ctx.new_page()
        page.goto(self.origin)
        page.locator('#kids .big').wait_for()
        self.open_dad(page)
        return page

    def open_dad(self, page):
        page.locator('#dadBtn').click()
        for digit in '1234':
            page.locator(f'.keypad button[data-k="{digit}"]').click()
        page.locator('#dadPanel').wait_for(state='visible')

    def test_wake_lock_state_denial_visibility_and_stale_requests(self):
        script = (Path(__file__).parent / 'fixtures/wake_lock.js').read_text()
        page = self.page(script)
        toggle = page.locator('#keepAwake')
        self.assertFalse(toggle.is_checked())
        self.assertEqual(0, page.evaluate('__wake.calls'))
        page.evaluate('__wake.mode = "hold"')
        toggle.check()
        page.wait_for_function('__wake.pending.length === 1')
        toggle.uncheck()
        page.evaluate('__wake.resolve()')
        page.wait_for_function('__wake.releases === 1')
        self.assertEqual('Off', page.locator('#awakeStatus').inner_text())
        page.evaluate('__wake.mode = "auto"')
        toggle.check()
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.startsWith("Active")')
        page.evaluate('__wake.locks.at(-1).release()')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.includes("browser released")')
        calls = page.evaluate('__wake.calls')
        page.wait_for_timeout(350)
        self.assertEqual(calls, page.evaluate('__wake.calls'))
        page.evaluate('__wake.visibility(false); __wake.visibility(true)')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.startsWith("Active")')
        toggle.uncheck()
        page.wait_for_function('__wake.locks.every(lock => lock.released)')
        page.evaluate('__wake.mode = "deny"')
        toggle.check()
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.includes("declined")')
        calls = page.evaluate('__wake.calls')
        page.wait_for_timeout(350)
        self.assertEqual(calls, page.evaluate('__wake.calls'))
        self.assertTrue(toggle.is_checked())  # Intent is separate from actual lock.
        toggle.uncheck()
        page.evaluate('__wake.mode = "hold"')
        toggle.check()
        page.wait_for_function('__wake.pending.length === 1')
        page.evaluate('__wake.visibility(false); __wake.visibility(true); __wake.mode = "auto"; __wake.resolve()')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.startsWith("Active")')
        self.assertEqual(1, page.evaluate('__wake.locks.filter(lock => !lock.released).length'))
        page.evaluate('window.dispatchEvent(new Event("pagehide"))')
        page.wait_for_function('__wake.locks.every(lock => lock.released)')
        self.assertIn('Paused', page.locator('#awakeStatus').inner_text())
        page.evaluate('window.dispatchEvent(new Event("pageshow"))')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.startsWith("Active")')
        # An off/on while the previous acquire is pending does not resurrect it.
        toggle.uncheck()
        page.evaluate('__wake.mode = "hold"')
        toggle.check()
        page.wait_for_function('__wake.pending.length === 1')
        toggle.uncheck()
        toggle.check()
        page.evaluate('__wake.mode = "auto"; __wake.resolve()')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.startsWith("Active")')
        self.assertEqual(1, page.evaluate('__wake.locks.filter(lock => !lock.released).length'))
        page.reload()
        self.open_dad(page)
        self.assertFalse(toggle.is_checked())
        self.assertEqual(0, page.evaluate('__wake.calls'))
        self.assertEqual('Off', page.locator('#awakeStatus').inner_text())

    def test_unsupported_and_insecure_are_honest(self):
        for script, message in [
            ('Object.defineProperty(navigator, "wakeLock", {value: undefined})', 'does not support'),
            ('Object.defineProperty(window, "isSecureContext", {value: false})', 'needs HTTPS'),
        ]:
            page = self.page(script)
            self.assertTrue(page.locator('#keepAwake').is_disabled())
            self.assertIn(message, page.locator('#awakeStatus').inner_text())

    def test_native_chromium_wake_lock_and_selfhost_tour(self):
        for width in [390, 1280]:
            page = self.page(width=width)
            self.assertTrue(page.evaluate('window.isSecureContext'))
            toggle = page.locator('#keepAwake')
            if page.evaluate('!!navigator.wakeLock?.request'):
                toggle.check()
                page.wait_for_function('!document.querySelector("#awakeStatus").textContent.startsWith("Requesting")')
                observed = page.locator('#awakeStatus').inner_text()
                self.assertTrue(observed.startswith('Active') or 'declined' in observed, observed)
                print(f'Native Chromium Screen Wake Lock ({width}px): {observed}')
                toggle.uncheck()
                self.assertEqual('Off', page.locator('#awakeStatus').inner_text())
            else:
                self.assertTrue(toggle.is_disabled())
                print(f'Native Chromium Screen Wake Lock ({width}px): unsupported')
            before = self.get_state()['kids'][0]['owned_cents']
            page.locator('#replayTour').click()
            page.locator('#tourNext').click()
            page.locator('#tourSkip').click()
            self.assertFalse(page.locator('#productTour').is_visible())
            self.assertEqual(0, page.locator('.tour-target').count())
            self.assertEqual(before, self.get_state()['kids'][0]['owned_cents'])
            # Existing local PIN and money controls still work.
            self.open_dad(page)
            page.locator('.gift-amt').fill('1')
            page.locator('.gift-note').fill('Synthetic experience test')
            page.locator('.gift-btn').click()
            page.wait_for_function('document.querySelector("#kids .big").textContent === "$6.00"')
            page.reload()
            page.wait_for_function('document.querySelector("#kids .big").textContent === "$6.00"')
            # Reset only this disposable fixture for the second viewport.
            self.post('/api/spend', {'kid_id': self.kid_id, 'amount_cents': 100, 'reason': 'Synthetic reset'})
