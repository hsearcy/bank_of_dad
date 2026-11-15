"""A fresh installation starts empty and can be configured locally."""
import os
import subprocess
import sys
import tempfile
import unittest


class OnboardingTests(unittest.TestCase):
    def test_empty_database_pin_and_add_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["BANK_OF_DAD_DB"] = os.path.join(tmp, "fresh.db")
            script = '''
import app
client = app.app.test_client()
assert client.get('/').status_code == 200
assert client.get('/api/state').get_json()['kids'] == []
assert client.get('/api/admin/status').get_json()['pin_set'] is False
assert client.post('/api/admin/pin', json={'new_pin': '1234'}).get_json()['ok']
assert client.post('/api/admin/kid', json={'name': 'Sample'}).status_code == 403
r = client.post('/api/admin/kid', json={'name': 'Sample'}, headers={'X-Dad-Pin': '1234'})
assert r.get_json()['ok']
assert [k['name'] for k in client.get('/api/state').get_json()['kids']] == ['Sample']
'''
            subprocess.run([sys.executable, "-c", script], env=env, check=True)


if __name__ == "__main__":
    unittest.main()
