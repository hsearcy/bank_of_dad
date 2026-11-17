"""Run the actual Flask UI only with a temporary, empty self-host database."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory(prefix='bod-normal-ui-') as tmp:
        env = os.environ.copy()
        env['BANK_OF_DAD_DB'] = str(Path(tmp) / 'bank.db')
        # The server binds its own ephemeral loopback port; no production listener.
        code = ("import app; from werkzeug.serving import make_server; "
                "s=make_server('127.0.0.1', 0, app.app); "
                "print(s.server_port, flush=True); s.serve_forever()")
        server = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=ROOT,
                                  env=env, stdout=subprocess.PIPE, text=True)
        try:
            assert server.stdout is not None
            port = server.stdout.readline().strip()
            if not port.isdigit():
                raise RuntimeError('Flask failed to start')
            env['BASE_URL'] = f'http://127.0.0.1:{port}'
            subprocess.run(['node', str(ROOT/'spike/actual-ui/normal_browser.cjs')],
                           cwd=ROOT, env=env, check=True, timeout=90)
        finally:
            server.terminate()
            server.wait(timeout=10)
    print('Teardown: disposable Flask server stopped and database removed.')


if __name__ == '__main__':
    main()
