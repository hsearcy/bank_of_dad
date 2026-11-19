"""Fail-closed ingress configuration shared by Worker and binding-only DOs."""
import re
from urllib.parse import urlsplit

LOOPBACK = ('127.0.0.1', 'localhost', '::1')
TEST_OVERRIDES = ('BOD_TEST_OIDC_ORIGIN', 'BOD_TEST_STRIPE_ORIGIN')


def runtime_mode(env):
    origin = str(getattr(env, 'BOD_PUBLIC_ORIGIN', ''))
    parts = urlsplit(origin)
    local = str(getattr(env, 'BOD_LOCAL_ONLY', ''))
    if (not parts.netloc or parts.path or parts.query or parts.fragment
            or parts.username or parts.password or origin != parts.scheme + '://' + parts.netloc):
        raise ValueError('An exact origin without a path is required')
    if local == 'true' and parts.scheme == 'http' and parts.hostname in LOOPBACK:
        return 'local'
    if (local != 'false' or parts.scheme != 'https' or parts.hostname in LOOPBACK
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', parts.netloc)
            or '.' not in parts.netloc):
        raise ValueError('An explicit public HTTPS origin is required')
    if any(str(getattr(env, name, '')) for name in TEST_OVERRIDES):
        raise ValueError('Test providers are forbidden on public ingress')
    mode = str(getattr(env, 'BOD_BILLING_MODE', 'unavailable'))
    if mode == 'live':
        if str(getattr(env, 'BOD_PUBLIC_PILOT', '')) != 'false':
            raise ValueError('Live billing requires explicit non-pilot configuration')
        stripe_config(env, public=True)
    elif (mode != 'unavailable' or str(getattr(env, 'BOD_PUBLIC_PILOT', '')) != 'true'
            or any(str(getattr(env, name, '')) for name in (
                'BOD_STRIPE_SECRET_KEY', 'BOD_STRIPE_WEBHOOK_SECRET',
                'BOD_STRIPE_PRICE_ID', 'BOD_STRIPE_LIVEMODE'))):
        raise ValueError('Public sandbox billing is forbidden')
    return 'public'


def stripe_config(env, public=False):
    """Explicit operator bindings only; never process env, requests or defaults."""
    mode = str(getattr(env, 'BOD_BILLING_MODE', 'unavailable'))
    live = mode == 'live'
    key = str(getattr(env, 'BOD_STRIPE_SECRET_KEY', ''))
    price = str(getattr(env, 'BOD_STRIPE_PRICE_ID', ''))
    secret = str(getattr(env, 'BOD_STRIPE_WEBHOOK_SECRET', ''))
    account = str(getattr(env, 'BOD_STRIPE_ACCOUNT_ID', ''))
    product = str(getattr(env, 'BOD_STRIPE_PRODUCT_ID', ''))
    if (mode not in ('test', 'live') or (public and not live)
            or str(getattr(env, 'BOD_STRIPE_LIVEMODE', '')) != ('true' if live else 'false')
            or not key.startswith(('sk_live_', 'rk_live_') if live else ('sk_test_', 'rk_test_'))
            or not re.fullmatch(r'price_[A-Za-z0-9_]+', price)
            or not secret.startswith('whsec_')
            or (live and (not re.fullmatch(r'acct_[A-Za-z0-9]+', account)
                          or not re.fullmatch(r'prod_[A-Za-z0-9]+', product)))):
        raise ValueError('Explicit matching Stripe billing configuration required')
    return dict(mode=mode, live=live, key=key, price=price, secret=secret,
                account=account, product=product)


def ingress_status(env, request):
    try:
        runtime_mode(env)
    except (ValueError, TypeError):
        return 503
    expected = str(env.BOD_PUBLIC_ORIGIN)
    actual = urlsplit(str(request.url))
    # The Cloudflare request URL and Host are authoritative. Never trust forwarded
    # host/proto/origin headers, even when running behind the local TLS emulator.
    if (actual.scheme + '://' + actual.netloc != expected
            or request.headers.get('Host') != urlsplit(expected).netloc):
        return 503
    origin = request.headers.get('Origin')
    if origin is not None and origin != expected:
        return 403
    if (request.method not in ('GET', 'HEAD', 'OPTIONS')
            and actual.path != '/billing/webhook' and origin != expected):
        return 403
    return 0


def cookie_header(env, name, value, age):
    secure = '; Secure' if runtime_mode(env) == 'public' else ''
    return f'{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}'
