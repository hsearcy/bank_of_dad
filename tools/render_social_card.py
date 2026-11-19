"""Render the editable artwork with the project's Playwright dev dependency.

Run from the repository: cloudflare/.venv/bin/python tools/render_social_card.py
Requires Chromium at /usr/bin/chromium; no network or family data is used.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with sync_playwright() as pw, pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox']) as browser:
    page = browser.new_page(viewport={'width': 1200, 'height': 630}, device_scale_factor=1)
    page.goto((root / 'tools/social-card.html').as_uri())
    page.wait_for_function('Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)')
    page.screenshot(path=str(root / 'static/social-card-v1.png'))
