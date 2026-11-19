"""Dashboard release wording through real isolated local workerd; no providers."""
import pytest

from test_runtime import Runtime
from test_public import PublicRuntime
from test_live_billing import PUBLIC_LIVE


@pytest.mark.parametrize('mode', ['local', 'public-pilot', 'public-live'])
def test_preview_banner_matches_runtime_mode(tmp_path, mode):
    if mode == 'local':
        server = Runtime(tmp_path)
    else:
        server = PublicRuntime(tmp_path, PUBLIC_LIVE if mode == 'public-live' else {})
    server.start()
    try:
        code, html, headers = server.call('/')
        assert code == 200, html
        assert 'id="kids"' in html
        assert 'Sample Kid' in html
        assert ('Local Cloudflare preview:' in html) == (mode == 'local')
        assert ('Do not use real family data.' in html) == (mode == 'local')
        assert ('Private pilot: payments are unavailable.' in html) == (mode == 'public-pilot')
        cookie = headers['Set-Cookie'].split(';')[0]
        assert server.call('/api/state', cookie=cookie)[0] == 200
    finally:
        server.stop()
