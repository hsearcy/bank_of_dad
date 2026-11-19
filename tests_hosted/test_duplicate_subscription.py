"""Regression: Checkout expiry must not hide a still-retrying subscription."""
import time

import pytest

from test_hosted import hosted_app, login, post, subscription


@pytest.mark.parametrize('status', ['past_due', 'unpaid', 'incomplete', 'trialing', 'paused',
                                   'canceled', 'incomplete_expired'])
def test_expired_checkout_checks_current_subscriptions(hosted_app, status):
    app, host, state, origin = hosted_app
    client = app.test_client()
    assert login(client, state, origin).status_code == 302
    assert post(client, '/billing/checkout', origin).status_code == 303
    with host.connect() as conn:
        conn.execute('UPDATE families SET checkout_expires=?', (time.time() - 86400,))
    state['checkout_status'] = 'expired'
    state['subscriptions']['cus_1'] = [subscription(status=status)]
    result = post(client, '/billing/checkout', origin)
    assert result.status_code == 303
    if status in ('canceled', 'incomplete_expired'):
        assert result.location == 'https://checkout.stripe.com/c/pay/synthetic'
        assert len(state['checkouts']) == 2
        assert state['portals'] == []
    else:
        assert result.location == 'https://billing.stripe.com/synthetic'
        assert len(state['checkouts']) == 1
        assert state['portals'] == [{'customer': 'cus_1', 'return_url': origin + '/billing'}]
