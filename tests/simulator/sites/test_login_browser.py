"""Simulatore sites-login con browser REALE (regressione turn e69dca8e).

Server HTTP locale + Chromium reale + vero `session_broker` (`op_open`+`op_login`)
contro una home sintetica tipo-Amazon: ingresso login in header -> `/signin`,
overlay cookie ("Cookie e scelte pubblicitarie" + "Rifiuta" navigante che
riappare), DOM con filler. Nessun contatto con siti reali.

Riproduce il bug per cui la dismissione dell'overlay privacy consumava il budget
di step d'ingresso login (`login_step_limit`) prima ancora di cliccare "accedi"
(-> `selector_missing`). Il fix da' alle dismissioni un budget PROPRIO e bounded
(`_MAX_PRIVACY_DISMISSALS`), cosi' un overlay che riappare non affama la
navigazione verso l'ingresso login.

Gated da env `METNOS_SITES_SIM=1` (browser reale, ~40s): skip di default, cosi'
non e' un prerequisito della suite. Richiede
`PLAYWRIGHT_BROWSERS_PATH` sulla distribuzione browser locale di Metnos.
"""
from __future__ import annotations

import asyncio
import http.server
import os
import socket
import sys
import threading
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


# ADR 0191 §8.A / v4 §10: overlay privacy NON-navigante — la dismissione sicura
# lo rimuove via JS (button[type=button]), senza submit/navigazione. Un consenso
# NAVIGANTE (input[type=submit]/form POST) passerebbe invece dal gate (P5/#11),
# testato separatamente.
_COOKIE_OVERLAY = """
<div id="sp-cc" style="position:fixed;left:0;right:0;bottom:0;background:#fff;
     border-top:1px solid #ccc;padding:20px;z-index:9999">
  <h2>Cookie e scelte pubblicitarie</h2>
  <p>Con il tuo consenso, possiamo utilizzare i cookie...</p>
  <button type="button" id="cc-accept"
          onclick="document.getElementById('sp-cc').remove()">Accetta</button>
  <button type="button" id="cc-reject"
          onclick="document.getElementById('sp-cc').remove()">Rifiuto</button>
</div>
"""


def _filler(n: int) -> str:
    return "".join(
        f'<div class="card"><a href="/p/{i}">Prodotto {i}</a>'
        f'<button>Aggiungi {i}</button></div>' for i in range(n))


def _home(overlay: bool, filler: int) -> str:
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Amazon.it: elettronica, libri</title></head><body>
<header style="background:#131921;color:#fff;padding:10px"><span>Amazon.it</span>
  <nav style="float:right">
    <a href="/signin" id="nav-signin" style="color:#fff;margin:0 10px">Ciao, accedi</a>
    <a href="/signin" style="color:#fff;margin:0 10px">Account e liste</a>
    <a href="/cart" style="color:#fff;margin:0 10px">Carrello</a>
  </nav></header>
<main>{_filler(filler)}</main>
{_COOKIE_OVERLAY if overlay else ''}
</body></html>"""


_SIGNIN = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Accedi</title></head><body><h1>Accedi</h1>
<form action="/signin-user" method="POST">
  <label>Inserisci il numero di cellulare o l'indirizzo e-mail</label>
  <input type="text" name="email" id="ap_email" autofocus>
  <input type="submit" value="Continua"></form></body></html>"""

_PASSWORD = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Password</title></head><body><h1>Password</h1>
<form action="/signin-pass" method="POST">
  <input type="password" name="password" id="ap_password">
  <input type="submit" value="Accedi"></form></body></html>"""

_DONE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Home account</title></head><body>
<header style="background:#131921;color:#fff;padding:10px"><span>Amazon.it</span>
  <nav style="float:right">
    <a href="/account" style="color:#fff;margin:0 10px">Ciao, test</a>
    <a href="/cart" id="nav-cart" style="color:#fff;margin:0 10px">Carrello</a>
  </nav></header><h1>Ciao, test</h1></body></html>"""

_PERSONAL_DONE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Area personale</title></head><body>
<header><button type="button" aria-label="Menu account test"
 onclick="document.getElementById('account-menu').hidden=false">Profilo test</button>
<nav id="account-menu" hidden><a href="/documenti">Documenti</a></nav></header>
<main><h1>Area personale</h1></main>
<div id="promo" role="dialog" aria-modal="true"
 style="position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.45);display:flex;
 align-items:center;justify-content:center">
 <section style="position:relative;width:420px;min-height:180px;background:white;padding:24px">
  <h2>Contenuto disponibile</h2><p>Informazione promozionale facoltativa.</p>
  <button type="button" onclick="document.getElementById('promo').remove()">OK</button>
 </section>
</div></body></html>"""

_DOCUMENTS = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>I miei documenti</title></head><body><main><h1>Documenti</h1>
<div role="tablist" aria-label="Periodo">
 <button id="recenti" role="tab" aria-selected="true"
  onclick="selectTab('recenti')">Recenti</button>
 <button id="archiviati" role="tab" aria-selected="false"
  onclick="selectTab('archiviati')">Archiviati</button>
</div>
<section id="recenti-panel"><p>Nessun documento recente.</p></section>
<section id="archiviati-panel" hidden>
 <article>Documento archiviato del 12 gennaio 2025</article>
</section></main>
<script>
function selectTab(selected) {
  for (const id of ['recenti', 'archiviati']) {
    document.getElementById(id).setAttribute(
      'aria-selected', id === selected ? 'true' : 'false');
    document.getElementById(id + '-panel').hidden = id !== selected;
  }
}
</script></body></html>"""

_CART = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Il tuo carrello Amazon</title></head><body><h1>Carrello</h1>
<div class="item"><span>Echo Dot (5a gen) Altoparlante con Alexa - Antracite</span>
  <span>EUR 34,99</span><span>Quantità: 1</span></div>
<div class="item"><span>Cavo USB-C Anker 2m (confezione da 2)</span>
  <span>EUR 12,99</span><span>Quantità: 2</span></div>
<p>Sottototale (3 articoli): EUR 60,97</p>
<div class="promo">Consigliati per te - Sponsorizzato</div>
</body></html>"""


def _make_handler(overlay: bool, filler: int, rate_limit: bool = False,
                  personal_goal: bool = False):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def _send(self, body, status=200, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self._send(_home(overlay, filler))
            elif path == "/signin":
                self._send(_SIGNIN)
            elif path == "/cart":
                self._send(_CART)
            elif path == "/account":
                self._send(_DONE)
            elif path == "/documenti":
                self._send(_DOCUMENTS)
            else:
                self._send("<html><body>ok</body></html>")

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            path = self.path.split("?")[0]
            if path == "/cookie":
                # reject naviga e RIAPPARE (worst case: nessun cookie di consenso)
                self._send("", status=302, headers={"Location": "/"})
            elif path == "/signin-user":
                self._send(_PASSWORD)
            elif path == "/signin-pass":
                if rate_limit:
                    # Fix #5: submit che risponde 429 REALE (+ Retry-After).
                    self._send("<html><body>Too Many Requests</body></html>",
                               status=429, headers={"Retry-After": "60"})
                else:
                    self._send(
                        _PERSONAL_DONE if personal_goal else _DONE,
                        headers={"Set-Cookie": "session-id=abc; Path=/"})
            else:
                self._send("<html><body>ok</body></html>")
    return H


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _drive(url: str, *, full_chain: bool = False,
                 personal_goal: bool = False):
    from playwright.async_api import async_playwright
    from playwright_sidecar import session_broker as sb
    from playwright_sidecar import credential_injection as ci
    import sites_audit

    # ADR 0191 P2: il fill e' autorizzato a MATCH ESATTO (scheme,host,port). Il
    # server sim gira su una porta random → registra l'origine ESATTA come farebbe
    # una credenziale reale di un device LAN (senza, la migrazione deriva :80 e
    # l'enforcement apre un gate credential_origin).
    import sites_origin as _so
    _sim_origin = _so.origin_of_url(url)
    ci._load_site_credentials = lambda domain: (
        {"username": "test@example.it", "password": "sim-pw",
         "credential_origins": [_sim_origin]}, "127.0.0.1")
    events = []
    sites_audit.record = lambda event, **f: events.append((event, f))

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # ADR 0191 B1: il broker riceve un BrowserProvider async, non un browser.
        async def _provider(_browser_mode="headless", _stealth=False):
            return browser
        sb.configure(_provider)
        try:
            opened = await sb.op_open(owner="host", url=url)
            sid = opened.get("session_id")
            if not sid:
                return {"open": opened}, events
            login = await sb.op_login(session_id=sid, owner="host")
            out = {"open": opened, "login": login}
            if personal_goal and login.get("logged_in"):
                action = "vai ai miei documenti archiviati"
                act = await sb.op_act(
                    session_id=sid, owner="host", action=action)
                for _ in range(4):
                    if act.get("approval_required") and act.get("approval_token"):
                        act = await sb.op_act(
                            session_id=sid, owner="host", action=action,
                            approval_token=act["approval_token"])
                    else:
                        break
                rd = await sb.op_read(session_id=sid, owner="host",
                                      include_screenshot=False)
                out.update({"act": act, "read_text": rd.get("text", "")})
            elif full_chain and login.get("logged_in"):
                # act(carrello) -> read -> extract: catena §2.2 completa.
                # La navigazione post-login e' sensibile (tainted-turn): apre un
                # gate; qui lo approviamo (simula il consenso utente).
                act = await sb.op_act(
                    session_id=sid, owner="host",
                    action="mostra gli articoli nel carrello",
                    goal_query="articoli nel carrello")
                for _ in range(4):
                    if act.get("approval_required") and act.get("approval_token"):
                        act = await sb.op_act(
                            session_id=sid, owner="host",
                            action="mostra gli articoli nel carrello",
                            approval_token=act["approval_token"])
                    else:
                        break
                rd = await sb.op_read(session_id=sid, owner="host",
                                      include_screenshot=False)
                out["read_text"] = rd.get("text", "")
                from extract_entries import handle_extract_entries
                out["extract"] = handle_extract_entries({
                    "entries": [{"text": out["read_text"]}]
                    if out["read_text"] else [],
                    "fields": ["articolo", "prezzo", "quantita"]})
            await sb.op_close(owner="host", close_all=True)
            return out, events
        finally:
            await browser.close()


@unittest.skipUnless(
    os.environ.get("METNOS_SITES_SIM") == "1",
    "METNOS_SITES_SIM not set; real-browser sites-login simulator skipped",
)
class TestSitesLoginSimulator(unittest.TestCase):
    def _run(self, overlay: bool, full_chain: bool = False,
             personal_goal: bool = False):
        port = _free_port()
        httpd = http.server.HTTPServer(
            ("127.0.0.1", port), _make_handler(
                overlay, filler=200, personal_goal=personal_goal))
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            return asyncio.run(_drive(
                f"http://127.0.0.1:{port}/", full_chain=full_chain,
                personal_goal=personal_goal))
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join(timeout=2)

    def test_cookie_overlay_does_not_starve_login_entry(self):
        from playwright_sidecar import session_broker as sb
        out, events = self._run(overlay=True)
        login = out.get("login") or {}
        # Login riuscito nonostante l'overlay che riappare.
        self.assertTrue(login.get("logged_in"),
                        f"login non riuscito con overlay: {login}")
        # L'ingresso login e' stato cliccato (budget non affamato).
        clicks = [f.get("resolved_name") for ev, f in events
                  if ev == "site_action" and f.get("primitive") == "click"]
        self.assertIn("Ciao, accedi", clicks)
        # Le dismissioni overlay sono bounded (budget proprio).
        dismisses = sum(1 for ev, _f in events if ev == "overlay_dismiss")
        self.assertLessEqual(dismisses, sb._MAX_PRIVACY_DISMISSALS)

    def test_login_entry_without_overlay(self):
        out, _events = self._run(overlay=False)
        login = out.get("login") or {}
        self.assertTrue(login.get("logged_in"),
                        f"login non riuscito senza overlay: {login}")

    def test_full_chain_login_to_structured_cart(self):
        """Obiettivo del handoff: dopo il login, act(carrello) -> read_sites ->
        extract_entries producono RECORD STRUTTURATI dal testo grezzo della
        pagina, scartando il rumore (sponsorizzato/subtotale)."""
        out, _events = self._run(overlay=True, full_chain=True)
        self.assertTrue((out.get("login") or {}).get("logged_in"),
                        f"login non riuscito: {out.get('login')}")
        self.assertIn("Echo Dot", out.get("read_text", ""))
        ex = out.get("extract") or {}
        self.assertTrue(ex.get("ok"), f"extract fallito: {ex}")
        items = ex.get("entries") or []
        # 2 articoli reali estratti; lo sponsorizzato NON e' un articolo.
        self.assertGreaterEqual(len(items), 2)
        names = " ".join(str(i.get("articolo", "")) for i in items)
        self.assertIn("Echo Dot", names)
        self.assertNotIn("Sponsorizzato", names)

    def test_post_login_overlay_then_personal_account_menu(self):
        """Il reducer conserva il possesso e l'acknowledgement localizzato non
        impedisce la traversata generica del menu personale."""
        out, events = self._run(overlay=True, personal_goal=True)
        self.assertTrue((out.get("login") or {}).get("logged_in"), out)
        self.assertTrue((out.get("act") or {}).get("ok"), (out, events))
        self.assertIn("Documento archiviato del 12 gennaio 2025",
                      out.get("read_text", ""), (out, events))
        dismisses = [fields for event, fields in events
                     if event == "overlay_dismiss"]
        self.assertTrue(dismisses, "la modale post-login non e' stata chiusa")
        clicks = [fields.get("resolved_name") for event, fields in events
                  if event == "site_action" and fields.get("outcome")]
        self.assertIn("Menu account test", clicks)
        self.assertIn("Documenti", clicks)
        self.assertIn("Archiviati", clicks)

    def test_real_429_on_submit_feeds_rate_limited_cooldown(self):
        """Fix adversarial #5: un 429 REALE sul submit (catturato dal listener di
        navigazione, non costruito a mano) → esito rate_limited → cooldown."""
        import tempfile
        import sites_cooldown
        with tempfile.TemporaryDirectory() as td:
            os.environ["METNOS_SITES_COOLDOWN_DB"] = os.path.join(td, "cd.sqlite")
            sites_cooldown._initialized.clear()
            port = _free_port()
            httpd = http.server.HTTPServer(
                ("127.0.0.1", port),
                _make_handler(overlay=False, filler=50, rate_limit=True))
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                out, _ev = asyncio.run(_drive(f"http://127.0.0.1:{port}/"))
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(timeout=2)
            login = out.get("login") or {}
            self.assertFalse(login.get("logged_in"),
                             f"il 429 non doveva autenticare: {login}")
            conn = sites_cooldown._connect()
            try:
                row = conn.execute(
                    "SELECT last_reason, fail_count FROM sites_cooldown"
                ).fetchone()
            finally:
                conn.close()
            os.environ.pop("METNOS_SITES_COOLDOWN_DB", None)
            self.assertIsNotNone(
                row, "il 429 reale non ha alimentato il cooldown (listener?)")
            self.assertEqual(row[0], "rate_limited")


if __name__ == "__main__":
    unittest.main()
