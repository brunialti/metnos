"""Read-only documentation checks; browser artifacts go to the supplied directory."""
import argparse
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument('root', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
root = args.root.resolve()
docs = root / 'docs'
args.output.mkdir(parents=True, exist_ok=True)
os.environ['METNOS_USER_DATA'] = str(args.output / 'data')
os.environ['METNOS_USER_STATE'] = str(args.output / 'state')
sys.path.insert(0, str(root / 'runtime'))
from published_docs import catalog, distribution_files
from tutor.sources import _HTMLBlocks

class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.ids = set()
        self.refs = []
        self.lang = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'html':
            self.lang = attrs.get('lang')
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        for attr in ('href', 'src'):
            if attrs.get(attr):
                self.refs.append(attrs[attr])

pages = {p: Page(p.read_text()) for p in docs.rglob('*.html')}
errors = []
checked_links = 0
changed = {docs / lang / name for lang in ('it', 'en')
           for name in ('organize_files.html', 'Metnos_QuickTour.html')}
for path, page in pages.items():
    for ref in page.refs:
        url = urlsplit(urljoin('https://metnos.com/' + str(path.relative_to(docs)), ref))
        if url.scheme not in ('http', 'https') or url.netloc != 'metnos.com':
            continue
        target = docs / unquote(url.path).lstrip('/')
        if target.is_dir():
            target /= 'index.html'
        elif not target.suffix:
            target = target.with_suffix('.html')
        reason = None
        if not target.is_file():
            reason = 'missing file'
        elif url.fragment and target in pages and unquote(url.fragment) not in pages[target].ids:
            reason = 'missing fragment'
        checked_links += 1
        if reason:
            errors.append({'page': str(path.relative_to(docs)), 'ref': ref, 'reason': reason,
                           'changed_page': path in changed})

tutor = {}
for lang, pending in [('it', 'in corso di attivazione'), ('en', 'activation in progress')]:
    guide = docs / lang / 'organize_files.html'
    text = guide.read_text()
    assert pages[guide].lang == lang
    assert not re.search(r'confirmation_token|grant|/opt/|stack_reconcile|\bBirth\b', text)
    blocks = _HTMLBlocks()
    blocks.feed(text)
    assert len(blocks.blocks) == 2, blocks.blocks
    assert pending in blocks.blocks[1][1]
    tour = docs / lang / 'Metnos_QuickTour.html'
    before = _HTMLBlocks()
    before.feed(subprocess.check_output(['git', 'show', f'581fd197:docs/{lang}/Metnos_QuickTour.html'], cwd=root).decode())
    after = _HTMLBlocks()
    after.feed(tour.read_text())
    assert before.blocks == after.blocks, 'Pending workflow leaked into Tutor Quick Tour'
    tutor[lang] = {'guide_blocks': len(blocks.blocks), 'quick_tour_unchanged': True}
assert pages[docs / 'it/organize_files.html'].ids == pages[docs / 'en/organize_files.html'].ids
urls = [element.text for element in ET.parse(docs / 'sitemap.xml').iter()
        if element.tag.endswith('}loc')]
for lang in ('it', 'en'):
    assert urls.count(f'https://metnos.com/{lang}/organize_files') == 1

class QuietHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        target = Path(super().translate_path(path))
        if not target.exists() and not target.suffix and target.with_suffix('.html').is_file():
            target = target.with_suffix('.html')
        return str(target)

    def log_message(self, *_args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(docs)))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
from playwright.sync_api import sync_playwright
screens = []
browser_errors = []
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/home/roberto/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
                                    headless=True, args=['--no-sandbox'])
        for lang in ('it', 'en'):
            for route_name in ('organize_files', 'organize_files.html', 'Metnos_QuickTour', 'Metnos_QuickTour.html'):
                name = route_name.removesuffix('.html')
                for width in (320, 390, 768, 1440):
                    page = browser.new_page(viewport={'width': width, 'height': 900})
                    page.on('pageerror', lambda error: browser_errors.append(str(error)))
                    page.on('response', lambda response: browser_errors.append(f'{response.status} {response.url}')
                            if response.status >= 400 else None)
                    page.goto(f'http://127.0.0.1:{server.server_port}/{lang}/{route_name}', wait_until='networkidle')
                    page.wait_for_selector('body[data-wiki-ready]')
                    measure = page.evaluate('({client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})')
                    overflow = measure['scroll'] > width + 1
                    baseline_scroll = None
                    if overflow and name == 'Metnos_QuickTour':
                        old = subprocess.check_output(['git', 'show', f'581fd197:docs/{lang}/Metnos_QuickTour.html'], cwd=root).decode()
                        page.route(f'**/{route_name}', lambda route: route.fulfill(body=old, content_type='text/html'))
                        page.reload(wait_until='networkidle')
                        baseline_scroll = page.evaluate('document.documentElement.scrollWidth')
                    assert not overflow or baseline_scroll == measure['scroll'], (lang, name, width, measure)
                    assert page.locator('.wiki-tree a[href$="/organize_files.html"]').count() == 1
                    assert page.locator('.wiki-tree a[aria-current="page"]').count() == 1
                    assert page.locator('.wiki-locale').get_attribute('hidden') is None
                    menu = page.locator('.wiki-toggle')
                    if menu.is_visible():
                        menu.click()
                        assert menu.get_attribute('aria-expanded') == 'true'
                        page.keyboard.press('Escape')
                        assert menu.get_attribute('aria-expanded') == 'false'
                    if name == 'organize_files' and width in (390, 1440):
                        page.screenshot(path=str(args.output / f'{lang}-{width}.png'), full_page=True)
                    screens.append({'lang': lang, 'page': route_name, 'width': width, 'scroll': measure['scroll'],
                                    'overflow': overflow, 'baseline_scroll': baseline_scroll})
                    page.close()
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join()

report = {'canonical_documents': len(catalog(docs)), 'distribution_files': len(distribution_files(docs)),
          'local_links': checked_links, 'link_issues': errors, 'tutor': tutor,
          'browser_cases': screens, 'browser_errors': browser_errors}
(args.output / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({key: len(value) if key in ('browser_cases', 'link_issues') else value
                  for key, value in report.items()}, indent=2))
assert not [error for error in errors if error['changed_page']], 'Links broken on edited pages'
assert not browser_errors, browser_errors
