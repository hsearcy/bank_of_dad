"""Real local JS Worker -> Python DO; all data and state disposable."""
import ast, json, os, secrets, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests_hosted'))
from wrangler_helpers import WRANGLER, isolated_environment, start_wrangler, stop

def main():
    with tempfile.TemporaryDirectory(prefix='bod-pending-dashboard-') as tmp:
        base = Path(tmp); src = base / 'src'; src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(ROOT / name, src / name)
        shutil.copytree(ROOT / 'hosted', src / 'hosted', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'tests_hosted/synthetic_recovery_socket.py', src/'synthetic_recovery_socket.py')
        for name in ('worker.py', 'caller.js'):
            shutil.copyfile(ROOT/'spike/pending-dashboard'/name, src/name)
        if '--mutant' in sys.argv:
            worker = src/'worker.py'
            text = worker.read_text()
            original = "categories.get((type(e), str(e)), 'other')"
            assert text.count(original) == 1
            worker.write_text(text.replace(original, "categories.get((type(e), str(e)), 'pending')"))
        source = (ROOT/'app.py').read_text(); tree = ast.parse(source)
        cfg = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'cfg')
        (src/'dashboard_contract.py').write_text('import bank, config\n' + ast.get_source_segment(source, cfg) + '\n')
        routes = []
        for n in tree.body:
            if not isinstance(n, ast.FunctionDef): continue
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'route':
                    methods = next((k.value for k in d.keywords if k.arg == 'methods'), None)
                    if methods and 'POST' in ast.literal_eval(methods): routes.append(ast.literal_eval(d.args[0]))
        assert routes
        (src/'wrangler.jsonc').write_text(json.dumps({'name':'pending-dashboard-do','main':'worker.py','compatibility_date':'2026-09-01','compatibility_flags':['python_workers','disable_python_external_sdk'],'durable_objects':{'bindings':[{'name':'BANK','class_name':'PendingDashboard'}]},'migrations':[{'tag':'v1','new_sqlite_classes':['PendingDashboard']}]}))
        secret = secrets.token_urlsafe(32)
        (src/'caller.jsonc').write_text(json.dumps({'name':'pending-dashboard-caller','main':'caller.js','compatibility_date':'2026-09-01','vars':{'TEST_SECRET':secret},'durable_objects':{'bindings':[{'name':'BANK','class_name':'PendingDashboard','script_name':'pending-dashboard-do'}]}}))
        p = proxy = None
        with (base/'wrangler.log').open('w+') as log:
            try:
                for restart in (False, True):
                    p, port = start_wrangler(src, base, log, isolated_environment(base), command=[WRANGLER,'dev','-c',str(src/'caller.jsonc')])
                    proxy_env = dict(os.environ, INTERNAL_PORT=str(port))
                    proxy_env.pop('TEST_SECRET', None); proxy_env.pop('DRIVER_URL', None)
                    proxy = subprocess.Popen(['node', str(ROOT/'spike/pending-dashboard/dashboard-proxy.cjs')], env=proxy_env, stdout=subprocess.PIPE, text=True)
                    proxy_port = int(proxy.stdout.readline().strip())
                    env = dict(os.environ, BASE_URL=f'http://127.0.0.1:{proxy_port}', DRIVER_URL=f'http://127.0.0.1:{port}', TEST_SECRET=secret, CHECKPOINT=str(base/'checkpoint'), POST_ROUTES=json.dumps(routes))
                    if restart: env['RESTART']='1'
                    subprocess.run(['node', str(ROOT/'spike/pending-dashboard/check.cjs')], env=env, check=True, timeout=150)
                    proxy.terminate(); proxy.wait(timeout=10); proxy = None
                    stop(p); p = None
            except BaseException:
                log.flush(); print((base/'wrangler.log').read_text()); raise
            finally:
                if proxy:
                    proxy.terminate(); proxy.wait(timeout=10)
                stop(p)

if __name__ == '__main__':
    if sys.argv[1:] == ['--verify-mutant']:
        run = subprocess.run([sys.executable, __file__, '--mutant'], capture_output=True, text=True, timeout=300)
        output = run.stdout + run.stderr
        assert run.returncode != 0 and 'pending SQL negative control misclassified:' in output and 'pending_check_sql_failed' in output, output
        print('PASS classifier mutant rejected by pending SQL negative control')
    else:
        assert sys.argv[1:] in ([], ['--mutant'])
        main()
