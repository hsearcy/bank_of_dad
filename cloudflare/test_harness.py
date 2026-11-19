"""Deterministic lifecycle regressions; no workerd/provider startup needed."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from test_runtime import Runtime


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('child lifecycle synchronization timed out')


@pytest.mark.parametrize('leader_exits_first', [False, True])
def test_stop_waits_for_owned_descendant_even_after_leader_exit(tmp_path, leader_exits_first):
    ready, stopped = tmp_path / 'ready', tmp_path / 'stopped'
    child = '''import signal, time, pathlib
ready, stopped = map(pathlib.Path, __import__('sys').argv[1:])
def stop(*_):
    time.sleep(.3)
    stopped.write_text('cleaned up')
    raise SystemExit
signal.signal(signal.SIGTERM, stop)
ready.write_text(str(__import__('os').getpid()))
while True: time.sleep(.01)
'''
    leader = '''import subprocess, sys, time
subprocess.Popen([sys.executable, '-c', sys.argv[1], *sys.argv[2:4]])
if sys.argv[4] == 'False':
    while True: time.sleep(.01)
'''
    runtime = Runtime(tmp_path)
    runtime.log = (tmp_path / 'runtime.log').open('a+')
    proc = subprocess.Popen([sys.executable, '-c', leader, child, str(ready), str(stopped),
                             str(leader_exits_first)], start_new_session=True,
                            stdout=runtime.log, stderr=subprocess.STDOUT)
    runtime.proc = proc
    try:
        wait_for(ready.exists)
        if leader_exits_first:
            proc.wait(timeout=5)
        runtime.stop()
        assert stopped.exists(), 'stop returned before descendant cleanup completed'
        # A zombie has no resources/listeners; it may await host-init reaping.
        stat = Path('/proc') / ready.read_text() / 'stat'
        assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
        runtime.log.close()
