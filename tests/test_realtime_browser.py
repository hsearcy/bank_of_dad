"""Opt-in real Chromium coverage of the existing template and app.js.

Run BANK_OF_DAD_BROWSER_TESTS=1 python -m unittest discover -s tests -p '*browser.py' -v
Requires: pip install playwright && python -m playwright install chromium
"""
from parent_pin_ui import open_parent

import os
import unittest

import test_realtime


@unittest.skipUnless(os.environ.get("BANK_OF_DAD_BROWSER_TESTS") == "1",
                     "opt in to real Chromium tests (see module docstring)")
class RealtimeBrowserTests(test_realtime.AppServerCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.errors = []

    def page(self, *, disable_poll=True, disable_socket=False):
        # Separate browser processes, not two tabs sharing a JS context.
        browser = self.playwright.chromium.launch(
            executable_path=os.environ.get("BOD_TEST_CHROMIUM"))
        self.addCleanup(browser.close)
        page = browser.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.add_init_script("""(() => {
          window.__sockets = [];
          const Native = window.WebSocket;
          window.WebSocket = class extends Native {
            constructor(...args) { super(...args); window.__sockets.push(this); }
          };
        })();""")
        if disable_poll:
            page.add_init_script("""(() => {
              const native = window.setInterval;
              window.setInterval = (fn, ms, ...args) =>
                ms === 30000 ? 0 : native(fn, ms, ...args);
            })();""")
        if disable_socket:
            page.add_init_script("delete window.WebSocket;")
        if not disable_poll:
            page.clock.install()  # install before the app creates its intervals
        page.goto(self.origin)
        page.wait_for_function("document.querySelector('#kids .big')?.textContent === '$5.00'")
        if not disable_socket:
            page.wait_for_function("window.__sockets.some(ws => ws.readyState === 1)")
        return page

    def wait_money(self, page, amount):
        page.wait_for_function("amount => document.querySelector('#kids .big')?.textContent === amount",
                               arg=amount, timeout=5000)

    def unlock_dad(self, page):
        open_parent(page)
        page.locator("#dadPanel").wait_for(state="visible")

    def test_real_controls_push_between_two_browsers_and_keep_animations(self):
        a, b = self.page(), self.page()
        # Both pages are already subscribed; inspect actual server frames too.
        b.evaluate("window.__frames = []; window.__sockets.at(-1).addEventListener('message', e => window.__frames.push(e.data))")
        self.unlock_dad(a)
        a.locator(".gift-amt").fill("2")
        a.locator(".gift-note").fill("Synthetic chores")
        a.locator(".gift-btn").click()
        self.wait_money(b, "$7.00")
        self.assertTrue(b.evaluate("window.__frames.some(f => JSON.parse(f).type === 'state_changed')"))
        a.locator("#closeDad").click()
        # An open input on the remote browser survives a pushed dashboard update.
        b.locator(".wallet .btn-spend").click()
        b.locator(".spend-reason").fill("in progress")
        a.locator(".btn-save").click()
        a.locator(".custom-amt").fill("1")
        a.locator('[data-act="save"]').click()
        b.wait_for_function("document.querySelector('.s-val')?.textContent === '$1.00'")
        self.assertEqual("in progress", b.locator(".spend-reason").input_value())
        b.locator("#modal .close").click()
        self.assertEqual(600, self.get_state()["kids"][0]["wallet_cents"])
        self.assertEqual(100, self.get_state()["kids"][0]["savings_cents"])
        # Fast-forward via the real Dad control, then collect with the existing
        # confetti, synth sound, reward removal and hero-spin path intact.
        self.unlock_dad(a)
        a.locator('.ff[data-days="30"]').click()
        a.locator("#closeDad").click()
        b.locator('.reward-btn[data-kind="allowance"]').wait_for()
        b.evaluate("window.__hero = document.querySelector('#kids .big')")
        # Reward buttons deliberately pulse forever; bypass only Playwright's
        # stability wait, not the real click handler or any app animation.
        b.locator('.reward-btn[data-kind="allowance"]').click(force=True)
        b.wait_for_timeout(200)
        self.assertTrue(b.evaluate("window.__hero.isConnected"))
        self.assertGreater(b.locator("#confetti .confetti-bit").count(), 0)
        self.wait_money(b, "$13.00")
        self.wait_money(a, "$13.00")
        self.assertEqual(1200, self.get_state()["kids"][0]["wallet_cents"])
        self.assertEqual([], self.errors)

    def test_reconnect_reads_missed_write_without_polling(self):
        page = self.page()
        page.evaluate("window.__sockets.at(-1).close()")
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 50})
        self.wait_money(page, "$5.50")
        self.assertGreaterEqual(page.evaluate("window.__sockets.length"), 2)
        # Page lifecycle cancellation must not reconnect until resumed.
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        count = page.evaluate("window.__sockets.length")
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 25})
        page.wait_for_timeout(1200)
        self.assertEqual(count, page.evaluate("window.__sockets.length"))
        page.evaluate("window.dispatchEvent(new Event('pageshow'))")
        self.wait_money(page, "$5.75")
        self.assertEqual([], self.errors)

    def test_open_graph_refreshes_and_invalidations_during_a_read_are_not_lost(self):
        page = self.page()
        page.locator(".btn-expand").click()
        old_chart = page.locator(".expanded-chart").inner_html()
        # Hold a REAL server response (not a fabricated snapshot) while another
        # committed mutation invalidates it. The next read must see both gifts.
        held = []
        def hold_first(route):
            if not held:
                held.append((route, route.fetch()))
            else:
                route.continue_()
        # Context routing also sees network requests owned by the real PWA's
        # service worker; page-only routing cannot intercept those requests.
        page.context.route("**/api/state", hold_first)
        with page.expect_request("**/api/state"):
            self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 50})
        page.wait_for_timeout(100)
        self.assertEqual(1, len(held))
        page.evaluate("window.__changes = 0; window.__sockets.at(-1).addEventListener('message', () => window.__changes++)")
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 25})
        page.wait_for_function("window.__changes > 0")
        held[0][0].fulfill(response=held[0][1])
        self.wait_money(page, "$5.75")
        self.assertNotEqual(old_chart, page.locator(".expanded-chart").inner_html())
        page.locator(".graph-close").click()
        page.locator(".wallet .btn-spend").click()
        page.locator(".spend-reason").fill("keep this form")
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 25})
        self.wait_money(page, "$6.00")
        self.assertEqual("keep this form", page.locator(".spend-reason").input_value())
        self.assertEqual([], self.errors)

    def test_polling_fallback_without_websocket_support(self):
        page = self.page(disable_poll=False, disable_socket=True)
        # Advance only the browser timer clock; do not call refresh ourselves.
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 75})
        page.clock.run_for(31000)
        self.wait_money(page, "$5.75")
        self.assertEqual([], self.errors)


if __name__ == "__main__":
    unittest.main()
