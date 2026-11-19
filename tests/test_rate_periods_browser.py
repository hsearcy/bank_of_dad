"""Real self-hosted phone UI, PIN and push acceptance on disposable state."""
from parent_pin_ui import open_parent

import os
import unittest

import test_realtime


@unittest.skipUnless(os.environ.get('BANK_OF_DAD_BROWSER_TESTS') == '1', 'opt in to Chromium')
class RatePeriodsBrowserTests(test_realtime.AppServerCase):
    def test_rates_periods_pin_unchanged_and_push(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(viewport={'width': 390, 'height': 844})
                context.add_init_script("""(() => {
                  const interval = window.setInterval;
                  window.setInterval = (fn, ms, ...args) => ms === 30000 ? 0 : interval(fn, ms, ...args);
                  window.__sockets = [];
                  const Native = window.WebSocket;
                  window.WebSocket = class extends Native {
                    constructor(...args) { super(...args); window.__sockets.push(this); }
                  };
                })();""")
                a, b = context.new_page(), context.new_page()
                errors = []
                for page in (a, b):
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(self.origin)
                    page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
                self.assertFalse(self.post('/api/admin/rates', {'savings_period_days': 1}, pin='wrong')['ok'])
                open_parent(a)
                expect(a.locator('.period-sav')).to_have_value('7')
                a.locator('#saveRates').click()
                expect(a.locator('.rate-status')).to_have_text('Saved ✓')
                a.locator('.period-sav').fill('14')
                a.locator('.rate-sav').fill('4.125')
                a.locator('.period-bond[data-key="1mo"]').fill('15')
                a.locator('.rate-bond[data-key="1mo"]').fill('33.25')
                a.locator('#saveRates').click()
                expect(a.locator('.rate-status')).to_have_text('Saved ✓')
                b.wait_for_function('STATE.config.savings_period_days === 14 && STATE.config.bonds[0].days === 15')
                expect(b.locator('.savings .pool-badge')).to_contain_text('4.125% / 14 days')
                self.assertTrue(a.locator('#dadRates').evaluate('(e) => e.scrollWidth <= e.clientWidth'))
                # Forecasts and what-if use the chosen savings period, not a week.
                projected = b.evaluate('project({wallet:0,savings:10000,invest:0}, 0, 14).at(-1).savings')
                self.assertAlmostEqual(projected, 10412.5)
                whatif = b.evaluate('whatIfProjection({}, [{t:0,total:10000},{t:14*86400,total:10000}], {save:10000,m1:0,m3:0}).at(-1).total')
                self.assertAlmostEqual(whatif, 10412.5)
                a.locator('#closeDad').click()
                open_parent(a)
                expect(a.locator('.period-sav')).to_have_value('14')
                expect(a.locator('.rate-bond[data-key="1mo"]')).to_have_value('33.25')
                self.assertEqual([], errors)
            finally:
                browser.close()
