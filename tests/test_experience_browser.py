"""Real self-hosted Chromium; synthetic banks, no hosted credentials or data."""
from parent_pin_ui import open_parent

import os
from pathlib import Path
import sqlite3
import unittest

import test_realtime


@unittest.skipUnless(os.environ.get('BANK_OF_DAD_BROWSER_TESTS') == '1', 'opt in to Chromium')
class ExperienceBrowserBase(test_realtime.AppServerCase):
    def setUp(self):
        super().setUp()
        from playwright.sync_api import sync_playwright
        self.p = sync_playwright().start()
        self.addCleanup(self.p.stop)
        self.browser = self.p.chromium.launch(executable_path=os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium'), args=['--no-sandbox'])
        self.addCleanup(self.browser.close)

    def page(self, script=None, width=390, height=844):
        ctx = self.browser.new_context(viewport={'width': width, 'height': height})
        self.addCleanup(ctx.close)
        if script:
            ctx.add_init_script(script)
        page = ctx.new_page()
        page.goto(self.origin)
        page.locator('#kids .big' if getattr(self, 'seed_kid', True) else '#kids > p').first.wait_for()
        self.open_dad(page)
        return page

    def open_dad(self, page):
        open_parent(page)
        page.locator('#dadPanel').wait_for(state='visible')

    def bank_snapshot(self):
        with sqlite3.connect(Path(self.tmp.name) / 'bank.db') as conn:
            return list(conn.iterdump())


class ExperienceBrowserTests(ExperienceBrowserBase):
    def test_founder_story_and_tour_return_without_mutation(self):
        from about_story_ui import check_about_story
        self.assertTrue(self.post('/api/admin/label', {'parent_label': 'Mom'})['ok'])
        for width in (390, 1280):
            page = self.page(width=width, height=667)
            page.locator('#closeDad').click()
            before = self.bank_snapshot()
            check_about_story(page, width)
            self.assertEqual('Mom', page.evaluate('parentName()'))
            self.assertIn('I give my kids', page.locator('#aboutDialog').text_content())
            self.assertEqual(before, self.bank_snapshot())

    def test_real_save_keeps_tour_and_finishes_information(self):
        from tour_browser_flow import save_and_finish_tour
        for width in (390, 1280):
            page = self.page(width=width, height=667)
            before = self.get_state()['kids'][0]
            save_and_finish_tour(page, width)
            after = self.get_state()['kids'][0]
            self.assertEqual(before['owned_cents'], after['owned_cents'])
            self.assertEqual(before['wallet_cents'] - 100, after['wallet_cents'])
            self.assertEqual(before['savings_cents'] + 100, after['savings_cents'])

    def test_footer_dialogs_and_compact_screen_controls(self):
        for width in [390, 1280]:
            with self.subTest(width=width):
                page = self.page(width=width, height=667)
                page.emulate_media(reduced_motion='reduce')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                self.assertLess(page.locator('.device-controls').bounding_box()['height'], 200)
                self.assertLess(len(page.locator('#awakeHelp').inner_text()), 65)
                page.locator('#closeDad').click()
                before = self.bank_snapshot()
                footer = page.get_by_role('contentinfo')
                self.assertTrue(footer.is_visible())
                for name, title in [('About', 'About Bank of Dad'), ('Privacy', 'Privacy')]:
                    opener = footer.get_by_role('button', name=name, exact=True)
                    opener.focus()
                    page.keyboard.press('Enter')
                    dialog = page.get_by_role('dialog', name=title, exact=True)
                    self.assertTrue(dialog.is_visible())
                    self.assertEqual(title, page.locator(':focus').inner_text())
                    page.keyboard.press('Tab')
                    self.assertTrue(page.locator(':focus').evaluate('(el) => !!el.closest("dialog")'))
                    self.assertTrue(dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().bottom <= innerHeight'))
                    page.keyboard.press('Escape')
                    self.assertFalse(dialog.is_visible())
                    self.assertTrue(opener.evaluate('(el) => el === document.activeElement'))
                    opener.click()
                    dialog.get_by_role('button', name='Close', exact=True).click()
                    self.assertFalse(dialog.is_visible())
                footer.get_by_role('button', name='Privacy', exact=True).click()
                privacy = page.get_by_role('dialog', name='Privacy', exact=True)
                self.assertIn('server running this copy', privacy.inner_text())
                self.assertNotIn('Google', privacy.inner_text())
                self.assertNotIn('Stripe', privacy.inner_text())
                privacy.get_by_role('button', name='Close', exact=True).click()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                self.assertEqual(before, self.bank_snapshot())
                self.assertEqual([], errors)

    def test_tour_prioritizes_interest_on_later_child(self):
        self.assertTrue(self.post('/api/admin/kid', {'name': 'Interest ready'})['ok'])
        second_id = self.get_state()['kids'][1]['id']
        self.assertTrue(self.post('/api/save', {'kid_id': second_id, 'amount_cents': 100})['ok'])
        self.assertTrue(self.post('/api/admin/fastforward', {'days': 7})['ok'])
        page = self.page(height=667)
        cards = page.locator('#kids .card')
        self.assertEqual(0, cards.nth(0).locator('.reward-btn[data-kind="interest"]').count())
        self.assertEqual(1, cards.nth(1).locator('.reward-btn[data-kind="interest"]').count())
        before = self.bank_snapshot()
        page.locator('#replayTour').click()
        page.locator('#tourNext').click()
        page.locator('#tourNext').click()
        interest = cards.nth(1).locator('.reward-btn[data-kind="interest"]')
        self.assertIn('tour-target', interest.get_attribute('class'))
        self.assertIn('ready to try now', page.locator('#tourText').inner_text())
        page.evaluate('backgroundRefresh()')
        page.wait_for_function('document.querySelector(".reward-btn[data-kind=interest]").classList.contains("tour-target")')
        self.assertTrue(interest.evaluate('(el) => { const r=el.getBoundingClientRect(); return r.top >= 0 && r.bottom < document.querySelector("#productTour").getBoundingClientRect().top; }'))
        for _ in range(5):
            page.locator('#tourNext').click()
        self.assertFalse(page.locator('#productTour').is_visible())
        self.assertEqual(before, self.bank_snapshot())

    def test_tour_interest_fallback_explains_save_without_ready_reward(self):
        page = self.page(height=667)
        before = self.bank_snapshot()
        page.locator('#replayTour').click()
        page.locator('#tourNext').click()
        page.locator('#tourNext').click()
        self.assertIn('tour-target', page.locator('.btn-save').get_attribute('class'))
        text = page.locator('#tourText').inner_text()
        self.assertIn('Save money', text)
        self.assertNotIn('ready to try now', text)
        page.keyboard.press('Escape')
        self.assertEqual(before, self.bank_snapshot())

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
        self.assertEqual('', page.locator('#awakeStatus').text_content())
        self.assertFalse(page.locator('#awakeStatus').is_visible())
        page.evaluate('__wake.mode = "auto"')
        toggle.check()
        page.wait_for_function('document.querySelector("#awakeStatus").textContent === "" && __wake.locks.some(lock => !lock.released)')
        page.evaluate('__wake.locks.at(-1).release()')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent.includes("browser released")')
        calls = page.evaluate('__wake.calls')
        page.wait_for_timeout(350)
        self.assertEqual(calls, page.evaluate('__wake.calls'))
        page.evaluate('__wake.visibility(false); __wake.visibility(true)')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent === "" && __wake.locks.some(lock => !lock.released)')
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
        page.wait_for_function('document.querySelector("#awakeStatus").textContent === "" && __wake.locks.some(lock => !lock.released)')
        self.assertEqual(1, page.evaluate('__wake.locks.filter(lock => !lock.released).length'))
        page.evaluate('window.dispatchEvent(new Event("pagehide"))')
        page.wait_for_function('__wake.locks.every(lock => lock.released)')
        self.assertIn('Paused', page.locator('#awakeStatus').inner_text())
        page.evaluate('window.dispatchEvent(new Event("pageshow"))')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent === "" && __wake.locks.some(lock => !lock.released)')
        self.open_dad(page)  # pagehide correctly clears the shared-device PIN.
        # An off/on while the previous acquire is pending does not resurrect it.
        toggle.uncheck()
        page.evaluate('__wake.mode = "hold"')
        toggle.check()
        page.wait_for_function('__wake.pending.length === 1')
        toggle.uncheck()
        toggle.check()
        page.evaluate('__wake.mode = "auto"; __wake.resolve()')
        page.wait_for_function('document.querySelector("#awakeStatus").textContent === "" && __wake.locks.some(lock => !lock.released)')
        self.assertEqual(1, page.evaluate('__wake.locks.filter(lock => !lock.released).length'))
        page.reload()
        self.open_dad(page)
        self.assertFalse(toggle.is_checked())
        self.assertEqual(0, page.evaluate('__wake.calls'))
        self.assertEqual('', page.locator('#awakeStatus').text_content())
        self.assertFalse(page.locator('#awakeStatus').is_visible())

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
                self.assertTrue(observed == '' or 'declined' in observed, observed)
                print(f'Native Chromium Screen Wake Lock ({width}px): {observed}')
                toggle.uncheck()
                self.assertEqual('', page.locator('#awakeStatus').text_content())
                self.assertFalse(page.locator('#awakeStatus').is_visible())
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
            page.wait_for_function('document.querySelector("#kids .big")?.textContent === "$6.00"')
            page.reload()
            page.wait_for_function('document.querySelector("#kids .big")?.textContent === "$6.00"')
            # Reset only this disposable fixture for the second viewport.
            self.post('/api/spend', {'kid_id': self.kid_id, 'amount_cents': 100, 'reason': 'Synthetic reset'})


class EmptyExperienceBrowserTests(ExperienceBrowserBase):
    seed_kid = False

    def test_fresh_empty_bank_tour_only_points_to_dad_and_explains_add_child(self):
        page = self.page(height=667)
        self.assertEqual([], self.get_state()['kids'])
        before = self.bank_snapshot()
        page.locator('#replayTour').click()
        self.assertNotIn('Four', page.locator('#tourText').inner_text())
        page.locator('#tourNext').click()
        self.assertIn('1 of 2', page.locator('#tourTitle').inner_text())
        self.assertIn('add your first kid', page.locator('#tourText').inner_text())
        self.assertEqual('dadBtn', page.locator('.tour-target').get_attribute('id'))
        self.assertEqual('Next', page.locator('#tourNext').inner_text())
        page.evaluate('backgroundRefresh()')
        page.wait_for_function('document.querySelector("#dadBtn").classList.contains("tour-target")')
        page.locator('#tourNext').click()
        self.assertIn('A few things to know', page.locator('#tourTitle').inner_text())
        self.assertEqual('Done', page.locator('#tourNext').inner_text())
        page.locator('#tourNext').focus()
        page.keyboard.press('Enter')
        self.assertFalse(page.locator('#productTour').is_visible())
        self.assertEqual(0, page.locator('.tour-target').count())
        self.assertEqual(before, self.bank_snapshot())
        # The guidance leads to the real add-child controls, without adding data.
        self.open_dad(page)
        self.assertTrue(page.locator('#newKidName').is_visible())
        self.assertEqual(before, self.bank_snapshot())
