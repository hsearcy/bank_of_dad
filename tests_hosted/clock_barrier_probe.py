"""Disposable Wrangler/SQLite runtime experiment; unsafe fixture, never deploy."""
import concurrent.futures
import json
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop

ROOT = Path(__file__).resolve().parent

def main():
    with tempfile.TemporaryDirectory(prefix='bod-clock-probe-') as tmp:
        base = Path(tmp); src = base / 'src'; src.mkdir()
        shutil.copy(ROOT / 'clock_barrier_worker.py', src / 'worker.py')
        shutil.copy(ROOT / 'clock_barrier_caller.js', src / 'caller.js')
        common = {'compatibility_date':'2026-09-01'}
        (src / 'wrangler.jsonc').write_text(json.dumps({**common, 'name':'bod-clock-probe', 'main':'worker.py', 'compatibility_flags':['python_workers','disable_python_external_sdk'], 'durable_objects':{'bindings':[{'name':'BANK','class_name':'ClockProbe'}, {'name':'RELAY','class_name':'PythonRelay'}, {'name':'CONTROL','class_name':'Control','script_name':'bod-clock-caller'}]}, 'migrations':[{'tag':'v1','new_sqlite_classes':['ClockProbe','PythonRelay']}]}))
        (src / 'caller.jsonc').write_text(json.dumps({**common, 'name':'bod-clock-caller','main':'caller.js', 'durable_objects':{'bindings':[{'name':'BANK','class_name':'ClockProbe','script_name':'bod-clock-probe'}, {'name':'RELAY','class_name':'PythonRelay','script_name':'bod-clock-probe'}, {'name':'CONTROL','class_name':'Control'}]}, 'migrations':[{'tag':'v1','new_sqlite_classes':['Control']}]}))
        with (base / 'wrangler.log').open('w+') as log:
            proc = None
            try:
                def start(): return start_wrangler(src, base, log, isolated_environment(base), command=[WRANGLER, 'dev', '-c', str(src / 'caller.jsonc')])
                proc, port = start()
                def call(method, **kw):
                    req = urllib.request.Request(f'http://127.0.0.1:{port}/', data=json.dumps({'method':method, **kw}, allow_nan=False).encode(), headers={'content-type':'application/json'})
                    with urllib.request.urlopen(req, timeout=40) as r: return json.load(r)['result']
                def wait_for(pred, futures=()):
                    deadline = time.monotonic() + 15
                    state = None
                    while time.monotonic() < deadline:
                        state = call('state')
                        if pred(state): return state
                        for future in futures:
                            if future.done(): raise AssertionError(('unexpected completion', future.result(), state))
                        time.sleep(.03)
                    raise AssertionError(('timeout', state))
                # Negative control: with the gate disabled the same runtime must
                # deliver a newer request during the older request's barrier pause,
                # and the stale action then wrongly succeeds. This demonstrates
                # that the test can detect the race, rather than relying on sleep.
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    stale = pool.submit(call, 'run', family='negative', now=1009, hold=True, ungated=True)
                    wait_for(lambda s:s['held'], (stale,))
                    newer = pool.submit(call, 'run', family='negative', now=1010, ungated=True)
                    assert newer.result(timeout=15) == 'denied'
                    assert not stale.done()
                    call('release')
                    assert stale.result() == 'allowed'
                    assert call('snapshot', family='negative') == {'clock':[1010], 'actions':[1009]}
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    first = pool.submit(call, 'run', family='race', now=1009, hold=True)
                    wait_for(lambda s:s['held'], (first,))
                    before = call('state')
                    second = pool.submit(call, 'run', family='race', now=1010)
                    state = wait_for(lambda s:len(s['starts']) > len(before['starts']), (first, second))
                    assert len(state['entries']) == len(before['entries']), state
                    assert call('run', family='independent', now=1009) == 'allowed'
                    assert call('snapshot', family='independent') == {'clock':[1009], 'actions':[1009]}
                    assert not first.done() and not second.done()
                    call('release')
                    assert first.result() == 'allowed' and second.result() == 'denied'
                    assert call('snapshot', family='race') == {'clock':[1010], 'actions':[1009]}
                # Reverse ordering: a newer observation precedes an already
                # submitted older action, which must not become authorized.
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    new = pool.submit(call, 'run', family='reverse', now=1010, hold=True)
                    wait_for(lambda s:s['held'], (new,))
                    before = call('state')
                    old = pool.submit(call, 'python', family='reverse', now=1009)
                    state = wait_for(lambda s:len(s['starts']) > len(before['starts']), (new, old))
                    assert len(state['entries']) == len(before['entries']), state
                    call('release')
                    assert new.result() == 'denied' and old.result() == 'rollback'
                    assert call('snapshot', family='reverse') == {'clock':[1010], 'actions':[]}
                assert call('python', family='race', now=1009) == 'rollback'
                assert call('snapshot', family='race') == {'clock':[1010], 'actions':[1009]}
                assert call('python', family='failure', now=1009, fail='after_sync') == 'injected_action_failure'
                assert call('snapshot', family='failure') == {'clock':[1009], 'actions':[]}
                assert call('run', family='barrier', now=1009, fail='before_sync') == 'injected_barrier_failure'
                assert call('snapshot', family='barrier')['actions'] == []
                assert call('run', family='tx-fail', now=1009, fail='watermark_tx') == 'runtime_error'
                assert call('snapshot', family='tx-fail') == {'clock':[], 'actions':[]}
                assert call('run', family='tx-fail', now=1009, fail='action_tx') == 'runtime_error'
                assert call('snapshot', family='tx-fail') == {'clock':[1009], 'actions':[]}
                for invalid in [None, True, -1, 2001, 1.5, 'x']:
                    assert call('run', family='invalid', now=invalid) == 'invalid'
                for special in ['nan', 'infinity']:
                    assert call('run', family='invalid', special=special) == 'invalid'
                assert call('snapshot', family='invalid') == {'clock':[], 'actions':[]}
                assert call('run', family='future', now=1500) == 'denied'
                assert call('run', family='future', now=1009) == 'rollback'
                stop(proc); proc = None
                proc, port = start()
                assert call('python', family='race', now=1009) == 'rollback'
                assert call('snapshot', family='race') == {'clock':[1010], 'actions':[1009]}
                assert call('snapshot', family='failure') == {'clock':[1009], 'actions':[]}
                print('PASS: controlled JS overlap, Python RPC, restart, faults, clock validation and independent family')
            except BaseException:
                log.flush(); print(Path(log.name).read_text(), flush=True)
                raise
            finally: stop(proc)

if __name__ == '__main__': main()
