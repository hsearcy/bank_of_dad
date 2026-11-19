"""Exercise the real shared-device parent gate; never bypass it in browser tests."""
from playwright.sync_api import expect


def check_pin_spacing(page):
    field = page.locator('#parentPinInput')
    field.focus()
    assert field.evaluate('''(el) => {
        const r = el.getBoundingClientRect(), css = getComputedStyle(el);
        const ring = parseFloat(css.outlineWidth) + Math.max(0, parseFloat(css.outlineOffset));
        const key = document.querySelector('.pin-card .keypad button').getBoundingClientRect();
        return key.top - r.bottom - ring >= 8 && key.height >= 44 &&
          document.documentElement.scrollWidth <= innerWidth;
    }''')


def open_parent(page, pin='1234'):
    page.locator('#dadBtn').click()
    expect(page.locator('#parentPinInput')).to_be_visible()
    check_pin_spacing(page)
    setup = 'Set ' in page.locator('#pinTitle').inner_text()
    page.locator('#parentPinInput').fill(pin)
    page.locator('.pin-card [data-k="ok"]').click()
    if setup:
        expect(page.locator('#pinTitle')).to_contain_text('Confirm PIN')
        check_pin_spacing(page)
        page.locator('#parentPinInput').fill(pin)
        page.locator('.pin-card [data-k="ok"]').click()
    expect(page.locator('#dadPanel')).to_be_visible()
