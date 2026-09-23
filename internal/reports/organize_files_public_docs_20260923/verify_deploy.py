"""Read-only post-deploy checks for the complete static publication."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import html
import re
from pathlib import Path
import subprocess
from urllib.parse import quote
from playwright.sync_api import sync_playwright

root = Path('/opt/metnos/.claude/worktrees/organize-files-public-docs')
artifact = root / '.wrangler/organize-docs-site'
output = Path('/tmp/metnos-organize-docs.bZIVCJ')

def decoded_email(value):
    encoded = bytes.fromhex(value)
    return ''.join(chr(byte ^ encoded[0]) for byte in encoded[1:])

def normalize_edge_email(body):
    """Undo only Cloudflare's existing email protection, never general HTML differences."""
    value = body.decode('utf-8')
    value = re.sub(
        r'<(?:a|span)\b[^>]*\bdata-cfemail="([a-fA-F0-9]+)"[^>]*>\[email&#160;protected\]</(?:a|span)>',
        lambda match: html.escape(decoded_email(match.group(1)), quote=False), value)
    value = re.sub(r'href="/cdn-cgi/l/email-protection#([a-fA-F0-9]+)"',
                   lambda match: 'href="mailto:' + html.escape(decoded_email(match.group(1)), quote=True) + '"', value)
    value = re.sub(r'<script data-cfasync="false" src="/cdn-cgi/scripts/[a-fA-F0-9]+/cloudflare-static/email-decode\.min\.js"></script>', '', value)
    return value

def verify(path):
    rel = str(path.relative_to(artifact))
    url = 'https://metnos.com/' + quote(rel)
    try:
        body = subprocess.check_output(['curl', '-fLsS', '--max-time', '25', url], stderr=subprocess.PIPE)
        expected = path.read_bytes()
        byte_match = body == expected
        edge_match = not byte_match and path.suffix == '.html' and normalize_edge_email(body) == normalize_edge_email(expected)
        return {'path': rel, 'matched': byte_match or edge_match,
                'comparison': 'exact bytes' if byte_match else 'Cloudflare email protection normalized' if edge_match else 'different',
                'sha256': hashlib.sha256(body).hexdigest()}
    except Exception as error:
        return {'path': rel, 'matched': False, 'error': str(error)}

files = [path for path in artifact.rglob('*') if path.is_file() and path.name not in ('_headers', '_redirects')]
with ThreadPoolExecutor(max_workers=6) as pool:
    rows = list(pool.map(verify, files))
(output / 'live-files.json').write_text(json.dumps(rows, indent=2) + '\n')
issues = [row for row in rows if not row['matched']]
print(json.dumps({'public_files_checked': len(rows), 'issues': issues}, indent=2))
assert not issues

browser_errors = []
cases = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='/home/roberto/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
                                headless=True, args=['--no-sandbox'])
    for lang in ('it', 'en'):
        for width in (390, 1440):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.on('pageerror', lambda error: browser_errors.append(str(error)))
            response = page.goto(f'https://metnos.com/{lang}/organize_files', wait_until='networkidle')
            assert response.status == 200
            assert page.locator('html').get_attribute('lang') == lang
            assert page.locator('.wiki-tree a[aria-current="page"]').get_attribute('href') == f'/{lang}/organize_files.html'
            assert page.locator('.wiki-locale').get_attribute('hidden') is None
            assert page.evaluate('document.documentElement.scrollWidth') <= width + 1
            assert page.locator('header .lead').inner_text().find('attivazione' if lang == 'it' else 'activation') >= 0
            page.screenshot(path=str(root / '.wrangler/organize-docs-checks' / f'live-{lang}-{width}.png'), full_page=True)
            cases.append({'lang': lang, 'width': width, 'http': response.status, 'url': page.url})
            page.close()
    browser.close()
report = {'cases': cases, 'browser_errors': browser_errors}
(output / 'live-browser.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
assert not browser_errors
