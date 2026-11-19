"""Founder story on fresh local workerd banks; never contact providers."""
import sqlite3
import pytest
from test_runtime import runtime
from about_story_ui import check_about_story


@pytest.mark.parametrize('width', [390, 1280])
def test_about_story_and_final_tour_return(runtime, width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw, pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox']) as browser:
        context = browser.new_context(viewport={'width': width, 'height': 667}, service_workers='block', reduced_motion='reduce')
        page = context.new_page()
        page.goto(runtime.origin)
        expect(page.locator('#guestWelcome')).to_be_visible()
        page.locator('#tryGuestBank').click()
        page.locator('#tourSkip').click()
        cookie = next(c for c in context.cookies() if c['name'] == 'bod-local-session')
        database = runtime.database(cookie['name'] + '=' + cookie['value'])
        with sqlite3.connect(database) as conn:
            before = list(conn.iterdump())
        check_about_story(page, width)
        with sqlite3.connect(database) as conn:
            assert before == list(conn.iterdump()), 'About/tour changed the bank'
        context.close()
