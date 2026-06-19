#!/usr/bin/env python3
"""posts_server.py — serve i post Reddit (IT+EN) in markdown copiabile via HTTP.

Pagina con due textarea (IT, EN) + bottone "copia", così Roberto copia-incolla
il markdown grezzo su Reddit senza perdere la formattazione. Anche endpoint
raw /it.md e /en.md per copia diretta.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from html import escape

_DIR = Path(__file__).resolve().parents[1] / "internal/reports/scaling_results"
PORT = int(os.environ.get("POSTS_PORT", "8902"))


def _read(name):
    try:
        return (_DIR / name).read_text(encoding="utf-8")
    except Exception:
        return f"(manca {name})"


def page():
    it = escape(_read("reddit_post_it.md"))
    en = escape(_read("reddit_post_en.md"))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Metnos — post Reddit</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font:14px/1.5 system-ui,sans-serif;margin:0;padding:18px}}
h1{{color:#58a6ff;font-size:18px}} h2{{color:#8b98a5;font-size:14px;margin:18px 0 6px}}
.row{{display:flex;gap:16px;flex-wrap:wrap}}
.col{{flex:1;min-width:360px}}
textarea{{width:100%;height:62vh;background:#161b22;color:#e6edf3;border:1px solid #30363d;
  border-radius:8px;padding:12px;font:12px/1.45 ui-monospace,Menlo,monospace;resize:vertical}}
button{{background:#238636;color:#fff;border:0;border-radius:6px;padding:8px 14px;
  font-size:13px;cursor:pointer;margin:6px 0}}
a{{color:#58a6ff}}
.note{{color:#7a8aa0;font-size:12px}}
</style></head><body>
<h1>Post Reddit — markdown copiabile</h1>
<p class="note">Seleziona tutto (clic + Ctrl/Cmd+A) o usa "Copia", poi incolla su Reddit (modalità markdown).
Raw: <a href="/it.md">/it.md</a> · <a href="/en.md">/en.md</a></p>
<h2>Immagini da caricare nel post (drag &amp; drop su Reddit)</h2>
<p class="note">
IMG 1 (heatmap): <a href="/img/struct_iter5_final.png" download>struct_iter5_final.png</a> ·
IMG 2 (scomposizione IT): <a href="/img/example_breakdown_it.png" download>example_breakdown_it.png</a> ·
IMG 2 (breakdown EN): <a href="/img/example_breakdown_en.png" download>example_breakdown_en.png</a> ·
bonus (azioni vs domini): <a href="/img/struct_iter5_final_marginals.png" download>marginals.png</a>
</p>
<div class="row" style="margin-bottom:10px">
  <img src="/img/struct_iter5_final.png" style="max-width:48%;border:1px solid #30363d;border-radius:8px">
  <img src="/img/example_breakdown_it.png" style="max-width:48%;border:1px solid #30363d;border-radius:8px">
</div>
<div class="row">
  <div class="col">
    <h2>🇮🇹 Italiano</h2>
    <button onclick="cp('ta_it')">Copia IT</button>
    <textarea id="ta_it" spellcheck="false">{it}</textarea>
  </div>
  <div class="col">
    <h2>🇬🇧 English</h2>
    <button onclick="cp('ta_en')">Copy EN</button>
    <textarea id="ta_en" spellcheck="false">{en}</textarea>
  </div>
</div>
<script>
function cp(id){{var t=document.getElementById(id);t.select();
  try{{navigator.clipboard.writeText(t.value);}}catch(e){{document.execCommand('copy');}}}}
</script>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.rstrip("/")
        if p in ("", "/posts"):
            body = page().encode("utf-8"); ct = "text/html; charset=utf-8"
        elif p == "/it.md":
            body = _read("reddit_post_it.md").encode("utf-8"); ct = "text/plain; charset=utf-8"
        elif p == "/en.md":
            body = _read("reddit_post_en.md").encode("utf-8"); ct = "text/plain; charset=utf-8"
        elif p.startswith("/img/"):
            name = os.path.basename(p)
            fp = _DIR / name
            if fp.suffix == ".png" and fp.is_file():
                body = fp.read_bytes(); ct = "image/png"
            else:
                self.send_response(404); self.end_headers(); return
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"posts server on http://0.0.0.0:{PORT}/  (it.md, en.md raw)")
    srv.serve_forever()
