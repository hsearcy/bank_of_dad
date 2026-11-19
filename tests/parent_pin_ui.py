"""Exercise the real shared-device parent gate; never bypass it in browser tests."""
from playwright.sync_api import expect


def open_parent(page, pin='1234'):
    page.locator('#dadBtn').click()
    expect(page.locator('#parentPinInput')).to_be_visible()
    setup = 'Set ' in page.locator('#pinTitle').inner_text()
    page.locator('#parentPinInput').fill(pin)
    page.locator('.pin-card [data-k="ok"]').click()
    if setup:
        expect(page.locator('#pinTitle')).to_contain_text('Confirm PIN')
        page.locator('#parentPinInput').fill(pin)
        page.locator('.pin-card [data-k="ok"]').click()
    expect(page.locator('#dadPanel')).to_be_visible()
