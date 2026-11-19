"""Shared actual-control tour acceptance for Flask and local workerd."""
import os
from pathlib import Path
from playwright.sync_api import expect


def save_and_finish_tour(page, width):
    page.locator('#replayTour').click()
    for _ in range(3):
        page.locator('#tourNext').click()
    expect(page.locator('#tourTitle')).to_contain_text('Give saving a try')
    title = page.locator('#tourTitle').inner_text()
    page.locator('.btn-save').first.click()
    expect(page.locator('#modal')).to_be_visible()
    # Regression: the old document click listener closes the tour here.
    assert not page.locator('#productTour').evaluate('(el) => el.hidden')
    page.locator('#modalCard .custom-amt').fill('1')
    page.evaluate('backgroundRefresh()')
    expect(page.locator('#modalCard .custom-amt')).to_have_value('1')
    artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(artifacts) / f'tour-save-panel-{width}.png'))
    page.locator('#modalCard .close').click()
    expect(page.locator('#productTour')).to_be_visible()
    expect(page.locator('#tourTitle')).to_have_text(title)
    expect(page.locator('.btn-save').first).to_have_class(__import__('re').compile('tour-target'))
    # A failed transport leaves the real picker and tour available for retry.
    page.locator('.btn-save').first.click()
    page.locator('#modalCard .custom-amt').fill('1')
    page.route('**/api/save', lambda route: route.abort('failed'), times=1)
    with page.expect_event('requestfailed', predicate=lambda request: request.url.endswith('/api/save')):
        page.locator('#modalCard [data-act="save"]').click()
    assert not page.locator('#productTour').evaluate('(el) => el.hidden')
    expect(page.locator('#modalCard .custom-amt')).to_have_value('1')
    with page.expect_response('**/api/save') as response:
        page.locator('#modalCard [data-act="save"]').click()
    assert response.value.json()['ok']
    expect(page.locator('#modal')).not_to_be_visible()
    expect(page.locator('#productTour')).to_be_visible()
    expect(page.locator('#tourTitle')).to_have_text(title)
    page.evaluate('backgroundRefresh()')
    page.wait_for_function('document.querySelector(".btn-save").classList.contains("tour-target")')
    page.locator('#tourNext').click()
    page.locator('#tourNext').click()
    expect(page.locator('#tourTitle')).to_contain_text('Four tools below the graph')
    controls = page.locator('.wallet-actions.tour-target')
    expect(controls).to_have_count(1)
    for selector in ('.btn-spend', '.btn-whatif', '.btn-history', '.btn-expand'):
        expect(controls.locator(selector)).to_be_visible()
        assert controls.locator(selector).get_attribute('aria-label')
    assert controls.evaluate('(el) => { const r=el.getBoundingClientRect(); return r.top >= 0 && r.bottom < document.querySelector("#productTour").getBoundingClientRect().top; }')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    graph_title = page.locator('#tourTitle').inner_text()
    for selector in ('.btn-spend', '.btn-whatif', '.btn-history', '.btn-expand'):
        controls.locator(selector).click()
        expect(page.locator('#modal')).to_be_visible()
        assert not page.locator('#productTour').evaluate('(el) => el.hidden')
        page.evaluate('backgroundRefresh()')
        page.locator('#modalCard .close, #modalCard .graph-close').click()
        expect(page.locator('#productTour')).to_be_visible()
        expect(page.locator('#tourTitle')).to_have_text(graph_title)
        expect(page.locator('.wallet-actions.tour-target')).to_have_count(1)
    page.emulate_media(reduced_motion='no-preference')
    page.emulate_media(reduced_motion='reduce')
    artifacts = os.environ.get('BOD_TEST_ARTIFACTS')
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(artifacts) / f'tour-controls-{width}.png'))
    page.locator('#tourNext').click()
    expect(page.locator('#tourTitle')).to_contain_text('A few things to know')
    text = page.locator('#tourText').inner_text()
    for phrase in ('not a real bank', 'own kids', 'always-on', 'claim', 'VERY HIGH', 'parent dashboard', 'default', 'optional automatic'):
        assert phrase in text
    if page.evaluate('STATE.config.auto_interest_enabled'):
        assert 'Automatic savings interest is on' in text
        assert 'once every configured savings period' in text
    else:
        assert 'kids claim interest themselves' in text
    assert page.evaluate('parentName()') in text
    assert page.locator('#productTour').evaluate('(el) => {const r=el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && el.scrollWidth <= el.clientWidth;}')
    if artifacts:
        page.screenshot(path=str(Path(artifacts) / f'tour-info-{width}.png'))
    page.locator('#tourSkip').click()
    expect(page.locator('#productTour')).not_to_be_visible()
    assert page.locator('.tour-target').count() == 0
    page.locator('#dadBtn').click()
    page.locator('#parentPinInput').fill('1234')
    page.locator('.pin-card [data-k="ok"]').click()
    page.locator('#replayTour').click()
    for _ in range(7):
        page.locator('#tourNext').click()
    expect(page.locator('#productTour')).not_to_be_visible()
