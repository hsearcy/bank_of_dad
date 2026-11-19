"""Short welcome and unchanged onboarding on disposable workerd families."""
import pytest
from test_runtime import runtime


@pytest.mark.parametrize('width', [390, 1280])
def test_short_welcome_preserves_trial_sample_and_skip(runtime, width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw, pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox']) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 844}, service_workers='block', reduced_motion='reduce')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(runtime.origin)
        welcome = page.locator('#guestWelcome')
        expect(welcome).to_be_visible()
        expect(page.locator('#guestWelcomeTitle')).to_be_focused()
        text = welcome.inner_text()
        for removed in ('Sample Kid has', 'Try the controls', 'The free trial lasts 31 days. No automatic charge.'):
            assert removed not in text
        for retained in ('my kids', '31 days', 'No sign-up or card needed.', 'clearing browser data', 'free access', 'tablet', 'Self-host for free'):
            assert retained in text
        assert welcome.evaluate('(el) => el.scrollHeight <= el.clientHeight')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#tryGuestBank').evaluate('(el) => el.getBoundingClientRect().bottom <= innerHeight')
        page.keyboard.press('Tab')
        expect(welcome.locator('a').first).to_be_focused()
        page.locator('#tryGuestBank').click()
        expect(page.locator('#productTour')).to_be_visible()
        page.locator('#tourSkip').click()
        expect(page.locator('#productTour')).not_to_be_visible()
        assert page.evaluate('[STATE.kids[0].name, STATE.kids[0].wallet_cents, STATE.kids[0].savings_cents, STATE.kids[0].invest_cents, STATE.kids[0].pending_interest_cents]') == ['Sample Kid', 500, 100, 100, 1]
        page.reload()
        expect(page.locator('#guestWelcome')).to_have_count(0)
        assert not errors
        context.close()
