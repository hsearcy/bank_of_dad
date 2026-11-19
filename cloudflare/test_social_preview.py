"""SSR social cards, public crawler assets and portable self-host safety."""
from html.parser import HTMLParser
from pathlib import Path
import struct
import zlib

import httpx
import pytest
from jinja2 import Environment, FileSystemLoader
from test_public import PublicRuntime, family_databases

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'https://thebankofdad.app/static/social-card-v1.png'


class Metadata(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = {}
        self.feed(html.split('</head>', 1)[0])

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('property', attrs.get('name'))
            if key:
                assert key not in self.tags
                self.tags[key] = attrs.get('content')


def assert_metadata(html):
    tags = Metadata(html).tags
    for key, value in {'og:type': 'website', 'og:title': 'Bank of Dad',
                       'og:url': 'https://thebankofdad.app/', 'og:image': IMAGE,
                       'og:image:width': '1200', 'og:image:height': '630',
                       'og:image:type': 'image/png', 'twitter:card': 'summary_large_image',
                       'twitter:title': 'Bank of Dad', 'twitter:image': IMAGE}.items():
        assert tags[key] == value
    for key in ('og:description', 'twitter:description', 'og:image:alt', 'twitter:image:alt'):
        assert 'not a real bank' in tags[key]
    assert tags['og:image:alt'] == tags['twitter:image:alt']


def assert_png(raw):
    assert raw[:8] == b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>II', raw[16:24]) == (1200, 630)
    pos = 8
    pixels = bytearray()
    while pos < len(raw):
        length = struct.unpack('>I', raw[pos:pos+4])[0]
        kind = raw[pos+4:pos+8]
        body = raw[pos+8:pos+8+length]
        assert zlib.crc32(kind + body) == struct.unpack('>I', raw[pos+8+length:pos+12+length])[0]
        if kind == b'IDAT':
            pixels.extend(body)
        pos += length + 12
    assert zlib.decompress(pixels)
    assert len(raw) < 5_000_000


@pytest.mark.parametrize('mode', ['self-host', 'local', 'public'])
def test_metadata_runtime_scope(mode):
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=True)
    values = {'session': {}}
    if mode != 'self-host':
        values['cloudflare_incomplete'] = mode == 'local'
    html = env.get_template('index.html').render(**values)
    if mode == 'public':
        assert_metadata(html)
    else:
        assert not any(key.startswith(('og:', 'twitter:')) for key in Metadata(html).tags)
        assert IMAGE not in html


def test_image_integrity():
    assert_png((ROOT / 'static/social-card-v1.png').read_bytes())


def test_public_crawler_html_assets_and_no_asset_guest(tmp_path):
    server = PublicRuntime(tmp_path)
    server.start()
    try:
        before = family_databases(server)
        with httpx.Client(verify=False, headers={'Host': 'thebankofdad.app', 'User-Agent': 'Twitterbot/1.0'}) as client:
            image = client.get(server.origin + '/static/social-card-v1.png')
            assert image.status_code == 200
            assert image.headers['content-type'].startswith('image/png')
            assert 'set-cookie' not in image.headers
            assert_png(image.content)
            assert image.content == (ROOT / 'static/social-card-v1.png').read_bytes()
            robots = client.get(server.origin + '/robots.txt')
            assert robots.status_code == 200 and 'Allow: /' in robots.text
            assert 'set-cookie' not in robots.headers
        assert family_databases(server) == before
        for agent in ('Twitterbot/1.0', 'Mozilla/5.0'):
            code, html, _ = server.call('/', origin=False, headers={'User-Agent': agent})
            assert code == 200
            assert_metadata(html)
    finally:
        server.stop()
