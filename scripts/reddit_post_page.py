#!/usr/bin/env python3
"""Serve the Reddit post (title + body) as a web page with copy buttons,
so it can be copied from a browser (terminal copy not available).

Run:  python3 scripts/reddit_post_page.py
Open: http://<host>:8772/
"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import html

PORT = 8772
BASE = Path("/tmp/reddit-post")

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Reddit post — copy</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;background:#0f1117;color:#e6e6e6}}
 h2{{font-size:1rem;color:#9aa;margin:1.4rem 0 .4rem}}
 textarea{{width:100%;box-sizing:border-box;background:#1a1d27;color:#fff;border:1px solid #333;border-radius:6px;padding:.7rem;font-family:ui-monospace,monospace;font-size:.85rem}}
 button{{margin:.5rem 0;padding:.6rem 1.2rem;border:0;border-radius:6px;background:#ff4500;color:#fff;font-weight:600;cursor:pointer}}
 a{{color:#5af}} .ok{{color:#5d5;margin-left:.6rem}}
</style></head><body>
<h1>Reddit post — copy & paste</h1>
<p>Posta su <a href="https://old.reddit.com/r/LocalLLaMA/submit" target="_blank">r/LocalLLaMA (text post)</a>.
Usa i bottoni Copy (il copia del browser funziona anche se il terminale no).</p>

<h2>TITLE</h2>
<textarea id="t" rows="2">{title}</textarea>
<button onclick="cp('t',this)">Copy title</button>

<h2>BODY (markdown)</h2>
<textarea id="b" rows="26">{body}</textarea>
<button onclick="cp('b',this)">Copy body</button>

<script>
function cp(id,btn){{
  var el=document.getElementById(id); el.focus(); el.select();
  try{{ document.execCommand('copy'); }}catch(e){{}}
  try{{ if(navigator.clipboard) navigator.clipboard.writeText(el.value); }}catch(e){{}}
  var s=document.createElement('span'); s.className='ok'; s.textContent='✓ copied';
  btn.after(s); setTimeout(function(){{s.remove();}},1500);
}}
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(404); self.end_headers(); return
        title = (BASE / "title.txt").read_text(encoding="utf-8").strip()
        body = (BASE / "body.md").read_text(encoding="utf-8")
        out = PAGE.format(title=html.escape(title), body=html.escape(body))
        b = out.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"post page on http://0.0.0.0:{PORT}/")
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
