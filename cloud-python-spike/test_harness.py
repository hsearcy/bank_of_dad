"""Regression checks for the local-only Wrangler test harness."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
import test_spike as harness


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bod-harness-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.src = self.base / 'src'
        self.src.mkdir()
        for name in ('bank.py', 'db.py', 'config.py'):
            shutil.copyfile(harness.ROOT / name, self.src / name)
        for name in ('worker.py', 'fixtures.py', 'scenario.py', 'wrangler.jsonc'):
            shutil.copyfile(harness.HERE / name, self.src / name)
        self.env = harness.isolated_environment(self.base)

    def test_environment_overrides_inherited_paths(self):
        with mock.patch.dict(os.environ, {'HOME': '/outside', 'XDG_CONFIG_HOME': '/outside',
                'XDG_CACHE_HOME': '/outside', 'WRANGLER_LOG_PATH': '/outside', 'TMPDIR': '/outside'}):
            env = harness.isolated_environment(self.base)
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'WRANGLER_LOG_PATH', 'TMPDIR'):
            self.assertTrue(Path(env[key]).is_relative_to(self.base), (key, env[key]))

    def test_real_bind_conflict_retries_and_preserves_diagnostic(self):
        with socket.socket() as held, (self.base / 'run.log').open('w+') as log:
            held.bind(('127.0.0.1', 0))
            held.listen()
            occupied = held.getsockname()[1]
            ports = iter([occupied, harness.free_port()])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                proc, port = harness.start_wrangler(self.src, self.base, log, self.env, choose_port=lambda: next(ports))
            self.addCleanup(harness.stop, proc)
            self.assertNotEqual(port, occupied)
            self.assertIn(f'Address already in use (127.0.0.1:{occupied})', stderr.getvalue())
            self.assertTrue(list((self.base / 'wrangler-logs').glob('*.log')))
            log.flush()
            self.assertIn('wrangler-logs', (self.base / 'run.log').read_text())

    def test_retry_limit_retains_each_real_collision(self):
        with socket.socket() as held, (self.base / 'run.log').open('w+') as log:
            held.bind(('127.0.0.1', 0))
            held.listen()
            port = held.getsockname()[1]
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(RuntimeError) as failure:
                    harness.start_wrangler(self.src, self.base, log, self.env,
                        choose_port=lambda: port, attempts=2)
            text = str(failure.exception)
            self.assertIn('retry limit exceeded', text)
            self.assertEqual(text.count(f'Address already in use (127.0.0.1:{port})'), 2)
            self.assertIn('attempt 1', text)
            self.assertIn('attempt 2', text)

    def test_unrelated_collision_text_does_not_mask_real_error(self):
        fake = self.base / 'fake.py'
        fake.write_text("import sys\np = sys.argv[sys.argv.index('--port') + 1]\nprint(f'✘ [ERROR] Address already in use (127.0.0.1:{p}). Please check that you are not already running a server on this address or specify a different port with --port.')\nprint('✘ [ERROR] genuine unrelated failure')\nsys.exit(7)\n")
        with (self.base / 'run.log').open('w+') as log:
            with self.assertRaisesRegex(RuntimeError, 'genuine unrelated failure') as failure:
                harness.start_wrangler(self.src, self.base, log, self.env,
                    command=[sys.executable, str(fake)])
        self.assertIn('status 7', str(failure.exception))
        self.assertEqual((self.base / 'run.log').read_text().count('genuine unrelated failure'), 1)

    def test_readme_parallel_example_propagates_failure_and_waits(self):
        readme = (harness.HERE / 'README.md').read_text()
        example = readme.split('To repeat the parallel full-suite check', 1)[1].split('```sh\n', 1)[1].split('```', 1)[0]
        bin_dir = self.base / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'python3'
        fake.write_text('#!/bin/sh\nif mkdir "$TEST_DIR/first" 2>/dev/null; then exit 7; fi\nsleep 0.2\ntouch "$TEST_DIR/waited"\n')
        fake.chmod(0o755)
        env = self.env | {'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'TEST_DIR': str(self.base)}
        result = subprocess.run(['sh', '-c', example], env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.base / 'waited').exists())
        self.assertNotIn('round 2:', result.stdout)


if __name__ == '__main__':
    unittest.main()
