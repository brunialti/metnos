# SPDX-License-Identifier: AGPL-3.0-only
"""Phase 6 — First boot.

Final phase. After all the moving parts are in place, this:

- generates a one-shot admin onboarding URL with the HMAC token from
  ``~/.config/metnos/admin.key`` (so the user can claim the web
  dashboard without re-authenticating)
- prints a Telegram pairing snippet if the bot is enabled
- writes a Markdown summary at
  ``$METNOS_HOME/install_summary.md`` so the user has a single doc
  recording every choice they made
- opens the browser to the dashboard if ``$DISPLAY`` / ``$WAYLAND_DISPLAY``
  is set and the user agrees

After this phase finishes, Metnos is fully installed.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import i18n, state, ui


def _onboard_token(admin_key_hex: str) -> str:
    """One-shot HMAC token good for 15 minutes."""
    expires = int(time.time()) + 15 * 60
    nonce = secrets.token_hex(8)
    payload = f"{expires}.{nonce}"
    sig = hmac.new(
        bytes.fromhex(admin_key_hex), payload.encode(), hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload}.{sig}"


def _read_admin_key() -> str | None:
    p = Path(os.environ.get("METNOS_USER_CONFIG", Path.home() / ".config" / "metnos")) / "admin.key"
    if not p.exists():
        return None
    return p.read_text().strip()


def _private_ipv4(value: str) -> str:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return ""
    if (
        address.version != 4
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or not address.is_private
    ):
        return ""
    return str(address)


def _lan_ipv4_addresses() -> tuple[str, ...]:
    """Return useful private IPv4 addresses, with the default route first."""
    found: list[str] = []

    def add(value: str) -> None:
        normalized = _private_ipv4(value)
        if normalized and normalized not in found:
            found.append(normalized)

    override = os.environ.get("METNOS_INSTALL_LAN_IP", "").strip()
    if override:
        add(override)

    # A UDP connect selects the default interface without sending application
    # data. It works even when DNS is unavailable.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            add(probe.getsockname()[0])
    except OSError:
        pass

    # Fallback for a LAN with no default route. Avoid container/bridge links,
    # which are not useful addresses for a browser on another device.
    try:
        import psutil

        ignored_prefixes = ("docker", "veth", "br-", "virbr", "lo")
        stats = psutil.net_if_stats()
        for name, addresses in sorted(psutil.net_if_addrs().items()):
            if name.startswith(ignored_prefixes) or not stats.get(name, None):
                continue
            if not stats[name].isup:
                continue
            for address in addresses:
                if address.family == socket.AF_INET:
                    add(address.address)
    except (ImportError, OSError):
        pass
    return tuple(found)


def _connection_urls(
    port: int,
    bind_host: str,
    *,
    lan_addresses: tuple[str, ...] | None = None,
) -> tuple[str, tuple[str, ...]]:
    local_url = f"http://127.0.0.1:{port}/"
    host = str(bind_host or "127.0.0.1").strip()
    if host == "127.0.0.1":
        return local_url, ()
    if host != "0.0.0.0":
        address = _private_ipv4(host)
        return local_url, ((f"http://{address}:{port}/",) if address else ())
    addresses = lan_addresses if lan_addresses is not None else _lan_ipv4_addresses()
    return local_url, tuple(f"http://{address}:{port}/" for address in addresses)


def _write_summary(
    rows: list[dict],
    *,
    port: int = 8770,
    bind_host: str = "0.0.0.0",
    lan_urls: tuple[str, ...] | None = None,
) -> Path:
    home = Path(os.environ.get("METNOS_USER_DATA", Path.home() / ".local" / "share" / "metnos"))
    home.mkdir(parents=True, exist_ok=True)
    p = home / "install_summary.md"

    lines = [
        "# Metnos installation summary",
        "",
        f"_Generated {datetime.now().isoformat(timespec='seconds')}_",
        "",
        "## Phases",
        "",
        "| # | Name | Status | Notes |",
        "|--:|------|:------:|-------|",
    ]
    for r in rows:
        status = "✓ done" if r["done"] else "⋯ pending"
        notes = ", ".join(f"`{k}={v}`" for k, v in (r.get("notes") or {}).items()) or "—"
        lines.append(f"| {r['phase']} | {r['name']} | {status} | {notes} |")

    local_url, detected_lan_urls = _connection_urls(port, bind_host)
    resolved_lan_urls = detected_lan_urls if lan_urls is None else tuple(lan_urls)
    connection_lines = [f"- On this server: `{local_url}`"]
    connection_lines.extend(
        f"- From another device on the same private LAN: `{url}`"
        for url in resolved_lan_urls
    )
    if not resolved_lan_urls:
        if bind_host == "127.0.0.1":
            connection_lines.append(
                "- Other devices are not enabled for this installation "
                "(`METNOS_HTTP_HOST=127.0.0.1`)."
            )
        else:
            connection_lines.append(
                "- No active private-LAN IPv4 address was detected. Connect "
                "the network interface, then run `ip -brief address`."
            )

    lines += [
        "",
        "## Files of interest",
        "",
        "- `~/.config/metnos/admin.key` — HMAC key for admin onboarding (mode 0600)",
        "- `~/.config/metnos/llm_tiers.toml` — tier routing config",
        "- `~/.local/share/metnos/install_summary.md` — this file",
        "- `<METNOS_INSTALL_ROOT>/.venv/` — Python virtual environment",
        "- `~/.local/state/metnos/install/phase*.done` — phase sentinels (delete to re-run)",
        "",
        "## Day-2 commands",
        "",
        "```bash",
        "systemctl --user status metnos-http",
        "systemctl --user restart metnos-http",
        "journalctl --user -u metnos-http -f",
        "python -m install --force-phase 4   # re-run secrets dialog",
        "python -m install.sidecar --list    # optional self-hosted sidecars",
        "python -m install.sidecar searxng   # add self-hosted web search",
        "```",
        "",
        "## Optional sidecars",
        "",
        "Self-hosted companion services you can add any time (each is a "
        "user-level systemd unit, no sudo):",
        "",
        "- **SearXNG** — self-hosted web search backing `find_urls`. "
        "`python -m install.sidecar searxng`",
        "- **Photon** / **VLM** — offline geocoding / image captions "
        "(installers coming soon).",
        "",
        "## How to connect",
        "",
        "### Web UI",
        "",
        *connection_lines,
        "",
        "Use the LAN URL only from the same trusted private network. Metnos serves",
        "plain HTTP by default: do not forward this port from a router or expose it",
        "directly to the Internet.",
        "",
        "The first connection needs the one-shot onboarding URL printed during",
        "installation (valid for 15 minutes). Re-run `python -m install",
        "--force-phase 6` to issue a fresh URL.",
        "",
        "### Telegram",
        "",
        "If configured, open your BotFather bot, send `/start`, and paste the pairing",
        "code from the Web UI. To configure it later, run `python -m install",
        "--force-phase 4`.",
        "",
        "## Next steps",
        "",
        "- Claim admin access via the one-shot onboarding URL printed during install (15 min).",
        "- Read the full architecture at https://metnos.com",
        "- Issues / questions: https://github.com/brunialti/metnos/issues",
        "",
    ]
    p.write_text("\n".join(lines))
    return p


def _select_skills(args: Any) -> dict[str, bool]:
    """Pick which first-party SKILLS (modular capabilities) start enabled.

    All default to ON (auto_enable) so a fresh install matches the reference
    instance; a skill you enable but haven't configured stays DORMANT (visible,
    inert) until its prerequisite is met. You can change this any time later
    with ``metnos-skills enable/disable`` or by asking in chat. Honours
    ``--yes`` (enable every auto_enable default, no prompts)."""
    try:
        from runtime.skills_catalog import FIRST_PARTY_SKILLS
        from runtime.skill_registry import set_skill_enabled
    except Exception as e:  # pragma: no cover — never block first boot on this
        ui.warn(i18n.t("p6_skill_unavailable", err=e))
        return {}
    ui.step(i18n.t("p6_step_skills"))
    ui.info(i18n.t("p6_skills_info"))
    decisions: dict[str, bool] = {}
    for sk in FIRST_PARTY_SKILLS:
        name = sk["name"]
        default_on = bool(sk.get("auto_enable", True))
        if getattr(args, "yes", False):
            enabled = default_on
        else:
            enabled = ui.confirm(i18n.t(
                "p6_skill_confirm",
                name=name,
                desc=sk.get("desc", ""),
                requires=sk.get("requires", "—"),
            ), default=default_on)
        try:
            set_skill_enabled(name, enabled)
        except Exception as e:  # pragma: no cover
            ui.warn(i18n.t("p6_skill_persist_failed", name=name, err=e))
        decisions[name] = enabled
    on = [k for k, v in decisions.items() if v]
    skills = ", ".join(on) if on else i18n.t("p6_skills_core_only")
    ui.ok(i18n.t("p6_skills_enabled", skills=skills))
    return decisions


def run(args: Any) -> dict[str, Any]:
    notes: dict[str, Any] = {}
    ui.banner(i18n.t("p6_banner_title"), i18n.t("p6_banner_subtitle"))

    # Pull port + telegram + service state from phase 4/5
    phase4 = state.load(4)
    phase5 = state.load(5)
    port = (phase5.notes.get("http_port") if phase5 else None) or (phase4.notes.get("http_port") if phase4 else 8770)
    http_host = (
        (phase5.notes.get("http_host") if phase5 else None)
        or (phase4.notes.get("http_host") if phase4 else None)
        or "127.0.0.1"
    )
    local_url, lan_urls = _connection_urls(port, http_host)
    telegram_on = bool(phase4 and phase4.notes.get("telegram"))
    http_enabled = bool(phase5 and phase5.notes.get("http_enabled"))
    http_healthy = bool(phase5 and phase5.notes.get("http_healthy"))

    # 0. If the HTTP service never started, the onboarding URL would be a
    #    dead link. Be honest (§2.8): tell the user how to recover instead.
    if not http_enabled:
        ui.warn(i18n.t("p6_http_not_running"))
        ui.console().print(i18n.t("p6_recover_head"))
        ui.console().print(i18n.t("p6_recover_inspect"))
        ui.console().print(i18n.t("p6_recover_logs"))
        ui.console().print(i18n.t("p6_recover_rerun"))
        ui.console().print()
        notes["http_enabled"] = False
    elif not http_healthy:
        ui.warn(i18n.t("p6_http_unhealthy"))

    # 1. Onboarding URL (only meaningful once the service is up)
    admin_key = _read_admin_key()
    if admin_key and http_enabled:
        token = _onboard_token(admin_key)
        bases = (local_url, *lan_urls)
        urls = tuple(f"{base}admin/onboard?t={token}" for base in bases)
        ui.console().print()
        ui.console().print(i18n.t("p6_onboard_head_many"))
        for url in urls:
            ui.console().print(f"    [link={url}]{url}[/link]")
        ui.console().print()
        notes["onboard_url_emitted"] = True
    elif not admin_key:
        ui.warn(i18n.t("p6_onboard_no_key"))
        notes["onboard_url_emitted"] = False
    else:
        # admin.key present but the service is down — URL deferred, not emitted.
        ui.info(i18n.t("p6_onboard_deferred"))
        notes["onboard_url_emitted"] = False

    # 2. How to connect — Web UI (needs the admin key on first connect)
    ui.console().print(i18n.t("p6_webui_head"))
    ui.console().print(i18n.t("p6_webui_local_url", url=local_url))
    for url in lan_urls:
        ui.console().print(i18n.t("p6_webui_lan_url", url=url))
    if not lan_urls and http_host == "127.0.0.1":
        ui.console().print(i18n.t("p6_webui_lan_disabled"))
    elif not lan_urls:
        ui.console().print(i18n.t("p6_webui_lan_missing"))
    ui.console().print(i18n.t("p6_webui_private_warning"))
    ui.console().print(i18n.t("p6_webui_keynote"))
    ui.console().print()

    # 3. How to connect — Telegram
    if telegram_on:
        ui.console().print(i18n.t("p6_tg_connect_head"))
        ui.console().print(i18n.t("p6_tg_connect_1"))
        ui.console().print(i18n.t("p6_tg_connect_2"))
        ui.console().print(i18n.t("p6_tg_connect_3"))
    else:
        ui.console().print(i18n.t("p6_tg_disabled_head"))
        ui.console().print(i18n.t("p6_tg_disabled_1"))
        ui.console().print(i18n.t("p6_tg_disabled_2"))
        ui.console().print(i18n.t("p6_tg_disabled_3"))
    ui.console().print()

    # 2b. Skill selection (modular capabilities)
    notes["skills"] = _select_skills(args)

    # 3. Write the summary
    ui.step(i18n.t("p6_step_summary"))
    summary_path = _write_summary(
        state.summary(), port=port, bind_host=http_host, lan_urls=lan_urls,
    )
    ui.ok(i18n.t("p6_summary_at", path=summary_path))
    notes["summary_path"] = str(summary_path)

    # 4. Final note — honest about whether the service is actually up.
    ui.console().print()
    if http_enabled and http_healthy:
        ui.console().print(i18n.t("p6_final_done"))
    elif http_enabled:
        ui.console().print(i18n.t("p6_final_started"))
    else:
        ui.console().print(i18n.t("p6_final_not_running"))
    ui.console().print(i18n.t("p6_final_anytime"))
    notes["http_host"] = http_host
    notes["web_ui_urls"] = [local_url, *lan_urls]

    return notes
