"""Process isolation helpers adapted from local-only Python DO spike."""
import os, re, signal, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
WRANGLER = os.environ.get('WRANGLER_BIN', str(Path(__file__).parent / 'node_modules/.bin/wrangler'))

def group_live(pgid):
    # Linux /proc includes reparented group members after Wrangler exits. Ignore
    # zombies: they hold no listeners and only their parent can reap them.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            stat = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(stat[2]) == pgid and stat[0] != 'Z':
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def stop(p):
    if p is None:
        return
    def send(sig):
        try:
            os.killpg(p.pid, sig)  # Only the session we created, never by port/name.
        except ProcessLookupError:
            pass
    def wait_group(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            p.poll()  # Reap the leader, independently of its descendants.
            if not group_live(p.pid):
                return True
            time.sleep(.05)
        return False
    send(signal.SIGTERM)
    if not wait_group(15):
        send(signal.SIGKILL)
        if not wait_group(10):
            raise RuntimeError(f'process group {p.pid} did not exit')
    p.wait(timeout=1)

def isolated_environment(base):
    env = os.environ.copy()
    for key, path in {'HOME': base / 'home', 'XDG_CONFIG_HOME': base / 'config',
                      'XDG_CACHE_HOME': base / 'cache', 'WRANGLER_LOG_PATH': base / 'wrangler-logs',
                      'TMPDIR': base / 'tmp'}.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(WRANGLER_SEND_METRICS='false', CI='1', BANK_OF_DAD_DB=str(base / 'forbidden.db'))
    return env


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def is_bind_failure(output, port):
    # Match the pinned Wrangler's actual proxy-bind diagnostic, not arbitrary
    # user/module output mentioning a collision. Other errors must not be hidden.
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    errors = re.findall(r'^.*\[ERROR\].*$', output, re.MULTILINE)
    expected = (f'Address already in use (127.0.0.1:{port}). Please check that you are not '
                'already running a server on this address or specify a different port with --port.')
    return len(errors) == 1 and errors[0].strip() == '✘ [ERROR] ' + expected


def start_wrangler(src, base, log, env, choose_port=free_port, command=None, attempts=5):
    logpath = Path(log.name)
    diagnostics = []
    for attempt in range(attempts):
        port = choose_port()
        log.flush()
        offset = logpath.stat().st_size
        cmd = command or [WRANGLER, 'dev']
        proc = subprocess.Popen(cmd + [
            '--config', str(src / 'wrangler.jsonc'), '--local', '--ip', '127.0.0.1',
            '--port', str(port), '--inspector-ip', '127.0.0.1', '--inspector-port', '0',
            '--persist-to', str(base / 'state')], cwd=src, env=env, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                output = logpath.read_bytes()[offset:].decode(errors='replace')
                if proc.poll() is not None:
                    # Read again after exit to include all of the failed attempt.
                    output = logpath.read_bytes()[offset:].decode(errors='replace')
                    if proc.returncode == 1 and is_bind_failure(output, port):
                        break
                    raise RuntimeError(f'Wrangler exited early (status {proc.returncode}):\n{output}')
                if f'Ready on http://127.0.0.1:{port}' in output:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/read', timeout=2):
                            pass
                        return proc, port
                    except urllib.error.HTTPError:
                        return proc, port
                    except OSError:
                        pass
                time.sleep(.2)
            else:
                raise RuntimeError(f'Wrangler startup timed out:\n{output}')
        except BaseException:
            stop(proc)
            raise
        stop(proc)
        diagnostic = f'Bind collision on attempt {attempt + 1} (port {port}):\n{output}'
        diagnostics.append(diagnostic)
        print(diagnostic, file=sys.stderr, flush=True)
    raise RuntimeError('Wrangler bind collision retry limit exceeded:\n' + '\n'.join(diagnostics))
