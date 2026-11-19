"""Opt-in real Google HTTP only; no real Google account login or credentials.

BOD_TEST_GOOGLE_HTTP=1 uv run pytest -q test_google_http.py
"""
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from test_runtime import Runtime


@pytest.mark.skipif(os.environ.get('BOD_TEST_GOOGLE_HTTP') != '1', reason='explicit real Google HTTP probe only')
def test_real_google_discovery_and_invalid_code_exchange(tmp_path):
    runtime = Runtime(tmp_path, {'BOD_OIDC_CLIENT_ID': 'disposable-invalid-client.apps.googleusercontent.com',
                                 'BOD_OIDC_CLIENT_SECRET': 'disposable-invalid-secret'})
    runtime.start()
    try:
        with httpx.Client(base_url=runtime.origin, timeout=45, follow_redirects=False) as c:
            r = c.get('/auth/login')
            assert r.status_code == 302, r.text
            url = urlsplit(r.headers['location'])
            assert url.hostname == 'accounts.google.com'
            params = parse_qs(url.query)
            assert params['scope'] == ['openid profile']
            assert params['code_challenge_method'] == ['S256']
            r = c.get('/auth/callback', params={'state': params['state'][0], 'code': 'disposable-invalid-code'})
            assert r.status_code == 400
            assert 'bod-local-session' not in c.cookies
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                log = (tmp_path / 'runtime.log').read_text()
                if 'OIDC HTTP: oauth2.googleapis.com 401' in log:
                    break
                time.sleep(.1)
            assert 'OIDC HTTP: accounts.google.com 200' in log
            assert 'OIDC HTTP: oauth2.googleapis.com 401' in log
    finally:
        runtime.stop()
