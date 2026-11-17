"""Run only on disposable local Wrangler storage; no Cloudflare deployment."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests_hosted'))
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop

def main():
    with tempfile.TemporaryDirectory(prefix='bod-live-sync-') as tmp:
        base=Path(tmp); src=base/'src'; src.mkdir()
        for name in ('bank.py','db.py','config.py'):
            shutil.copyfile(ROOT/name,src/name)
        shutil.copytree(ROOT/'hosted',src/'hosted',ignore=shutil.ignore_patterns('__pycache__'))
        for source,target in [('bank_worker.py','worker.py'),('caller.js','caller.js')]:
            shutil.copyfile(ROOT/'spike/live-sync'/source,src/target)
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'bod-live-synthetic-bank','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticLiveBank'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticLiveBank']}]}))
        (src/'caller.jsonc').write_text(json.dumps({'name':'bod-live-synthetic-ui','main':'caller.js','compatibility_date':'2026-09-01','durable_objects':{'bindings':[{'name':'BANK','class_name':'SyntheticLiveBank','script_name':'bod-live-synthetic-bank'}]}}))
        p=None
        with (base/'wrangler.log').open('w+') as log:
            try:
                env=isolated_environment(base)
                p,port=start_wrangler(src,base,log,env,command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                env['BASE_URL']=f'http://127.0.0.1:{port}'
                if '--serve' in sys.argv:
                    print(f'SYNTHETIC ONLY: {env["BASE_URL"]} (Ctrl-C to stop and delete state)', flush=True)
                    p.wait()
                else:
                    subprocess.run(['node',str(ROOT/'spike/live-sync/browser.cjs')],env=env,check=True,timeout=120)
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text(),flush=True); raise
            finally: stop(p)
if __name__=='__main__': main()
