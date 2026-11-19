"""Real self-hosted PIN, Chromium controls, push and disposable persistence."""
from parent_pin_ui import open_parent

import os
import sqlite3
import subprocess
import unittest

from test_realtime import AppServerCase, ROOT
from kid_management_ui import exercise_kid_controls


@unittest.skipUnless(os.environ.get('BANK_OF_DAD_BROWSER_TESTS') == '1', 'opt in to Chromium')
class KidManagementBrowserTests(AppServerCase):
    def restart(self):
        args = self.proc.args
        self.stop()
        env = dict(os.environ, BANK_OF_DAD_DB=os.path.join(self.tmp.name, 'bank.db'))
        self.proc = subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                     stderr=self.log, text=True)
        assert self.proc.stdout is not None
        port = self.proc.stdout.readline().strip()
        assert port.isdigit()
        self.origin = 'http://127.0.0.1:' + port

    def test_rename_and_remove_survive_server_restart(self):
        self.post('/api/save', {'kid_id': self.kid_id, 'amount_cents': 100})
        self.post('/api/bond', {'kid_id': self.kid_id, 'amount_cents': 100, 'term': '1mo'})
        self.assertTrue(self.post('/api/admin/kid/update', {'kid_id': self.kid_id, 'name': 'Persisted'})['ok'])
        self.restart()
        kid = self.get_state()['kids'][0]
        self.assertEqual(('Persisted', 300, 100, 100),
                         (kid['name'], kid['wallet_cents'], kid['savings_cents'], kid['invest_cents']))
        self.assertTrue(self.post('/api/admin/kid/remove', {'kid_id': self.kid_id, 'confirm_name': 'Persisted'})['ok'])
        self.restart()
        self.assertEqual([], self.get_state()['kids'])

    def test_phone_controls_and_live_push(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw, pw.chromium.launch() as first, pw.chromium.launch() as second:
            pages = [first.new_page(), second.new_page()]
            errors = []
            for page in pages:
                page.add_init_script('''const native = window.setInterval;
                    window.setInterval = (fn, ms, ...args) => ms === 30000 ? 0 : native(fn, ms, ...args);
                    window.__sockets = [];
                    const Socket = window.WebSocket;
                    window.WebSocket = class extends Socket {constructor(...args) {super(...args); window.__sockets.push(this);}};''')
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(self.origin)
                page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
            def unlock(page):
                open_parent(page)
                page.locator('#dadPanel').wait_for(state='visible')
            exercise_kid_controls(pages[0], pages[1], unlock)
            assert self.get_state()['kids'] == []
            assert errors == []
        with sqlite3.connect(os.path.join(self.tmp.name, 'bank.db')) as conn:
            for table in ('kids', 'bonds', 'transactions', 'history'):
                assert conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
