"""Watcher standalone per progress.json del batch enrichment.

Spawnato via `systemd-run --user`, sopravvive a chiusura sessione Claude
e SSH grazie a linger=yes. Polla `progress.json` ogni 30s. Quando vede
`phase=done`, invia notifica Telegram al `default_chat_id` e termina.

Uso:
    systemd-run --user --unit=metnos-progress-watcher \\
        --setenv=METNOS_PROGRESS_FILE=<path> \\
        --setenv=METNOS_BATCH_LABEL="enrichment foto" \\
        /opt/suprastructure/.venv/bin/python <install_root>/runtime/watch_progress_telegram.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

POLL_S = 30
MAX_WAIT_S = 24 * 3600  # 24h cap


def main() -> int:
    prog_path = os.environ.get("METNOS_PROGRESS_FILE")
    if not prog_path:
        print("ERROR: METNOS_PROGRESS_FILE env not set", file=sys.stderr)
        return 2
    label = os.environ.get("METNOS_BATCH_LABEL", "batch")
    p = Path(prog_path)

    _RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
        str(pp / "runtime") for pp in Path(__file__).resolve().parents
        if (pp / "runtime" / "config.py").is_file())
    if _RUNTIME not in sys.path:
        sys.path.insert(0, _RUNTIME)
    from channels.telegram import TelegramChannel  # type: ignore
    from channels import OutboundMessage  # type: ignore

    start = time.time()
    print(f"watch_progress_telegram: polling {p} every {POLL_S}s "
          f"(label={label!r})", flush=True)

    last_pct = -1.0
    while time.time() - start < MAX_WAIT_S:
        try:
            data = json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(POLL_S)
            continue

        phase = data.get("phase")
        pct = data.get("pct", 0)
        if pct != last_pct:
            print(f"  phase={phase} pct={pct}% n={data.get('n_processed')} "
                  f"ok={data.get('ok')} fail={data.get('fail')}", flush=True)
            last_pct = pct

        if phase == "done":
            ok = data.get("ok_count") or data.get("ok") or 0
            fail = data.get("fail_count") or data.get("fail") or 0
            n_total = data.get("n_total", 0)
            model_text = data.get("model_text", "?")
            model_vlm = data.get("model_vlm", "?")
            elapsed = int(time.time() - start)
            elapsed_h = elapsed // 3600
            elapsed_m = (elapsed % 3600) // 60
            text = (
                f"Metnos: {label} completato. "
                f"{ok}/{n_total} ok / {fail} fail. "
                f"VLM={model_vlm} text={model_text}. "
                f"Tempo (dal watcher start): {elapsed_h:02d}:{elapsed_m:02d}."
            )
            try:
                ch = TelegramChannel()
                resp = ch.send(ch.default_chat_id, OutboundMessage(text=text))
                print(f"telegram sent: {resp}", flush=True)
                return 0
            except Exception as ex:
                print(f"ERROR sending Telegram: {ex!r}", file=sys.stderr)
                return 3

        time.sleep(POLL_S)

    print("watch_progress_telegram: timeout 24h, exiting", file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
