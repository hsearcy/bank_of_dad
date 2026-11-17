"""Real local JS Worker -> Python DO websocket integration; never deploy."""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop
ROOT = Path(__file__).resolve().parent.parent

def main():
    with tempfile.TemporaryDirectory(prefix='bod-recovery-socket-') as tmp:
        base = Path(tmp); src = base/'src'; src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT/name, src/name)
        shutil.copytree(ROOT/'hosted', src/'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'tests_hosted/synthetic_recovery_socket.py',src/'worker.py')
        shutil.copyfile(ROOT/'tests_hosted/synthetic_recovery_socket_caller.js',src/'caller.js')
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'synthetic-recovery-socket','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'COMBINED','class_name':'SyntheticRecoverySocket'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['SyntheticRecoverySocket']}]}))
        (src/'caller.jsonc').write_text(json.dumps({'name':'synthetic-recovery-socket-caller','main':'caller.js','compatibility_date':'2026-09-01','durable_objects':{'bindings':[{'name':'COMBINED','class_name':'SyntheticRecoverySocket','script_name':'synthetic-recovery-socket'}]}}))
        p = None
        with (base/'wrangler.log').open('w+') as log:
            try:
                def start(): return start_wrangler(src,base,log,isolated_environment(base),command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                for restart in (False, True):
                    p, port = start()
                    env = dict(os.environ, BASE_URL=f'http://127.0.0.1:{port}', CHECKPOINT=str(base/'checkpoint'))
                    if restart: env['RESTART']='1'
                    subprocess.run(['node',str(ROOT/'tests_hosted/recovery_socket_check.cjs')],env=env,check=True,timeout=150)
                    stop(p); p=None
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text()); raise
            finally:
                stop(p)

if __name__ == '__main__': main()
