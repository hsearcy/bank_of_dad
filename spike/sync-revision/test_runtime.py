"""Local-only actual mixed-language Durable Object revision checks."""
import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests_hosted'))
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop

# Extra controls exist only in this disposable test module, never the fixture.
TEST_WORKER = '''\nfrom worker import SyntheticLiveBank\nfrom hosted.object import _rpc_result\nclass TestBank(SyntheticLiveBank):\n    async def fixture_fault(self, family, token, operation, action, args, stage):\n        return _rpc_result(self._revision.mutate(family, token, operation, action, args, fault=stage))\n    async def fixture_mature_zero(self, family, token):\n        import time\n        def op(c):\n            self._authorization._auth(c, family, token)\n            past = int(time.time()) - 1\n            c.execute('UPDATE bonds SET buy_ts=?, mature_ts=?, payout_cents=principal_cents WHERE kid_id=1', (past, past))\n        self._authorization.bank._transaction(op)\n        return _rpc_result(True)\n    async def fixture_bootstrap_fault(self, family):\n        from worker import OWNER\n        self._revision.bootstrap_fresh(family, OWNER, fault=True)\n    async def fixture_break_schema(self):\n        self._authorization.bank._transaction(lambda c: c.execute('CREATE INDEX synthetic_extra ON kids(name)'))\n        return _rpc_result(True)\n    async def fixture_revoke(self, family, token):\n        self._authorization.revoke(family, token)\n        return _rpc_result(True)\n    async def fixture_corrupt(self):\n        self._authorization.bank._transaction(lambda c: c.execute('UPDATE fixture_revision SET version=2'))\n        return _rpc_result(True)\n'''

def main():
    with tempfile.TemporaryDirectory(prefix='bod-revision-do-') as tmp:
        base = Path(tmp); src = base / 'src'; src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT / name, src / name)
        shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'spike/actual-ui/bank_worker.py', src / 'worker.py')
        shutil.copyfile(ROOT / 'spike/sync-revision/revision.py', src / 'revision.py')
        shutil.copyfile(ROOT / 'spike/sync-revision/runtime-caller.js', src / 'caller.js')
        (src / 'test_worker.py').write_text(TEST_WORKER)
        app = (ROOT / 'app.py').read_text(); tree = ast.parse(app)
        cfg = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'cfg')
        (src / 'dashboard_contract.py').write_text('import bank, config\n' + ast.get_source_segment(app, cfg) + '\n')
        (src / 'wrangler.jsonc').write_text(json.dumps({'name':'bod-revision-object','main':'test_worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'BANK','class_name':'TestBank'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['TestBank']}]}))
        (src / 'caller.jsonc').write_text(json.dumps({'name':'bod-revision-driver','main':'caller.js','compatibility_date':'2026-09-01','durable_objects':{'bindings':[{'name':'BANK','class_name':'TestBank','script_name':'bod-revision-object'}]}}))
        env = isolated_environment(base); proc = None
        with (base / 'wrangler.log').open('w+') as log:
            try:
                proc, port = start_wrangler(src, base, log, env, command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                env['BASE_URL'] = f'http://127.0.0.1:{port}'
                env['CHECKPOINT'] = str(base/'checkpoint.json')
                subprocess.run(['node', str(ROOT/'spike/sync-revision/runtime-check.cjs')], env=env, check=True, timeout=120)
                stop(proc); proc = None
                proc, port = start_wrangler(src, base, log, env, command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                env['BASE_URL'] = f'http://127.0.0.1:{port}'
                env['RESTART_CHECK'] = '1'
                subprocess.run(['node', str(ROOT/'spike/sync-revision/runtime-check.cjs')], env=env, check=True, timeout=120)
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text(), file=sys.stderr); raise
            finally:
                stop(proc)
        if (base/'forbidden.db').exists(): raise RuntimeError('unexpected self-host database')
    print('Temporary runtime state and processes removed.')

if __name__ == '__main__': main()
