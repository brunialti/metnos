"""Explicit instance browser selection; runtime never downloads a browser."""
from __future__ import annotations

import json
import os
import platform
import sys
from importlib.metadata import version
from pathlib import Path

CAMOUFOX_PACKAGE = "0.5.6"
CAMOUFOX_VERSION = "156.0.1"
CAMOUFOX_BUILD = "beta.33"
CAMOUFOX_RELEASE = f"{CAMOUFOX_VERSION}-{CAMOUFOX_BUILD}"
CAMOUFOX_URL = (
    "https://github.com/daijro/camoufox/releases/download/"
    f"v{CAMOUFOX_RELEASE}/camoufox-{CAMOUFOX_RELEASE}-lin.x86_64.zip"
)
CAMOUFOX_SHA256 = "730d731153b2a16cac238a0b4a7f849c04d1fd6181c09ec6af29695af0407af8"
CAMOUFOX_SIZE = 1294876837
_CHROMIUM_TECHNIQUES = frozenset({
    "webdriver_launch_arg", "ua_override", "mobile_emulation",
    "chrome_permissions_js",
})
_CONFIGURATION_KEYS = frozenset({
    "METNOS_SITES_BROWSER_ENGINE", "METNOS_SITES_WEBSOCKETS_ALLOWED",
    "METNOS_SITES_STEALTH_ALLOWED", "METNOS_CAMOUFOX_BROWSERS_PATH",
})


def configuration_path() -> Path:
    return Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local/share/metnos")) / "browser-engine.env"


def load_configuration() -> None:
    """Load only browser choices, including under the signed minimal environment.

    This is data, never shell input. Explicit process settings take precedence,
    in particular an administrative false stealth ceiling.
    """
    path = configuration_path()
    if not path.is_file():
        return
    values = {}
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name in _CONFIGURATION_KEYS:
            parsed = json.loads(value)
            if not isinstance(parsed, str):
                raise ValueError("browser_configuration_value_invalid")
            values[name] = parsed
    for name, value in values.items():
        os.environ.setdefault(name, value)


def websockets_allowed() -> bool:
    """Explicit instance exception; WebSockets bypass the HTTP host guard."""
    return os.environ.get("METNOS_SITES_WEBSOCKETS_ALLOWED", "0").strip().lower() in {
        "1", "true", "yes", "on"}


def selected() -> str:
    engine = os.environ.get("METNOS_SITES_BROWSER_ENGINE", "chromium").strip().lower()
    if engine not in {"chromium", "camoufox"}:
        raise RuntimeError("invalid_browser_engine")
    if engine == "camoufox":
        if os.environ.get("METNOS_SITES_STEALTH_ALLOWED", "1").strip().lower() in {
                "0", "false", "no", "off"}:
            raise RuntimeError("camoufox_disabled_by_stealth_ceiling")
        # Playwright's isolated-world shim cannot intercept native sockets
        # created by site scripts in Camoufox. Never claim they are blocked.
        if not websockets_allowed():
            raise RuntimeError("camoufox_requires_websockets_allowed")
        if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
            raise RuntimeError("camoufox_platform_unsupported")
    return engine


async def apply_websocket_policy(context) -> None:
    if websockets_allowed():
        return
    selected()  # Fail closed if the engine cannot enforce the default policy.

    async def close_socket(socket):
        await socket.close()

    await context.route_web_socket("**/*", close_socket)


def incompatible_techniques(techniques) -> tuple[str, ...]:
    if selected() != "camoufox":
        return ()
    return tuple(sorted(set(techniques) & _CHROMIUM_TECHNIQUES))


def browser_directory() -> Path:
    data = Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local/share/metnos"))
    base = Path(os.environ.get("METNOS_CAMOUFOX_BROWSERS_PATH", data / "camoufox-browsers"))
    return base / CAMOUFOX_RELEASE


def installed_binary() -> Path:
    root = browser_directory()
    try:
        receipt = json.loads((root / "metnos-install.json").read_text())
        metadata = json.loads((root / "version.json").read_text())
    except (OSError, ValueError):
        raise RuntimeError("camoufox_installation_missing") from None
    binary = root / "camoufox-bin"
    if (not isinstance(receipt, dict) or receipt.get("sha256") != CAMOUFOX_SHA256 or
            metadata != {"version": CAMOUFOX_VERSION, "release": CAMOUFOX_BUILD} or
            not binary.is_file() or not os.access(binary, os.X_OK)):
        raise RuntimeError("camoufox_installation_mismatch")
    return binary


def prepare_environment(environment: dict[str, str]) -> None:
    """Once at startup, before importing Camoufox's platformdirs cache paths."""
    if selected() != "camoufox":
        return
    if version("camoufox") != CAMOUFOX_PACKAGE or version("playwright") != "1.61.0":
        raise RuntimeError("camoufox_package_version_mismatch")
    installed_binary()
    # Firefox probes this even with Playwright's temporary isolated profiles.
    # The installer creates it; runtime need not write outside app roots.
    if not (Path.home() / ".camoufox").is_dir():
        raise RuntimeError("camoufox_profile_directory_missing")
    os.environ["XDG_CACHE_HOME"] = environment["XDG_CACHE_HOME"]


async def launch(playwright, *, headless: bool, environment: dict[str, str]):
    if selected() != "camoufox":
        raise RuntimeError("camoufox_not_selected")
    from camoufox.addons import DefaultAddons
    from camoufox.async_api import AsyncNewBrowser
    # The explicit executable and version avoid pkgman's implicit fetch / active
    # channel. No downloaded/solver extensions, GeoIP service, humanization
    # or main-world bypass. Native sockets require the explicit instance choice.
    return await AsyncNewBrowser(
        playwright, executable_path=str(installed_binary()),
        ff_version=int(CAMOUFOX_VERSION.split(".")[0]),
        i_know_what_im_doing=True, os="linux", headless=headless,
        window=(1280, 800), env=environment, exclude_addons=list(DefaultAddons),
        addons=[],
        geoip=False, humanize=False, main_world_eval=False, disable_coop=False,
        block_webrtc=True, firefox_user_prefs={
            # Required by Firefox request interception. Each broker context
            # blocks registration with Playwright's service_workers="block".
            "dom.serviceWorkers.enabled": True,
            "network.prefetch-next": False,
            "network.dns.disablePrefetch": True,
            "network.http.speculative-parallel-limit": 0,
        },
    )
