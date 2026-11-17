"""Local spike controls must never be registered on the real Flask application."""
import os
import subprocess
import sys
import tempfile
import unittest


class SyntheticControlsAbsent(unittest.TestCase):
    def test_fake_routes_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env['BANK_OF_DAD_DB'] = os.path.join(tmp, 'disposable.db')
            script = '''
import app
client = app.app.test_client()
for path in ('/__test/setup', '/__test/mutate', '/__test/snapshot', '/__spike/setup', '/__spike/socket'):
    for method in ('GET', 'POST'):
        response = client.open(path, method=method, json={})
        assert response.status_code == 404, (path, method, response.status_code)
'''
            subprocess.run([sys.executable, '-c', script], env=env, check=True)
