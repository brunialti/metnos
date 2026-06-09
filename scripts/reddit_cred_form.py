#!/usr/bin/env python3
"""Minimal local form to store Reddit credentials in the Metnos encrypted
credential store (Fernet, ADR 0082) — so secrets never pass through chat.

Run:  python3 scripts/reddit_cred_form.py
Open: http://<host>:8771/   (same LAN as the Metnos admin UI on 8770)

On submit it calls credentials.store("reddit", {...}) and exits.
"""
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import credentials  # noqa: E402

PORT = 8771

FORM = """<!doctype html><html><head><meta charset="utf-8">
<title>Metnos — Reddit credentials</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:560px;margin:3rem auto;padding:0 1rem;background:#0f1117;color:#e6e6e6}
 h1{font-size:1.3rem} label{display:block;margin:.9rem 0 .25rem;font-size:.9rem;color:#9aa}
 input{width:100%;padding:.6rem;border:1px solid #333;border-radius:6px;background:#1a1d27;color:#fff;box-sizing:border-box}
 small{color:#778;display:block;margin-top:.2rem}
 button{margin-top:1.4rem;padding:.7rem 1.4rem;border:0;border-radius:6px;background:#ff4500;color:#fff;font-weight:600;cursor:pointer}
 .ok{background:#143;padding:1rem;border-radius:8px;border:1px solid #2a6}
</style></head><body>
<h1>Reddit credentials → Metnos encrypted store</h1>
<p><small>Create a "script" app at <b>reddit.com/prefs/apps</b> (type: script).
Nothing is sent anywhere except this machine's local store.</small></p>
<form method="POST" action="/save">
 <label>client_id <small>14-char string under the app name</small></label>
 <input name="client_id" required>
 <label>client_secret</label>
 <input name="client_secret" required>
 <label>Reddit username</label>
 <input name="username" required>
 <label>Reddit password <small>if 2FA is on, use: password:123456 (with the current code)</small></label>
 <input name="password" type="password" required>
 <label>user_agent <small>any descriptive string</small></label>
 <input name="user_agent" value="metnos-poster/0.1 by u/USERNAME">
 <button type="submit">Save to encrypted store</button>
</form></body></html>"""

DONE = """<!doctype html><html><head><meta charset="utf-8"><title>Saved</title>
<style>body{font-family:system-ui;max-width:560px;margin:4rem auto;background:#0f1117;color:#e6e6e6}
.ok{background:#143;padding:1.2rem;border-radius:8px;border:1px solid #2a6}</style></head>
<body><div class="ok">✓ Reddit credentials saved (encrypted). You can close this tab.
The form server has stopped.</div></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, html, code=200):
        b = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(FORM)
        else:
            self._send("not found", 404)

    def do_POST(self):
        if self.path != "/save":
            self._send("not found", 404)
            return
        n = int(self.headers.get("Content-Length", 0))
        data = parse_qs(self.rfile.read(n).decode("utf-8"))
        payload = {k: (data.get(k, [""])[0]).strip()
                   for k in ("client_id", "client_secret", "username",
                             "password", "user_agent")}
        credentials.store("reddit", payload)
        self._send(DONE)
        # one-shot: stop after a successful save
        import threading
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    srv = HTTPServer(("0.0.0.0", PORT), H)
    print(f"Reddit credential form on http://0.0.0.0:{PORT}/  (Ctrl-C to cancel)")
    srv.serve_forever()
    print("form server stopped (credentials saved).")
