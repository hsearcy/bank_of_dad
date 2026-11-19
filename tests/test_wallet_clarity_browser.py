"""Opt-in real self-hosted UI regression for spendable-wallet clarity."""
import os
import unittest

from test_realtime import AppServerCase
from wallet_clarity_ui import INIT, exercise_wallet


@unittest.skipUnless(os.environ.get('BANK_OF_DAD_BROWSER_TESTS') == '1', 'opt in to Chromium')
class WalletClarityBrowserTests(AppServerCase):
    def test_distinct_balances_mutations_push_reconnect_and_empty(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw, pw.chromium.launch() as first, pw.chromium.launch() as second:
            errors = []
            pages = []
            for browser in (first, second):
                context = browser.new_context()
                context.add_init_script(INIT)
                page = context.new_page()
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                page.goto(self.origin)
                page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
                pages.append(page)
            reconnect_errors = exercise_wallet(pages[0], pages[1], self.post, self.get_state, 'self-hosted')
            # Existing Werkzeug/simple-websocket emits this console diagnostic
            # on an intentional client close, reproduced with origin/master JS.
            # All ordinary UI actions must be console-clean; do not suppress it.
            self.assertEqual(reconnect_errors, errors)
            for error in reconnect_errors:
                self.assertRegex(error, r"^WebSocket connection to 'ws://127\.0\.0\.1:\d+/api/updates' failed: Invalid frame header$")

    def test_initial_loading_does_not_invent_zero_wallet(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            context = browser.new_context(service_workers='block')
            held = []
            context.route('**/api/state', lambda route: held.append(route))
            page = context.new_page()
            page.goto(self.origin)
            page.wait_for_timeout(150)
            self.assertTrue(held)
            self.assertEqual(0, page.locator('.wallet-value').count())
            for route in held[:]:
                route.continue_()
            context.unroute_all(behavior='wait')
            page.wait_for_function("document.querySelector('.wallet-value')?.textContent === '$5.00'")
