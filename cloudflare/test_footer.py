"""Actual Worker dashboard, disposable family banks and desktop/mobile Chromium."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'tests'))
from parent_pin_ui import open_parent

import os
from pathlib import Path
import sqlite3

import pytest
from playwright.sync_api import sync_playwright, expect

from test_runtime import runtime


def snapshot(server, cookie):
    with sqlite3.connect(server.database(cookie)) as conn:
        return list(conn.iterdump())


@pytest.mark.parametrize('width', [390, 1280])
def test_footer_and_compact_controls_in_workerd(runtime, width):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('BOD_TEST_CHROMIUM', '/usr/bin/chromium'), args=['--no-sandbox'])
        ctx = browser.new_context(viewport={'width': width, 'height': 667}, reduced_motion='reduce')
        page = ctx.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(runtime.origin)
            page.locator('#tryGuestBank').click()
            page.locator('#tourSkip').click()
            expect(page.locator('#kids .big')).to_have_text('$7.00')
            cookie = next(c for c in ctx.cookies() if c['name'] == 'bod-local-session')
            cookie = cookie['name'] + '=' + cookie['value']
            before = snapshot(runtime, cookie)
            footer = page.get_by_role('contentinfo')
            footer.scroll_into_view_if_needed()
            expect(footer.get_by_role('button', name='About', exact=True)).to_be_visible()
            expect(footer.get_by_role('button', name='Privacy', exact=True)).to_be_visible()
            artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
            def capture(name):
                if artifacts:
                    Path(artifacts).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(artifacts) / f'footer-{name}-{width}.png'), full_page=True)
            capture('dashboard')
            for label, title in [('About', 'About Bank of Dad'), ('Privacy', 'Privacy')]:
                opener = footer.get_by_role('button', name=label, exact=True)
                opener.focus()
                page.keyboard.press('Enter')
                dialog = page.get_by_role('dialog', name=title, exact=True)
                expect(dialog).to_be_visible()
                expect(dialog.get_by_role('heading', name=title, exact=True)).to_be_focused()
                assert dialog.evaluate('(el) => el.scrollTop === 0 && el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().bottom <= innerHeight')
                capture(label.lower())
                page.keyboard.press('Shift+Tab')
                expect(dialog.get_by_role('button', name='Close', exact=True)).to_be_focused()
                page.keyboard.press('Tab')
                expect(dialog.locator('a').first).to_be_focused()
                for _ in range(8):
                    page.keyboard.press('Tab')
                    assert page.locator(':focus').evaluate('(el) => !!el.closest("dialog")')
                if label == 'About':
                    expect(dialog).to_contain_text('my kids')
                    expect(dialog).to_contain_text('compounding')
                else:
                    for text in ['names you enter', 'verified account ID', 'Google', 'Stripe', 'Cloudflare', 'Clearing browser data']:
                        expect(dialog).to_contain_text(text)
                    expect(dialog).not_to_contain_text('Use nicknames')
                    expect(dialog.get_by_role('link', name='support@thebankofdad.app')).to_have_attribute('href', 'mailto:support@thebankofdad.app')
                page.keyboard.press('Escape')
                expect(dialog).not_to_be_visible()
                expect(opener).to_be_focused()
                opener.click()
                dialog.get_by_role('button', name='Close', exact=True).click()
                expect(opener).to_be_focused()
            open_parent(page)
            section = page.get_by_role('region', name='This screen')
            height = section.bounding_box()['height']
            assert height < 150, height
            print(f'This screen ({width}px): {height}px')
            expect(page.get_by_role('switch', name='Keep screen awake')).not_to_be_checked()
            expect(page.locator('#awakeStatus')).to_have_text('Off')
            capture('dad')
            page.get_by_role('button', name='Quick tour', exact=True).click()
            expect(page.locator('#productTour')).to_be_visible()
            page.keyboard.press('Escape')
            assert snapshot(runtime, cookie) == before
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert errors == []
        finally:
            ctx.close()
            browser.close()
