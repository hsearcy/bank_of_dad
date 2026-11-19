"""Paid-coverage regressions over real local workerd and disposable provider HTTP."""
import copy
import sqlite3
import time

from test_live_billing import live, live_event
from test_billing import subscription, record, post, webhook, paid_invoice
import pytest
from test_oauth import client, csrf, login


def test_active_unpaid_checkout_cannot_activate(live):
    runtime, oidc, stripe = live
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'unpaid-parent'); token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        _, family = record(runtime, c)
        customer = family['customer']
        sub = subscription(customer)
        sub['livemode'] = True
        sub['items']['data'][0]['price'] = copy.deepcopy(stripe.price)
        stripe.subs[customer] = [sub]
        value = live_event(customer, 'unpaid_checkout')
        value['type'] = 'checkout.session.completed'
        value['data']['object']['payment_status'] = 'unpaid'
        assert webhook(runtime, value).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        assert any(path == '/v1/invoices' for _, path, _, _ in stripe.calls)
        # Even invoice.status=paid isn't sufficient while the payment processes.
        invoice = paid_invoice(stripe, sub, id='in_delayed')
        intent = stripe.intents['pi_in_delayed']
        intent['status'] = 'processing'
        paid = live_event(customer, 'delayed_paid')
        paid['type'] = 'invoice.paid'
        assert webhook(runtime, paid).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        intent['status'] = 'succeeded'
        paid['id'] = 'evt_settled_paid'
        assert webhook(runtime, paid).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == invoice['lines']['data'][0]['period']['end']
        assert record(runtime, c)[1]['trial_until'] == family['trial_until']
        # A partial refund revokes only paid coverage; a delayed old paid event
        # cannot restore it from event payload instead of current provider state.
        intent['latest_charge']['amount_refunded'] = 1
        refund = live_event(customer, 'partial_refund'); refund['type'] = 'charge.refunded'
        assert webhook(runtime, refund).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        paid['id'] = 'evt_old_paid_after_refund'
        assert webhook(runtime, paid).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0
        intent['latest_charge']['amount_refunded'] = 0
        intent['latest_charge']['disputed'] = True
        dispute = live_event(customer, 'disputed'); dispute['type'] = 'charge.dispute.created'
        dispute['data']['object'] = {'charge': intent['latest_charge']['id']}
        assert webhook(runtime, dispute).status_code == 200
        assert record(runtime, c)[1]['paid_until'] == 0


@pytest.mark.parametrize('catalog', ['price', 'product'])
def test_archived_catalog_portal_reconcile_and_new_checkout(live, catalog):
    runtime, oidc, stripe = live
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'archived-' + catalog); token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        _, family = record(runtime, c)
        customer = family['customer']
        end = int(time.time()) + 86400
        sub = subscription(customer, end=end)
        sub['livemode'] = True
        sub['items']['data'][0]['price'] = copy.deepcopy(stripe.price)
        stripe.subs[customer] = [sub]
        paid_invoice(stripe, sub, id='in_archived_' + catalog)
        obj = getattr(stripe, catalog)
        obj['active'] = False
        if catalog == 'price':
            sub['items']['data'][0]['price']['active'] = False
        try:
            assert post(c, runtime, '/billing/portal', token).status_code == 303
            value = live_event(customer, 'archived_paid_' + catalog); value['type'] = 'invoice.paid'
            assert webhook(runtime, value).status_code == 200
            assert record(runtime, c)[1]['paid_until'] == end
            sub['cancel_at_period_end'] = True
            sub['cancel_at'] = end
            value = live_event(customer, 'archived_cancel_scheduled_' + catalog)
            assert webhook(runtime, value).status_code == 200
            assert record(runtime, c)[1]['paid_until'] == end
            assert post(c, runtime, '/billing/refresh', token).status_code == 303
            sub['status'] = 'canceled'
            value = live_event(customer, 'archived_canceled_' + catalog)
            value['type'] = 'customer.subscription.deleted'
            assert webhook(runtime, value).status_code == 200
            assert record(runtime, c)[1]['paid_until'] == 0
            assert post(c, runtime, '/billing/portal', token).status_code == 303
            count = len(stripe.checkouts)
            assert post(c, runtime, '/billing/checkout', token).status_code == 503
            assert len(stripe.checkouts) == count
        finally:
            obj['active'] = True


def test_renewal_uses_only_paid_coverage(live):
    runtime, oidc, stripe = live
    with client(runtime) as c:
        csrf(c); login(c, oidc, 'renewal-parent'); token = csrf(c)
        assert post(c, runtime, '/billing/checkout', token).status_code == 303
        path, family = record(runtime, c)
        customer = family['customer']
        now = int(time.time()); end = now + 1000
        sub = subscription(customer, end=end)
        sub['livemode'] = True
        sub['items']['data'][0]['price'] = copy.deepcopy(stripe.price)
        stripe.subs[customer] = [sub]
        original = paid_invoice(stripe, sub, id='in_original')
        def send(name, kind='invoice.paid'):
            value = live_event(customer, name); value['type'] = kind
            assert webhook(runtime, value).status_code == 200
        send('original_paid')
        assert record(runtime, c)[1]['paid_until'] == end
        # Subscription period advances before the renewal payment settles.
        sub['items']['data'][0]['current_period_end'] = end + 1000
        sub['status'] = 'past_due'
        send('renewal_failed', 'invoice.payment_failed')
        assert record(runtime, c)[1]['paid_until'] == end
        # Once the old coverage expires, unpaid active renewal still grants zero.
        original['lines']['data'][0]['period']['end'] = now - 1
        sub['status'] = 'active'
        send('renewal_still_unpaid', 'checkout.session.async_payment_failed')
        assert record(runtime, c)[1]['paid_until'] == 0
        renewal = paid_invoice(stripe, sub, id='in_renewal')
        send('renewal_settled')
        assert record(runtime, c)[1]['paid_until'] == end + 1000
        # No foreign customer, subscription, price or mode can provide coverage.
        targets = [(renewal, 'customer', 'cus_Other'), (renewal, 'livemode', False),
            (renewal['parent']['subscription_details'], 'subscription', 'sub_Other'),
            (renewal['lines']['data'][0]['pricing']['price_details'], 'price', 'price_Other'),
            (stripe.intents['pi_in_renewal'], 'customer', 'cus_Other')]
        for target, key, wrong in targets:
            renewal['status'] = 'open'
            assert post(c, runtime, '/billing/refresh', token).status_code == 303
            assert record(runtime, c)[1]['paid_until'] == 0
            renewal['status'] = 'paid'
            saved = target[key]; target[key] = wrong
            assert post(c, runtime, '/billing/refresh', token).status_code in (303, 503)
            # Neither a failed verification nor an ignored mismatched line can
            # establish paid authorization from a previously unpaid snapshot.
            assert record(runtime, c)[1]['paid_until'] == 0
            target[key] = saved
        send('renewal_revalidated')
        with sqlite3.connect(path) as sql:
            assert sql.execute('SELECT paid_policy FROM _bod_billing').fetchone()[0] == 'invoice-v1'
        runtime.stop()
        with sqlite3.connect(path) as sql:
            sql.execute('UPDATE _bod_family SET trial_until=0')
            sql.execute('UPDATE _bod_billing SET paid_policy=NULL')
        runtime.start()
        # An old same-live-scope date is not grandfathered in as verified money.
        assert c.get('/api/state').status_code == 402
        assert c.get('/billing').status_code == 200
        assert post(c, runtime, '/billing/portal', token).status_code == 303
        assert post(c, runtime, '/billing/refresh', token).status_code == 303
        assert c.get('/api/state').status_code == 200
