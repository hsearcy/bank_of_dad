"""Shared real-browser acceptance for both deployment adapters."""
from playwright.sync_api import expect


def exercise_kid_controls(page, observer, unlock):
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    observer.on('pageerror', lambda e: errors.append(str(e)))
    page.set_viewport_size({'width': 390, 'height': 844})
    unlock(page)
    first = page.evaluate('STATE.kids[0].id')
    page.locator('#newKidName').fill('Sibling')
    page.locator('#addKidBtn').click()
    expect(page.locator('#dadKids .dk')).to_have_count(2)
    expect(observer.locator('#kids .name')).to_have_count(2, timeout=5000)
    kid = page.locator(f'.dk[data-id="{first}"]')
    kid.locator('.rename-kid').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_label('Name', exact=True).fill('New <Kid> 🐣')
    page.locator('#kidDialog').get_by_role('button', name='Save name', exact=True).click()
    expect(kid.locator('.dk-head')).to_contain_text('New <Kid> 🐣')
    expect(observer.locator('#kids .name').first).to_have_text('New <Kid> 🐣', timeout=5000)
    # Cancellation (button and Escape) performs no POST and preserves all state.
    before = page.request.get(page.url + 'api/state').json()['kids']
    requests = []
    page.on('request', lambda r: requests.append(r.url) if r.method == 'POST' and '/kid/' in r.url else None)
    kid.locator('.remove-kid').click()
    expect(page.locator('#kidDialogHelp')).to_contain_text('cannot be undone')
    expect(page.locator('#kidDialogHelp')).to_contain_text('investments, transactions and chart history')
    expect(page.locator('#kidDialogCancel')).to_be_focused()
    page.locator('#kidDialogCancel').click()
    kid.locator('.remove-kid').click()
    page.keyboard.press('Escape')
    kid.locator('.rename-kid').click()
    page.locator('#kidName').fill('Not saved')
    page.keyboard.press('Escape')
    assert requests == []
    assert page.request.get(page.url + 'api/state').json()['kids'] == before
    kid.locator('.remove-kid').click()
    # Native modal traps keyboard focus; long names and controls fit a phone.
    for _ in range(6):
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement.closest("#kidDialog") !== null')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('#kidDialogSave').bounding_box()['height'] >= 44
    page.get_by_role('button', name='Permanently remove New <Kid> 🐣', exact=True).click()
    expect(page.locator('#dadKids .dk')).to_have_count(1)
    expect(observer.locator('#kids .name')).to_have_count(1, timeout=5000)
    expect(observer.locator('#kids .name')).to_have_text('Sibling')
    # Remove last child: empty bank is usable and never silently reseeded.
    page.locator('.remove-kid').click()
    page.get_by_role('button', name='Permanently remove Sibling', exact=True).click()
    expect(observer.locator('#kids')).to_contain_text('add your first kid')
    page.reload()
    expect(page.locator('#kids')).to_contain_text('add your first kid')
    assert errors == []
    return first
