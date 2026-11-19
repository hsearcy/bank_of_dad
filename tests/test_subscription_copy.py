"""Copy contract across hosted states; no database or providers required."""
from html import unescape
from pathlib import Path
import re

from flask import Flask, render_template
import pytest

ROOT = Path(__file__).resolve().parents[1]
FOUNDER = ("I built this just for my own kids, but friends kept asking if they could use it so I finally gave in and put it up here. "
           "If you're enjoying it, your small yearly contribution will help me pay server costs and do maintenance.")
FREE = 'If you need free access for your family, let me know and we can work something out.'
OPEN_SOURCE = 'The Bank of Dad is also Open Source if you want to run it yourself for free!'


def render_account(kind='trial', guest=False, pilot=False, customer=False, support=True, device_bank=False):
    app = Flask(__name__, template_folder=str(ROOT / 'templates'))
    app.secret_key = 'disposable-copy-tests'
    with app.test_request_context('/'):
        from flask import session
        session['device_bank'] = device_bank
        return render_template('hosted_account.html', hosted_account=dict(kind=kind, days_left=31, until_label='December 20'),
                               hosted_guest=guest, public_pilot=pilot, hosted_family=dict(id='a' * 32, customer=customer),
                               support_url='mailto:support@thebankofdad.app' if support else None)


def text(html):
    return ' '.join(unescape(re.sub('<[^>]+>', '', html)).split())


@pytest.mark.parametrize('kind', ['trial', 'expired', 'paid', 'free'])
@pytest.mark.parametrize('guest', [False, True])
@pytest.mark.parametrize('pilot', [False, True])
@pytest.mark.parametrize('customer', [False, True])
def test_subscription_copy_and_existing_state_distinctions(kind, guest, pilot, customer):
    html = render_account(kind, guest, pilot, customer)
    copy = text(html)
    assert FREE in copy
    assert 'Please include your Family ID when you get in touch.' in copy
    assert 'Family ID: ' + 'a' * 32 in copy
    assert 'href="mailto:support@thebankofdad.app"' in html
    assert OPEN_SOURCE in copy
    assert re.search(r'href="https://github.com/hsearcy/bank_of_dad"[^>]*>run it yourself for free</a>!', html)
    assert 'No card needed, and no automatic charge when it ends.' not in copy
    assert 'free week' not in copy and '7-day trial' not in copy
    show_price = kind in ('trial', 'expired') and not pilot
    assert (FOUNDER in copy) == show_price
    assert ('account-price' in html) == show_price
    if show_price:
        assert html.index('account-contribution') < html.index('account-price')
        assert 'Checkout starts a paid annual subscription' in copy
    assert ('action="/billing/checkout"' in html) == (show_price and not guest)
    assert ('action="/billing/portal"' in html) == (customer and not pilot)
    assert ('Save this bank / sign in' in copy) == guest
    if kind == 'trial':
        assert 'Your free trial ends December 20.' in copy
    if pilot:
        assert 'Private pilot — payments unavailable.' in copy
        assert '31-day trial' in copy
    if kind == 'free' and customer:
        assert 'An existing Stripe subscription is separate' in copy


def test_unconfigured_contact_does_not_invent_destination():
    html = render_account(support=False)
    assert 'A contact link hasn’t been set up yet.' in html
    assert 'mailto:' not in html


def test_separate_device_trial_notice_and_return_action_are_preserved():
    html = render_account(device_bank=True)
    assert 'Your separate device trial is still here. Nothing was imported into this family.' in html
    assert 'action="/auth/device-bank"' in html


def test_visible_trial_language_and_no_automatic_charge_disclosure():
    paths = [p for folder in ('templates', 'static') for p in (ROOT / folder).rglob('*')
             if p.suffix in ('.html', '.js', '.css')]
    paths += list(ROOT.glob('*.py'))
    for path in paths:
        assert not re.search(r'(?:free week|for a week|(?:7|seven).day trial|trial lasts 7 days)', path.read_text(), re.I), path
    welcome = (ROOT / 'templates/index.html').read_text()
    assert 'Try it for 31 days on this device.' in welcome
    assert 'No automatic charge.' in welcome
