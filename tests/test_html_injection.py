"""Stored user text must remain text in every dashboard and modal rendering."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")


@unittest.skipUnless(CHROMIUM, "Chromium is required for DOM regression coverage")
class StoredHtmlInjectionTests(unittest.TestCase):
    def test_stored_child_and_transaction_text_is_not_markup(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["BANK_OF_DAD_DB"] = os.path.join(tmp, "test.db")
            # Store through the real API, then feed its responses to the real frontend.
            fixture = subprocess.check_output([sys.executable, "-c", r'''
import json, app
c = app.app.test_client()
assert c.post('/api/admin/pin', json={'new_pin':'1234'}).get_json()['ok']
h = {'X-Dad-Pin':'1234'}
name = '<img src=x onerror="window.pwned=1"> & O\'Brien'
emoji = '<svg onload="window.pwned=1"></svg>🐣'
note = '<img src=x onerror="window.pwned=1"> & "gift"'
assert c.post('/api/admin/kid', json={'name':name,'emoji':emoji}, headers=h).get_json()['ok']
state = c.get('/api/state').get_json()
kid = state['kids'][0]
assert c.post('/api/admin/gift', json={'kid_id':kid['id'],'amount_cents':100,'note':note}, headers=h).get_json()['ok']
state = c.get('/api/state').get_json()
txs = c.get('/api/transactions?kid_id=' + str(kid['id'])).get_json()
print(json.dumps({'state':state,'txs':txs,'name':name,'emoji':emoji,'note':note}))
'''], cwd=ROOT, env=env, text=True)
            data = json.loads(fixture)
            source = (ROOT / "static/app.js").read_text().split('// Register the service worker', 1)[0]
            encoded = json.dumps(data).replace('<', r'\u003c')
            html = '''<!doctype html><meta charset="utf-8"><div id="kids"></div>
<div id="modal"><div id="modalCard"></div></div><div id="dadPanel"><div id="dadKids"></div><div id="dadRates"></div></div><pre id="result">PENDING</pre>
<script>''' + source + '''</script><script>
(async () => {
 try {
  const fixture = ''' + encoded + ''';
  STATE = fixture.state; SERVER_NOW = STATE.server_now; FETCH_AT = Date.now()/1000;
  const k = STATE.kids[0];
  window.fetch = async () => ({json: async () => fixture.txs});
  const assert = (ok, msg) => { if (!ok) throw new Error(msg); };
  const safe = (root, label) => {
    assert(!root.querySelector('img, svg[onload], [onerror]'), label + ' injected element');
    assert(!window.pwned, label + ' executed script');
  };
  render();
  assert(document.querySelector('.name').textContent === fixture.name, 'dashboard name');
  assert(document.querySelector('.avatar').textContent === fixture.emoji, 'dashboard emoji');
  assert(document.querySelector('.btn-expand') && document.querySelector('svg.chart'), 'dashboard controls and chart');
  safe(document.querySelector('#kids'), 'dashboard');
  openGraphModal(k);
  assert(document.querySelector('#modalCard h2').textContent === fixture.emoji + ' ' + fixture.name + "'s Money Over Time", 'graph heading');
  assert(document.querySelector('.future-rng') && document.querySelector('.graph-close').onclick, 'graph controls');
  safe(document.querySelector('#modalCard'), 'graph');
  openSaveModal(k);
  assert(document.querySelector('#modalCard h2').textContent === '🏦 ' + fixture.name + "'s Savings", 'savings heading');
  safe(document.querySelector('#modalCard'), 'savings');
  const hist = openHistoryModal(k);
  assert(document.querySelector('#modalCard h2').textContent === '📜 ' + fixture.name + "'s History", 'history loading heading');
  safe(document.querySelector('#modalCard'), 'history loading');
  await hist;
  assert(document.querySelector('.tx-note').textContent.includes(fixture.note), 'history note');
  safe(document.querySelector('#modalCard'), 'history loaded');
  const edit = openEditHistory(k);
  assert(document.querySelector('#modalCard h2').textContent === '📜 Edit ' + fixture.name + "'s transactions", 'edit loading heading');
  safe(document.querySelector('#modalCard'), 'edit loading');
  await edit;
  assert(document.querySelector('#modalCard h2').textContent === '📜 Edit ' + fixture.name + "'s transactions", 'edit loaded heading');
  assert(document.querySelector('.etx-note').value === fixture.note, 'edit note');
  safe(document.querySelector('#modalCard'), 'edit loaded');
  renderDadPanel();
  assert(document.querySelector('.dk-head').textContent.includes(fixture.emoji + ' ' + fixture.name), 'dad heading');
  assert(document.querySelector('.gift-btn').onclick && document.querySelector('.edithist-btn').onclick, 'dad controls');
  safe(document.querySelector('#dadPanel'), 'dad panel');
  document.querySelector('#result').textContent = 'PASS';
 } catch (e) { document.querySelector('#result').textContent = 'FAIL: ' + e.stack; }
})();</script>'''
            page = Path(tmp) / 'test.html'
            page.write_text(html)
            assert CHROMIUM is not None
            result = subprocess.run([CHROMIUM, '--headless', '--no-sandbox', '--disable-gpu',
                                     '--disable-dev-shm-usage', '--user-data-dir=' + tmp + '/profile',
                                     '--virtual-time-budget=3000', '--dump-dom', page.as_uri()],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('<pre id="result">PASS</pre>', result.stdout, result.stdout[-5000:])
