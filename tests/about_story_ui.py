"""Shared real-browser acceptance for the founder story and tour return path."""
import os
from pathlib import Path
from playwright.sync_api import expect


def check_about_story(page, width):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    footer = page.get_by_role('contentinfo')
    expect(footer.get_by_role('button')).to_have_count(2)
    opener = footer.get_by_role('button', name='About', exact=True)
    opener.click()
    dialog = page.locator('#aboutDialog')
    expect(page.locator('#aboutTitle')).to_be_focused()
    assert dialog.evaluate('(el) => el.scrollTop === 0')
    text = dialog.inner_text()
    for phrase in ('not a real bank', 'I use it to track money I owe my kids',
                   '$1 per year of age every 14 days', 'don’t tie allowance to chores',
                   'worked well for our family', 'another stuffed animal', 'early elementary',
                   '100% increase doubles the amount', '$1 becomes $2, then $4, then $8',
                   'not returns to expect from a real investment'):
        assert phrase in text
    paragraphs = page.locator('#aboutDialog section p').all_text_contents()
    assert len(paragraphs) == 4
    assert paragraphs[0] == ('We started giving our kids an allowance because I got tired of saying no '
                             'to another stuffed animal every time we went to the store. Now they can '
                             'decide whether to buy it themselves or save for something else.')
    assert paragraphs[1] == ('I give my kids $1 per year of age every 14 days, so they get a little raise '
                             'each birthday. We don’t tie allowance to chores, and this has worked well '
                             'for our family.')
    assert text.index('I built Bank of Dad') < text.index('How I use it') < text.index(paragraphs[0])
    assert 'ongoing behavior problems' not in text
    assert 'not a rule' not in text
    page.locator('#howIUseIt').scroll_into_view_if_needed()
    expect(page.locator('#howIUseIt')).to_be_in_viewport()
    page.keyboard.press('Escape')
    expect(opener).to_be_focused()

    # The existing tour stays on its final step while About owns modality.
    page.evaluate('BodExperience.offer()')
    expect(page.locator('.tour-story-link')).to_have_count(0)
    for _ in range(6):
        page.locator('#tourNext').click()
    expect(page.locator('#tourTitle')).to_contain_text('A few things to know')
    title = page.locator('#tourTitle').inner_text()
    link = page.get_by_role('button', name='How I use it with my kids', exact=True)
    expect(link).to_be_visible()
    for close in ('Escape', 'Close'):
        link.focus()
        page.keyboard.press('Enter')
        expect(dialog).to_be_visible()
        expect(page.locator('#howIUseIt')).to_be_focused()
        expect(page.locator('#howIUseIt')).to_be_in_viewport()
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().bottom <= innerHeight')
        expect(page.locator('#productTour')).to_have_attribute('aria-hidden', 'true')
        assert page.locator('#productTour').evaluate('(el) => el.inert && !el.hidden')
        assert page.locator('dialog[open]').count() == 1
        # Walk both directions past all focusable controls, including wraparound.
        for key in ('Shift+Tab', 'Tab'):
            for _ in range(5):
                page.keyboard.press(key)
                assert page.locator(':focus').evaluate('(el) => !!el.closest("#aboutDialog")')
        if close == 'Escape':
            page.keyboard.press('Escape')
        else:
            dialog.get_by_role('button', name='Close', exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator('#productTour')).to_be_visible()
        expect(page.locator('#tourTitle')).to_have_text(title)
        expect(link).to_be_focused()
        page.evaluate('backgroundRefresh()')
        expect(link).to_be_focused()
    assert page.locator('#productTour').evaluate('(el) => {const r=el.getBoundingClientRect(); return r.top>=0 && r.bottom<=innerHeight && el.scrollWidth<=el.clientWidth;}')
    artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(artifacts) / f'about-tour-return-{width}.png'))
        link.click()
        page.screenshot(path=str(Path(artifacts) / f'about-story-{width}.png'))
        page.keyboard.press('Escape')
    page.locator('#tourNext').click()
    expect(page.locator('#productTour')).not_to_be_visible()
    # Opening About normally after the section link must still start at the top.
    opener.click()
    expect(page.locator('#aboutTitle')).to_be_focused()
    assert dialog.evaluate('(el) => el.scrollTop === 0')
    dialog.get_by_role('button', name='Close', exact=True).click()
    expect(opener).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors
