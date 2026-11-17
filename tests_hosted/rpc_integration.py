"""Actual local Python DO -> DO RPC, disposable state only."""
import json
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from wrangler_helpers import isolated_environment, start_wrangler, stop

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='bod-rpc-') as temp:
    base = Path(temp); src = base / 'src'; src.mkdir()
    for f in ('bank.py', 'db.py', 'config.py'): shutil.copyfile(ROOT / f, src / f)
    shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(ROOT / 'tests_hosted/synthetic_rpc_worker.py', src / 'worker.py')
    (src / 'wrangler.jsonc').write_text(json.dumps({'name':'bod-rpc-synthetic','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticRPC'}, {'name':'CALLER','class_name':'SyntheticCaller'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticRPC', 'SyntheticCaller']}]}))
    p = None
    with (base / 'wrangler.log').open('w+') as log:
        try:
            p, port = start_wrangler(src, base, log, isolated_environment(base))
            def call(action, token='', status=200):
                q = urllib.parse.urlencode({'action': action, 'token': token})
                try: r = urllib.request.urlopen(f'http://127.0.0.1:{port}/?{q}', timeout=45)
                except urllib.error.HTTPError as e: r = e
                with r:
                    body = json.load(r)
                    assert r.status == status, (action, r.status, body)
                    return body
            token = call('setup')['result']
            assert call('mutate', token)['result'] is True
            assert call('read', token)['result'][0]['name'] == 'RPC Kid'
            before = call('snapshot')['result']
            for action, value, typ, message in [
                ('read', 'bad', 'JsException', 'PermissionError: denied'),
                ('mutate', 'bad', 'JsException', 'PermissionError: denied'),
                ('wrong_family', token, 'JsException', 'PermissionError: denied'),
                ('raw', '', 'JsException', "TypeError: FamilyBank.mutate() missing 2 required positional arguments"),
                ('privileged', '', 'JsException', 'TypeError: Method issue_grant_privileged does not exist'),
                ('private', token, 'JsException', 'TypeError: Method _authorization does not exist'),
            ]:
                error = call(action, value, 500)
                assert error['type'] == typ and message in error['error'], (action, error)
                assert call('snapshot')['result'] == before, action
            assert len(call('read', token)['result']) == 1
            print('PASS synthetic cross-DO RPC: serialization, authorization and method exposure')
        except BaseException:
            log.flush(); print((base / 'wrangler.log').read_text()); raise
        finally: stop(p)
