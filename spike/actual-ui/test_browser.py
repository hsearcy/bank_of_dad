"""Run only on disposable local Wrangler storage; no Cloudflare deployment."""
import ast, json, os, secrets, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests_hosted'))
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop

def main():
    with tempfile.TemporaryDirectory(prefix='bod-actual-ui-') as tmp:
        base=Path(tmp); src=base/'src'; src.mkdir()
        for name in ('bank.py','db.py','config.py'):
            shutil.copyfile(ROOT/name,src/name)
        shutil.copytree(ROOT/'hosted',src/'hosted',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'spike/sync-revision/revision.py', src/'revision.py')
        for source,target in [('bank_worker.py','worker.py'),('caller.js','caller.js')]:
            shutil.copyfile(ROOT/'spike/actual-ui'/source,src/target)
        # Reuse Flask's exact pure config contract without importing app.py,
        # whose module initialization opens a self-host database.
        tree = ast.parse((ROOT/'app.py').read_text())
        cfg = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'cfg')
        (src/'dashboard_contract.py').write_text('import bank, config\n' + ast.get_source_segment((ROOT/'app.py').read_text(), cfg) + '\n')
        assets = src/'assets'; assets.mkdir()
        shutil.copytree(ROOT/'static', assets/'static')
        shutil.copyfile(ROOT/'templates/index.html', assets/'index.html')
        shutil.copyfile(ROOT/'spike/actual-ui/synthetic.js', assets/'static/synthetic.js')
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'bod-actual-synthetic-bank','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticLiveBank'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticLiveBank']}]}))
        (src/'caller.jsonc').write_text(json.dumps({'name':'bod-actual-synthetic-ui','main':'caller.js','compatibility_date':'2026-09-01','assets':{'directory':'assets','html_handling':'none','binding':'ASSETS','run_worker_first':True},'durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticLiveBank','script_name':'bod-actual-synthetic-bank'}]}}))
        p=None; proxy=None
        with (base/'wrangler.log').open('w+') as log:
            try:
                env=isolated_environment(base)
                secret=secrets.token_hex(32)
                caller=json.loads((src/'caller.jsonc').read_text()); caller['vars']={'TEST_SECRET':secret}
                (src/'caller.jsonc').write_text(json.dumps(caller))
                p,port=start_wrangler(src,base,log,env,command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                env['INTERNAL_PORT']=str(port)
                env['DRIVER_URL']=f'http://127.0.0.1:{port}'
                env['TEST_SECRET']=secret
                proxy_env=env.copy()
                proxy_env.pop('TEST_SECRET'); proxy_env.pop('DRIVER_URL')
                proxy=subprocess.Popen(['node',str(ROOT/'spike/actual-ui/dashboard-proxy.cjs')],env=proxy_env,stdout=subprocess.PIPE,text=True)
                proxy_port=int(proxy.stdout.readline().strip())
                env['BASE_URL']=f'http://127.0.0.1:{proxy_port}'
                if '--serve' in sys.argv:
                    print(f'SYNTHETIC ONLY: {env["BASE_URL"]} (Ctrl-C to stop and delete state)', flush=True)
                    p.wait()
                else:
                    subprocess.run(['node',str(ROOT/'spike/actual-ui/browser.cjs')],env=env,check=True,timeout=120)
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text(),flush=True); raise
            finally:
                if proxy:
                    proxy.terminate(); proxy.wait(timeout=10)
                stop(p)
                if (base/'forbidden.db').exists():
                    raise RuntimeError('unexpected self-host DB created')
    if base.exists():
        raise RuntimeError('temporary state survived teardown')
    print('Teardown: owned Wrangler process group stopped; temporary source/state removed; no self-host DB created.')
if __name__=='__main__': main()
