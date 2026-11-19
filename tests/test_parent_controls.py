"""Self-hosted real HTTP/SQLite and Chromium parent-label/PIN regression."""
import sqlite3
from pathlib import Path
import test_realtime


class ParentControlsTests(test_realtime.AppServerCase):
    def test_legacy_pin_name_persistence_and_browser(self):
        from playwright.sync_api import sync_playwright, expect
        database = Path(self.tmp.name) / 'bank.db'
        with sqlite3.connect(database) as conn:
            legacy_hash = conn.execute("SELECT value FROM settings WHERE key='dad_pin_hash'").fetchone()[0]
        self.assertFalse(self.post('/api/admin/label', {'parent_label': 'Mom'}, pin='wrong')['ok'])
        self.assertTrue(self.post('/api/admin/label', {'parent_label': 'Mom'})['ok'])
        self.assertEqual('Mom', self.get_state()['config']['parent_label'])
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(self.origin)
            expect(page.locator('#bankTitle')).to_have_text('🏦 Bank of Mom')
            page.locator('#dadBtn').click()
            expect(page.locator('#pinTitle')).to_have_text('🔒 Mom PIN')
            page.locator('#parentPinInput').fill('1234')
            page.locator('[data-k="ok"]').click()
            expect(page.locator('#dadPanel')).to_be_visible()
            page.locator('#parentLabel').fill("Nana & O'Brien")
            page.locator('#parentLabelForm button').click()
            expect(page.locator('#bankTitle')).to_have_text("🏦 Bank of Nana & O'Brien")
            page.locator('#closeDad').click()
            page.locator('#dadBtn').click()
            expect(page.locator('#pinTitle')).to_have_text("🔒 Nana & O'Brien PIN")
            page.keyboard.press('Escape')
            expect(page.locator('#dadPanel')).to_be_hidden()
            page.reload()
            expect(page.locator('#bankTitle')).to_have_text("🏦 Bank of Nana & O'Brien")
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        with sqlite3.connect(database) as conn:
            self.assertEqual(legacy_hash, conn.execute("SELECT value FROM settings WHERE key='dad_pin_hash'").fetchone()[0])
        self.assertFalse(self.post('/api/admin/pin', {'new_pin': '', 'current_pin': '1234'})['ok'])

    def test_no_pin_blocks_direct_admin_mutations_and_setup_race(self):
        database = Path(self.tmp.name) / 'bank.db'
        with sqlite3.connect(database) as conn:
            conn.execute("DELETE FROM settings WHERE key='dad_pin_hash'")
        self.assertFalse(self.post('/api/admin/gift', {'kid_id': self.kid_id, 'amount_cents': 50})['ok'])
        self.assertFalse(self.post('/api/admin/label', {'parent_label': 'Bypass'})['ok'])
        self.assertTrue(self.post('/api/admin/pin', {'new_pin': '12345678'})['ok'])
        self.assertFalse(self.post('/api/admin/pin', {'new_pin': '9999'})['ok'])
        self.assertTrue(self.post('/api/admin/label', {'parent_label': '爸爸'}, pin='12345678')['ok'])
        self.assertEqual('爸爸', self.get_state()['config']['parent_label'])
