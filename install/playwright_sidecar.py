# SPDX-License-Identifier: MIT
"""install/playwright_sidecar.py — JS-render sidecar (locale, opzionale).

Abilita la lettura di pagine rese via JavaScript (SPA: shopping, social,
molti siti moderni) che il fetch HTTP statico non riesce a leggere. Il
rendering avviene **interamente in locale** (browser selezionato su questa
macchina): nessuna pagina passa da un servizio o provider esterno.

Chromium resta il default. Camoufox richiede una scelta esplicita su Linux
x86_64 e un archivio verificato di circa 1,3 GB, oltre allo spazio estratto.
Senza, Metnos degrada con onesta': read_urls_html segnala "pagina SPA non
leggibile" invece di restituire contenuto vuoto.

Uso standalone (post-install):
    python -m install.playwright_sidecar            # interattivo
    python -m install.playwright_sidecar --yes      # non-interattivo

Robustezza rete: il download di Chromium viene scaricato a blocchi con
verifica d'integrita' per-blocco (doppio-fetch concorde) e md5 totale
contro l'hash ufficiale dell'oggetto. Cosi' una linea instabile (reset,
corruzione silenziosa) non produce un browser corrotto.
Camoufox usa una release esatta con limite di dimensione e SHA-256 fissati;
nessun download o cambio motore avviene durante l'esecuzione.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from runtime.playwright_sidecar import browser_engine

try:
    from . import ui
except ImportError:  # standalone senza package context
    class _UI:  # minimal fallback
        @staticmethod
        def step(m): print(f"  → {m}")
        @staticmethod
        def ok(m): print(f"  ✓ {m}")
        @staticmethod
        def warn(m): print(f"  ! {m}")
        @staticmethod
        def info(m): print(f"    {m}")
        @staticmethod
        def fail(m, exit_code=1): print(f"  ✗ {m}"); sys.exit(exit_code)
    ui = _UI()  # type: ignore


def _venv_python() -> str:
    """Python owned by Metnos, never inherited from the caller's project."""
    root = Path(os.environ.get(
        "METNOS_INSTALL_ROOT", Path(__file__).resolve().parents[1]))
    venv = Path(os.environ.get("METNOS_VENV", str(root / ".venv")))
    return str(venv / "bin" / "python")


def _ensure_venv() -> bool:
    py = Path(_venv_python())
    if py.is_file():
        return True
    ui.step(f"creazione venv Metnos: {py.parent.parent}")
    py.parent.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(py.parent.parent)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not py.is_file():
        ui.warn(f"creazione venv fallita: {(result.stderr or '').strip()[-300:]}")
        return False
    ui.ok("venv Metnos creato")
    return True


# ─── pip ──────────────────────────────────────────────────────────

def _pip_install(py: str) -> bool:
    repo = Path(__file__).resolve().parent.parent
    requirements = repo / "requirements.txt"
    ui.step("pip install dipendenze core Metnos + Playwright 1.61.0")
    if requirements.is_file():
        core = subprocess.run(
            [py, "-m", "pip", "install", "--upgrade-strategy",
             "only-if-needed", "-r", str(requirements)],
            capture_output=True, text=True,
        )
        if core.returncode != 0:
            ui.warn(f"pip core fallito: {core.stderr.strip()[-300:]}")
            return False
    packages = ["playwright==1.61.0", "playwright-captcha==0.1.5"]
    if browser_engine.selected() == "camoufox":
        packages.append("camoufox==0.5.6")
    r = subprocess.run(
        [py, "-m", "pip", "install", "--upgrade",
         *packages],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        ui.warn(f"pip Playwright fallito: {r.stderr.strip()[-300:]}")
        return False
    ui.ok("Playwright installato nel venv Metnos")
    return True


# ─── chromium: piano (url + dest) via playwright stesso ──────────────

# Playwright moderno (>=1.49) divide chromium in due browser: `chromium`
# (full, usato headed) e `chromium-headless-shell` (binario headless usato
# di default da launch()). Servono ENTRAMBI.
_BROWSERS = ("chromium", "chromium-headless-shell")


def _browsers_base() -> Path:
    data = Path(os.environ.get(
        "METNOS_USER_DATA",
        str(Path.home() / ".local" / "share" / "metnos")))
    return Path(os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH", str(data / "playwright-browsers")))


def _playwright_env() -> dict[str, str]:
    return dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(_browsers_base()))


def _browser_plan(py: str, name: str) -> dict | None:
    """Per un dato browser playwright: lancia l'install normale con DEBUG
    (rete sana => completa qui), parsa url di download + revisione, e
    calcola la dir di destinazione `<name>-<rev>` e il marker atteso.
    Ritorna {url, rev_dir, complete} o None.
    """
    env = dict(_playwright_env(), DEBUG="pw:install")
    r = subprocess.run([py, "-m", "playwright", "install", name],
                       capture_output=True, text=True, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    m_url = re.search(r"from\s+(https://\S+\.zip)", out)
    m_rev = re.search(rf"{re.escape(name)}\s+v(\d+)", out)
    if not m_rev:
        return None
    # Playwright nomina la dir col nome browser in snake_case + '-<rev>'
    # (es. chromium-headless-shell -> chromium_headless_shell-1223).
    rev_dir = _browsers_base() / f"{name.replace('-', '_')}-{m_rev.group(1)}"
    return {
        "url": m_url.group(1) if m_url else None,
        "rev_dir": str(rev_dir),
        "complete": (rev_dir / "INSTALLATION_COMPLETE").exists(),
    }


# ─── download robusto (httpx, chunk + double-fetch + md5) ────────────

def _gcs_md5_hex(headers) -> str | None:
    h = headers.get("x-goog-hash", "")
    m = re.search(r"md5=([A-Za-z0-9+/=]+)", h)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(1)).hex()
    except Exception:
        return None


def _robust_fetch(url: str, dest: Path, *, chunk: int = 16_000_000) -> bool:
    """Scarica `url` in `dest` resistendo a linee instabili.

    Strategia: range per blocchi; ogni blocco scaricato due volte e
    accettato solo se i due md5 coincidono (la corruzione casuale non si
    ripete identica). md5 totale verificato contro x-goog-hash quando noto.
    """
    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120.0) as cli:
        head = cli.head(url)
        total = int(head.headers.get("content-length", 0))
        want_md5 = _gcs_md5_hex(head.headers)
        final_url = str(head.url)
        if not total:
            ui.warn("content-length assente — impossibile scaricare a blocchi")
            return False

        def _range(a: int, b: int) -> bytes | None:
            for _ in range(8):
                try:
                    rr = cli.get(final_url, headers={"Range": f"bytes={a}-{b}"})
                    if rr.status_code in (200, 206) and len(rr.content) == (b - a + 1):
                        return rr.content
                except Exception:
                    pass
            return None

        h = hashlib.md5()
        with dest.open("wb") as f:
            off = 0
            n = (total + chunk - 1) // chunk
            while off < total:
                end = min(off + chunk - 1, total - 1)
                good = None
                for attempt in range(40):
                    a = _range(off, end)
                    if a is None:
                        continue
                    b = _range(off, end)
                    if b is not None and hashlib.md5(a).digest() == hashlib.md5(b).digest():
                        good = a
                        break
                if good is None:
                    ui.warn(f"blocco {off}-{end} non verificabile dopo 40 tentativi")
                    return False
                f.write(good)
                h.update(good)
                off = end + 1
                ui.info(f"  blocco {off // chunk}/{n} ok")
        if want_md5 and h.hexdigest() != want_md5:
            ui.warn(f"md5 totale {h.hexdigest()} != atteso {want_md5}")
            dest.unlink(missing_ok=True)
            return False
    ui.ok(f"scaricato e verificato: {dest.name} ({total} byte)")
    return True


def _extract_into_rev(zip_path: Path, rev_dir: Path) -> bool:
    """Estrae lo zip CfT dentro `<name>-<rev>/` e crea il marker
    INSTALLATION_COMPLETE che playwright valida al launch. Lo zip contiene
    una sola dir top-level (chrome-linux64/ o chrome-headless-shell-linux64/),
    che e' esattamente la struttura attesa da playwright."""
    import zipfile
    rev_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            z.extractall(rev_dir)
    except Exception as e:
        ui.warn(f"estrazione fallita: {e}")
        return False
    # rendi eseguibili i binari chrome*
    for n in names:
        if n.endswith("/chrome") or n.endswith("/chrome-headless-shell"):
            try:
                (rev_dir / n).chmod(0o755)
            except Exception:
                pass
    (rev_dir / "INSTALLATION_COMPLETE").write_text("")
    return any((rev_dir / n).exists() for n in names if n.endswith(("chrome", "chrome-headless-shell")))


def _install_browsers(py: str) -> bool:
    """Installa chromium + chromium-headless-shell. Via normale prima
    (rete sana); per ognuno che resta incompleto, fallback robusto."""
    all_ok = True
    for name in _BROWSERS:
        plan = _browser_plan(py, name)
        if plan and plan["complete"]:
            ui.ok(f"{name}: presente")
            continue
        if not plan or not plan.get("url"):
            ui.warn(f"{name}: URL non determinabile da playwright")
            all_ok = False
            continue
        ui.step(f"{name}: download normale incompleto (linea?) → fallback robusto")
        zip_path = Path("/tmp") / f"metnos-{name}.zip"
        if not _robust_fetch(plan["url"], zip_path):
            all_ok = False
            continue
        if not _extract_into_rev(zip_path, Path(plan["rev_dir"])):
            all_ok = False
            continue
        zip_path.unlink(missing_ok=True)
        ui.ok(f"{name}: installato in {plan['rev_dir']}")
    return all_ok


def _install_camoufox() -> bool:
    """Install the pinned official Linux build; verify before atomic admission."""
    import httpx
    import tempfile
    import time
    import zipfile

    destination = browser_engine.browser_directory()
    try:
        try:
            browser_engine.installed_binary()
        except RuntimeError:
            if destination.exists():
                raise RuntimeError("camoufox destination exists but is not the pinned installation")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".camoufox-", dir=destination.parent) as temp:
                archive = Path(temp) / "browser.zip"
                digest = hashlib.sha256()
                size = 0
                deadline = time.monotonic() + 600
                with httpx.stream("GET", browser_engine.CAMOUFOX_URL,
                                  follow_redirects=True, timeout=60) as response:
                    response.raise_for_status()
                    with archive.open("wb") as stream:
                        for block in response.iter_bytes(1024 * 1024):
                            size += len(block)
                            if size > browser_engine.CAMOUFOX_SIZE or time.monotonic() > deadline:
                                raise RuntimeError("camoufox download limit exceeded")
                            stream.write(block)
                            digest.update(block)
                if (size != browser_engine.CAMOUFOX_SIZE or
                        digest.hexdigest() != browser_engine.CAMOUFOX_SHA256):
                    raise RuntimeError("camoufox archive SHA-256 mismatch")
                extracted = Path(temp) / "browser"
                extracted.mkdir()
                with zipfile.ZipFile(archive) as bundle:
                    for member in bundle.infolist():
                        target = (extracted / member.filename).resolve()
                        if not target.is_relative_to(extracted.resolve()):
                            raise RuntimeError("camoufox archive path outside destination")
                        bundle.extract(member, extracted)
                        mode = (member.external_attr >> 16) & 0o777
                        if mode:
                            target.chmod(mode)
                binary = extracted / "camoufox-bin"
                if not binary.is_file() or not os.access(binary, os.X_OK):
                    raise RuntimeError("camoufox executable missing")
                (extracted / "version.json").write_text(json.dumps({
                    "version": browser_engine.CAMOUFOX_VERSION,
                    "release": browser_engine.CAMOUFOX_BUILD}))
                (extracted / "metnos-install.json").write_text(json.dumps({
                    "sha256": browser_engine.CAMOUFOX_SHA256,
                    "url": browser_engine.CAMOUFOX_URL}))
                extracted.rename(destination)
            browser_engine.installed_binary()
        # The Python launcher and Firefox use different application directories.
        # Prepare both while installing, including for a read-only service home.
        for name in (".camoufox", "camoufox"):
            (Path.home() / name).mkdir(mode=0o700, exist_ok=True)
    except Exception as exc:
        ui.warn(f"Camoufox installation failed: {exc}")
        return False
    ui.ok(f"Camoufox {browser_engine.CAMOUFOX_RELEASE}: verified")
    return True


def _save_browser_configuration() -> None:
    from .services import _write

    values = {"METNOS_SITES_BROWSER_ENGINE": browser_engine.selected(),
              "METNOS_SITES_WEBSOCKETS_ALLOWED": "1" if browser_engine.websockets_allowed() else "0",
              "METNOS_SITES_STEALTH_ALLOWED": os.environ.get("METNOS_SITES_STEALTH_ALLOWED", "1"),
              "METNOS_CAMOUFOX_BROWSERS_PATH": str(browser_engine.browser_directory().parent)}
    # JSON strings, read only by the shared runtime parser, never by a shell.
    _write(browser_engine.configuration_path(), "".join(
        f"{key}={json.dumps(value)}\n" for key, value in values.items()))


# ─── systemd user unit ───────────────────────────────────────────

def _install_unit() -> bool:
    import shutil
    if not shutil.which("systemctl"):
        ui.warn("systemctl assente — salto l'installazione del service")
        return False
    repo = Path(__file__).resolve().parent.parent
    tmpl = repo / "install" / "units" / "metnos-playwright.service.tmpl"
    if not tmpl.exists():
        ui.warn(f"template unit mancante: {tmpl}")
        return False
    venv = str(Path(_venv_python()).parent.parent)  # .../.venv
    data = Path(os.environ.get(
        "METNOS_USER_DATA",
        str(Path.home() / ".local" / "share" / "metnos")))
    body = (tmpl.read_text()
            .replace("@VENV@", venv)
            .replace("@REPO_DIR@", str(repo))
            .replace("@DATA_DIR@", str(data))
            .replace("@BROWSERS_DIR@", str(_browsers_base())))
    dest_dir = Path.home() / ".config" / "systemd" / "user"
    dest_dir.mkdir(parents=True, exist_ok=True)
    display_unit = repo / "systemd" / "metnos-side-display.service"
    if shutil.which("Xvfb") and display_unit.exists():
        (dest_dir / "metnos-side-display.service").write_text(display_unit.read_text())
    else:
        ui.warn("Xvfb non disponibile: il Side browser restera' non disponibile "
                "finche' non viene installato il pacchetto xvfb")
    (dest_dir / "metnos-playwright.service").write_text(body)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    if shutil.which("Xvfb") and display_unit.exists():
        subprocess.run(["systemctl", "--user", "enable", "--now",
                        "metnos-side-display.service"], capture_output=True,
                       text=True)
    r = subprocess.run(["systemctl", "--user", "enable", "--now",
                        "metnos-playwright.service"], capture_output=True, text=True)
    if r.returncode != 0:
        ui.warn(f"enable metnos-playwright fallito: {(r.stderr or '').strip()[-200:]}")
        return False
    ui.ok("metnos-playwright.service abilitato (:8771)")
    return True


def _health_8771(timeout_s: int = 20) -> bool:
    """Probe onesto del sidecar (§2.8): 200 su /health entro timeout."""
    import time as _t
    import urllib.request
    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8771/health",
                                        timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        _t.sleep(1)
    return False


# ─── orchestrazione ──────────────────────────────────────────────

def install(*, yes: bool = False, activate: bool = True, managed: bool = False) -> dict:
    """Installa il sidecar Playwright. Ritorna note per lo stato fase."""
    try:
        browser_engine.load_configuration()
        engine = browser_engine.selected()
    except (RuntimeError, ValueError, OSError):
        return {"playwright": "browser_configuration_failed"}
    if not _ensure_venv():
        return {"playwright": "venv_failed"}
    py = _venv_python()
    if managed:
        # This environment is the immutable, hash-verified release closure.
        from importlib.metadata import PackageNotFoundError, version
        try:
            valid = (version("playwright") == "1.61.0" and
                     version("playwright-captcha") == "0.1.5" and
                     (engine != "camoufox" or version("camoufox") == "0.5.6"))
        except PackageNotFoundError:
            valid = False
        if not valid:
            return {"playwright": "pip_failed"}
    elif not _pip_install(py):
        return {"playwright": "pip_failed"}
    if not (_install_camoufox() if engine == "camoufox" else _install_browsers(py)):
        return {"playwright": f"{engine}_failed"}
    _save_browser_configuration()
    if not activate:
        return {"playwright": "prepared"}
    if not _install_unit():
        return {"playwright": "installed_no_unit"}
    # §2.8: non dichiarare "avviato" senza verificare. Probe /health.
    healthy = _health_8771()
    if healthy:
        ui.ok("metnos-playwright in salute su :8771")
    else:
        ui.warn("metnos-playwright non risponde a /health entro 20s — "
                "controlla `systemctl --user status metnos-playwright`")
    return {"playwright": "running" if healthy else "started_unhealthy"}


def main() -> int:
    yes = "--yes" in sys.argv or "-y" in sys.argv
    ui.step("JS-render sidecar (locale, nessun provider esterno)")
    prepare = "--prepare" in sys.argv
    notes = install(yes=yes, activate=not prepare)
    print(notes)
    return 0 if notes.get("playwright", "") in ("running", "started_unhealthy", "prepared") else 1


if __name__ == "__main__":
    sys.exit(main())
