"""Real workerd/SQLite and Chromium acceptance tests; no provider mocks.

Run from cloudflare/: uv run pytest -q test_runtime.py
Every run owns a loopback process group and disposable persistence directory.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import contextlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


class Runtime:
    def __init__(self, base, variables=None, *, options=None, readiness_status=200):
        self.base = base
        self.options = options or []
        self.readiness_status = readiness_status
        self.ssl_context = None
        self.variables = variables or {}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        self.proc = None

    def start(self):
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(('CLOUDFLARE_', 'CF_API_', 'BOD_', 'BANK_OF_DAD_')):
                del env[key]
        home = self.base / 'home'
        home.mkdir(exist_ok=True)
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                   WRANGLER_LOG_PATH=str(self.base / 'logs'), WRANGLER_SEND_METRICS='false',
                   PATH=str(HERE / '.venv/bin') + ':' + env['PATH'])
        self.log = (self.base / 'runtime.log').open('a+')
        extra = [arg for k, v in self.variables.items() for arg in ('--var', k + ':' + v)]
        self.proc = subprocess.Popen([str(HERE / '.venv/bin/pywrangler'), 'dev', '--local',
            '--ip', '127.0.0.1', '--port', str(self.port), '--inspector-port', '0',
            '--var', 'BOD_PUBLIC_ORIGIN:' + self.origin, '--persist-to', str(self.base / 'state'), *extra, *self.options],
            cwd=HERE, env=env, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if self.call('/static/style.css')[0] == self.readiness_status:
                    return
            except (OSError, URLError):
                time.sleep(.2)
        self.stop()
        raise AssertionError('workerd startup failed: ' + (self.base / 'runtime.log').read_text()[-6000:])

    @staticmethod
    def _group_running(pgid):
        # pywrangler exits before npm/Node/workerd finish releasing listeners and
        # SQLite. Linux zombies own neither and may await host-init reaping.
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == pgid and fields[0] != 'Z':
                    return True
            except (OSError, ValueError, IndexError):
                continue  # process exited during inspection
        return False

    def stop(self):
        if self.proc is not None:
            proc = self.proc
            # The leader may already have exited while descendants still run.
            for sig, timeout in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, sig)
                deadline = time.monotonic() + timeout
                while self._group_running(proc.pid) and time.monotonic() < deadline:
                    proc.poll()  # reap the leader independently of descendants
                    time.sleep(.05)
                if not self._group_running(proc.pid):
                    break
            else:
                raise AssertionError(f'Owned runtime process group {proc.pid} did not stop')
            proc.wait(timeout=5)
            self.proc = None
            self.log.close()

    def call(self, path, *, cookie=None, csrf=None, body=None, origin=True, headers=None):
        hdr = {'Content-Type': 'application/json'}
        if cookie:
            hdr['Cookie'] = cookie
        if csrf:
            hdr['X-CSRF-Token'] = csrf
        if origin:
            hdr['Origin'] = self.origin if origin is True else origin
        hdr.update(headers or {})
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.origin + path, headers=hdr, data=data)
        try:
            response = urlopen(req, timeout=45, context=self.ssl_context)
        except HTTPError as error:
            response = error
        raw = response.read()
        content = json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw.decode()
        return response.status, content, response.headers

    def guest(self):
        code, html, headers = self.call('/')
        assert code == 200
        cookie = headers['Set-Cookie'].split(';')[0]
        csrf = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)[1]
        return cookie, csrf

    def parent_guest(self):
        cookie, token = self.guest()
        assert self.call('/api/admin/pin', cookie=cookie, csrf=token,
                         body={'new_pin': '1234'})[1]['ok']
        return cookie, token

    def parent_call(self, path, **kwargs):
        headers = {'X-Dad-Pin': '1234', **kwargs.pop('headers', {})}
        return self.call(path, headers=headers, **kwargs)

    def database(self, cookie):
        family = cookie.split('=')[1].split('.')[0]
        for path in (self.base / 'state').rglob('*.sqlite'):
            with sqlite3.connect(path) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='_bod_family'").fetchone():
                    if conn.execute('SELECT id FROM _bod_family').fetchone()[0] == family:
                        return path
        raise AssertionError('Persisted family not found')


@pytest.fixture(scope='module')
def runtime():
    with tempfile.TemporaryDirectory(prefix='bod-cloudflare-', dir=os.environ.get('TMPDIR')) as temp:
        server = Runtime(Path(temp))
        server.start()
        try:
            yield server
        finally:
            server.stop()


def state(server, cookie):
    code, result, _ = server.call('/api/state', cookie=cookie)
    assert code == 200
    return result


def test_current_routes_against_independent_sqlite_oracle(runtime, tmp_path):
    import bank
    import db
    from guest_bank import create_sample
    cookie, csrf = runtime.parent_guest()
    initial = state(runtime, cookie)
    assert (initial['kids'][0]['wallet_cents'], initial['kids'][0]['savings_cents'], initial['kids'][0]['invest_cents']) == (500, 100, 100)
    path = tmp_path / 'oracle.sqlite'
    db.init_db(path=path)
    oracle = db.get_conn(path)
    create_sample(oracle)
    def action(path, body, fn):
        code, result, _ = runtime.parent_call(path, cookie=cookie, csrf=csrf, body=body)
        assert code == 200 and result['ok'], result
        fn()
        actual = state(runtime, cookie)['kids'][0]
        expected = bank.all_kids_state(oracle)[0]
        for key in ('wallet_cents', 'savings_cents', 'invest_cents', 'owned_cents', 'pending_cents'):
            assert actual[key] == expected[key], (path, key, actual[key], expected[key])
    try:
        action('/api/admin/gift', dict(kid_id=1, amount_cents=500, note='Chores'), lambda: bank.gift(oracle, 1, 500, 'Chores'))
        action('/api/save', dict(kid_id=1, amount_cents=200), lambda: bank.move_to_savings(oracle, 1, 200))
        action('/api/withdraw', dict(kid_id=1, amount_cents=100), lambda: bank.withdraw_savings(oracle, 1, 100))
        action('/api/bond', dict(kid_id=1, amount_cents=100, term='1mo'), lambda: bank.buy_bond(oracle, 1, '1mo', 100))
        action('/api/spend', dict(kid_id=1, amount_cents=50, reason='Snack'), lambda: bank.spend(oracle, 1, 50, 'Snack'))
        action('/api/admin/kid/update', dict(kid_id=1, name='Test Child'), lambda: bank.update_kid(oracle, 1, 'Test Child'))
        action('/api/admin/kid/allowance', dict(kid_id=1, amount_cents=400, period_days=7, enabled=True), lambda: bank.set_allowance(oracle, 1, 400, 7, True))
        action('/api/admin/rates', dict(savings_weekly_pct=2, returns={'1mo': 30}), lambda: bank.set_rates(oracle, 2, {'1mo': 30}))
        action('/api/admin/fastforward', dict(days=30), lambda: bank.fast_forward(oracle, 30))
        action('/api/collect', dict(kid_id=1, kind='all'), lambda: bank.collect_rewards(oracle, 1, 'all'))
        actual = runtime.call('/api/transactions?kid_id=1', cookie=cookie)[1]['transactions']
        expected = bank.transactions(oracle, 1)
        for rows in (actual, expected):
            for row in rows:
                row.pop('ts')
        assert actual == expected
        gift = next(row for row in actual if row['kind'] == 'gift' and row['note'] == 'Chores')
        action('/api/admin/tx/edit', dict(tx_id=gift['id'], amount_cents=600, note='Updated chores'), lambda: bank.edit_transaction(oracle, gift['id'], 600, 'Updated chores'))
        action('/api/admin/tx/delete', dict(tx_id=gift['id']), lambda: bank.delete_transaction(oracle, gift['id']))
        action('/api/admin/kid', dict(name='Second Kid'), lambda: bank.add_kid(oracle, 'Second Kid'))
        assert len(state(runtime, cookie)['kids']) == 2
    finally:
        oracle.close()


def test_authority_isolation_csrf_and_incomplete_paths(runtime):
    assert runtime.call('/', headers={'Cookie': 'unrelated=value'})[0] == 200
    assert runtime.call('/', cookie='bod-local-session=malformed')[0] == 401
    a, csrf = runtime.parent_guest()
    b, _ = runtime.parent_guest()
    assert a != b
    assert runtime.call('/api/state')[0] == 401
    assert runtime.parent_call('/api/admin/gift', cookie=a, body=dict(kid_id=1, amount_cents=100))[0] == 403
    assert runtime.parent_call('/api/admin/gift', cookie=a, csrf=csrf, origin='https://foreign.invalid', body=dict(kid_id=1, amount_cents=100))[0] == 403
    assert runtime.parent_call('/api/admin/gift', cookie=a, csrf=csrf, origin=False, body=dict(kid_id=1, amount_cents=100))[0] == 403
    assert runtime.parent_call('/api/admin/gift', cookie=a, csrf=csrf, body=dict(kid_id=999, amount_cents=100))[0] == 404
    assert runtime.parent_call('/api/admin/gift', cookie=a, csrf=csrf, body=dict(kid_id=1, amount_cents=75))[0] == 200
    assert state(runtime, b)['kids'][0]['wallet_cents'] == 500
    assert state(runtime, a)['kids'][0]['wallet_cents'] == 575
    forged = a.split('.')[0] + '.' + '0' * 64
    assert runtime.call('/api/state', cookie=forged)[0] == 401
    # A caller cannot bootstrap a chosen family through its own header/cookie.
    assert runtime.call('/', cookie='bod-local-session=' + '1' * 32 + '.' + '2' * 64, headers={'X-Bod-Create': '1'})[0] == 401
    assert runtime.call('/auth/callback', cookie=a)[0] == 400
    assert runtime.call('/auth/login', cookie=a)[0] == 503
    assert runtime.call('/owner', cookie=a)[0] == 404
    # Guest billing cannot create a provider customer; configuration remains absent.
    # The ordinary helper follows its redirect to the unavailable sign-in route.
    for path in ('/billing/checkout', '/billing/portal', '/billing/webhook'):
        assert runtime.call(path, cookie=a, csrf=csrf, body={})[0] == 503
    assert runtime.call('/api/state', cookie=a, headers={'Host': 'foreign.invalid'})[0] == 503


def snapshot(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def test_real_sql_failure_rolls_back_and_restart_retains_bank(runtime):
    cookie, csrf = runtime.parent_guest()
    assert runtime.parent_call('/api/admin/gift', cookie=cookie, csrf=csrf, body=dict(kid_id=1, amount_cents=125))[0] == 200
    runtime.stop()
    path = runtime.database(cookie)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
    before = snapshot(path)
    runtime.start()
    assert runtime.parent_call('/api/admin/gift', cookie=cookie, csrf=csrf, body=dict(kid_id=1, amount_cents=250))[0] == 500
    assert state(runtime, cookie)['kids'][0]['wallet_cents'] == 625
    runtime.stop()
    assert snapshot(path) == before  # wallet, ledger, history, metadata and sequence
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER fail_history')
    runtime.start()
    assert state(runtime, cookie)['kids'][0]['wallet_cents'] == 625


INIT = '''(() => {
  const nativeInterval = window.setInterval;
  window.setInterval = (fn, ms, ...args) => ms === 30000 ? 0 : nativeInterval(fn, ms, ...args);
  window.__frames = []; window.__sockets = [];
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(...args) { super(...args); window.__sockets.push(this);
      this.addEventListener('message', e => window.__frames.push(e.data)); }
  };
})();'''


def test_real_frontend_two_browser_push_isolation_reconnect_and_logout(runtime):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as playwright:
        with contextlib.ExitStack() as stack:
            first = stack.enter_context(playwright.chromium.launch())
            second = stack.enter_context(playwright.chromium.launch())
            ctx = first.new_context(viewport={'width': 390, 'height': 844})
            ctx.add_init_script(INIT)
            a = ctx.new_page()
            errors = []
            a.on('pageerror', lambda error: errors.append(str(error)))
            a.goto(runtime.origin)
            expect(a.locator('#kids .big')).to_have_text('$7.00')
            a.locator('#tryGuestBank').click()
            a.locator('#tourSkip').click()
            assert a.evaluate('document.documentElement.scrollWidth <= innerWidth')
            shared = second.new_context(storage_state=ctx.storage_state())
            shared.add_init_script(INIT)
            b = shared.new_page()
            b.on('pageerror', lambda error: errors.append(str(error)))
            b.goto(runtime.origin)
            other = second.new_context()
            other.add_init_script(INIT)
            c = other.new_page()
            c.goto(runtime.origin)
            for page in (a, b, c):
                page.wait_for_function('window.__sockets.some(s => s.readyState === 1)')
            open_parent(a)
            a.locator('.gift-amt').fill('2')
            a.locator('.gift-note').fill('Browser chores')
            a.locator('.gift-btn').click()
            expect(b.locator('#kids .big')).to_have_text('$9.00', timeout=5000)
            assert b.evaluate('window.__frames.some(f => JSON.parse(f).type === "state_changed")')
            expect(c.locator('#kids .big')).to_have_text('$7.00')
            assert not c.evaluate('window.__frames.some(f => JSON.parse(f).type === "state_changed")')
            a.locator('#closeDad').click()
            a.locator('.btn-save').click()
            a.locator('.custom-amt').fill('1')
            a.locator('[data-act="save"]').click()
            b.wait_for_function('STATE.kids[0].savings_cents === 201')
            assert b.evaluate('STATE.kids[0].wallet_cents') == 600
            # Force a real socket loss and wait for an observed replacement.
            count = b.evaluate('window.__sockets.length')
            b.evaluate('window.__sockets.at(-1).close()')
            b.wait_for_function('n => window.__sockets.length > n && window.__sockets.at(-1).readyState === 1', arg=count)
            a.reload()
            assert a.locator('#guestWelcome').count() == 0
            expect(a.locator('#kids .big')).to_have_text('$9.01')
            # Log out through the existing Dad form. The other browser's idle
            # socket is revoked; the old capability cannot read any more.
            cookies = ctx.cookies()
            old = next(x for x in cookies if x['name'] == 'bod-local-session')
            open_parent(a)
            a.locator('.dad-account summary').click()
            a.locator('.account-signout button').click()
            a.wait_for_url('**/auth/signed-out')
            b.wait_for_url('**/auth/login')
            assert runtime.call('/api/state', cookie=old['name'] + '=' + old['value'])[0] == 401
            expect(c.locator('#kids .big')).to_have_text('$7.00')
            assert errors == []


def test_financial_bridge_rejects_unsafe_numbers_without_partial_writes(runtime):
    cookie, csrf = runtime.parent_guest()
    before = state(runtime, cookie)['kids'][0]
    # Individually exact amount; adding it to an existing balance is unsafe.
    code, _, _ = runtime.parent_call('/api/admin/gift', cookie=cookie, csrf=csrf,
                              body=dict(kid_id=1, amount_cents=2**53 - 1))
    assert code == 500
    after = state(runtime, cookie)['kids'][0]
    for key in ('wallet_cents', 'savings_cents', 'invest_cents', 'owned_cents'):
        assert after[key] == before[key]
    transactions = runtime.call('/api/transactions?kid_id=1', cookie=cookie)[1]['transactions']
    assert len(transactions) == 3  # unchanged sample gift/save/invest ledger


def test_socket_authorization_and_real_idle_expiry_alarm(runtime):
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus, ConnectionClosed
    cookie, csrf = runtime.parent_guest()
    url = runtime.origin.replace('http:', 'ws:') + '/api/updates'
    for candidate, origin, status in ((None, runtime.origin, 401),
                                      (cookie, 'https://foreign.invalid', 403),
                                      (cookie, 'null', 403)):
        with pytest.raises(InvalidStatus) as denied:
            with connect(url, origin=origin, additional_headers={'Cookie': candidate} if candidate else {}):
                pass
        assert denied.value.response.status_code == status
    runtime.stop()
    path = runtime.database(cookie)
    deadline = time.time() + 45
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE _bod_family SET trial_until=?', (deadline,))
    runtime.start()
    with connect(url, origin=runtime.origin, additional_headers={'Cookie': cookie}) as ws:
        assert json.loads(ws.recv(timeout=5))['type'] == 'connected'
        # No polling or writes: the persisted native alarm must revoke the
        # already-open idle connection at the real trial boundary.
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                ws.recv(timeout=50)
        assert closed.value.rcvd.code == 1008
    assert runtime.call('/api/state', cookie=cookie)[0] == 402
    assert runtime.parent_call('/api/admin/gift', cookie=cookie, csrf=csrf, body=dict(kid_id=1, amount_cents=100))[0] == 402
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT trial_until FROM _bod_family').fetchone()[0] == deadline
        assert conn.execute('SELECT wallet_cents FROM kids WHERE id=1').fetchone()[0] == 500
