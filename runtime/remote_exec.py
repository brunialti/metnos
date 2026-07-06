"""runtime.remote_exec — ponte fra il dispatch e gli executor remoti.

Quando il placement decide `device`, il runtime NON esegue `invoke_executor`
locale: accoda un'invocazione firmata (invocations.enqueue_invocation) e
attende il result consegnato dal client via POST /agent/result. Il result
ha la stessa shape §2.6 di un executor locale: il chiamante non distingue.

Errori onesti §2.8 (mai silenzio):
- device mai raggiunto entro la deadline -> ok:false + ERR_DEVICE_TIMEOUT
  (l'invocazione resta in coda finche' il device non ri-polla; il turno
  corrente pero' si chiude con l'errore, niente attese indefinite).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import invocations  # noqa: E402
from messages import get as _msg  # noqa: E402

from logging_setup import get_logger
log = get_logger(__name__)

# Margine oltre la deadline_ms dell'invocazione prima di dichiarare timeout
# lato server (il client ha bisogno di un giro di poll per prenderla).
WAIT_MARGIN_S = 15

# Deadline scalata per invocazioni MUTANTI di massa (stopgap A.0, bug live
# 1ba8e2c4 6/7: delete di 681 file sotto deadline 30s → job-object uccide il
# processo A METÀ: esecuzione parziale reale, results persi, undo orfano,
# final che dichiara il falso). 1s/item, cap 10min. Il fix pieno è il
# chunking per-invocazione (spec fase 7 A.0).
SCALE_MIN_ITEMS = 10
SCALE_S_PER_ITEM = 1
SCALE_CAP_S = 600


def _scaled_timeout_s(timeout_s: int, args: dict | None,
                      reversibility: str) -> int:
    """Timeout effettivo per l'invocazione remota. Read-only invariati;
    mutanti con vettori grandi (>SCALE_MIN_ITEMS) → base + 1s/item, cap
    SCALE_CAP_S. Deterministico §7.9 (conta il max fra gli arg lista)."""
    if reversibility == "read_only":
        return int(timeout_s)
    n_items = max((len(v) for v in (args or {}).values()
                   if isinstance(v, list)), default=0)
    if n_items <= SCALE_MIN_ITEMS:
        return int(timeout_s)
    return min(SCALE_CAP_S,
               max(int(timeout_s), 30 + n_items * SCALE_S_PER_ITEM))


def _register_i18n_keys() -> None:
    """Chiavi user-facing del sottosistema remoto (§11: mai stringhe
    hardcoded). Idempotente; il seed bundled le include (test gate)."""
    try:
        import i18n
        i18n.register_key_if_missing(
            "ERR_DEVICE_UNREACHABLE",
            "il dispositivo '{name}' non e' raggiungibile (nessun heartbeat recente)",
            "device '{name}' is not reachable (no recent heartbeat)",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_UNKNOWN",
            "il dispositivo '{name}' non risulta appaiato",
            "device '{name}' is not paired",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_AMBIGUOUS",
            "piu' dispositivi disponibili: specifica il nome del dispositivo",
            "multiple devices available: specify the device name",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_NONE_AVAILABLE",
            "nessun dispositivo raggiungibile per questa operazione",
            "no device is reachable for this operation",
            needs_translation=False)
        i18n.register_key_if_missing(
            "ERR_DEVICE_TIMEOUT",
            "il dispositivo '{name}' non ha confermato entro {seconds}s. "
            "L'operazione potrebbe comunque completarsi appena il dispositivo "
            "risponde: se è una modifica, resterà annullabile.",
            "device '{name}' did not confirm within {seconds}s. The operation "
            "may still complete once the device responds: if it changes data, "
            "it will remain undoable.",
            needs_translation=False)
    except Exception as _e:
        log.warning("registrazione chiavi i18n remote fallita: %s", _e)


_register_i18n_keys()


def invoke_remote(executor, args: dict, device_id: str, *,
                  timeout_s: int = 30,
                  turn_id: str | None = None,
                  reversibility: str | None = None,
                  env_injections: dict | None = None,
                  actor: str = "", channel: str = "") -> dict:
    """Esegue `executor` sul device remoto e ritorna il result (shape §2.6).

    `executor` e' la dataclass loader.Executor (serve name + revertible).
    Bloccante come invoke_executor locale: il chiamante e' il runtime sync.
    """
    from devices import get_device
    dev = get_device(device_id)
    dev_name = dev.name if dev else device_id[:12]

    rev = reversibility or (
        "revertible" if getattr(executor, "revertible", False) else "read_only")
    timeout_s = _scaled_timeout_s(timeout_s, args, rev)
    deadline_ms = int(timeout_s) * 1000
    # METNOS_TURN_ID nell'env del sandbox device (bug live 1ba8e2c4, 6/7): il
    # client fa env_clear() → senza iniezione l'executor revertibile scrive i
    # blob in `_history/no_turn/blob` invece che per-turno, e l'undo device
    # (ADR 0183) non li ritrova. Viaggia SOLO nel payload firmato (mTLS), mai a
    # riposo sul device. `env_injections` esplicito ha precedenza.
    env = dict(env_injections or {})
    if turn_id:
        env.setdefault("METNOS_TURN_ID", turn_id)
    invocation_id = invocations.enqueue_invocation(
        device_id,
        executor.name,
        args or {},
        turn_id=turn_id,
        scope="device",
        reversibility=rev,
        env_injections=env or None,
        deadline_ms=deadline_ms,
        origin_actor=actor or "",
        origin_channel=channel or "",
    )

    wait_s = timeout_s + WAIT_MARGIN_S
    result = invocations.wait_result(invocation_id, wait_s)
    if result is None:
        log.warning("invocation %s senza result entro %ds (device %s)",
                    invocation_id, wait_s, device_id[:12])
        # A.0 (fase 7): il turno smette di attendere ma l'invocazione RESTA in
        # coda — il device può completarla più tardi. Marcala ABBANDONATA così
        # il submit tardivo chiude l'undo orfano (§2.8: mai un'op mutante che
        # gira e resta non annullabile). Messaggio ONESTO sull'incertezza.
        invocations.mark_abandoned(invocation_id)
        return {
            "ok": False,
            "error": _msg("ERR_DEVICE_TIMEOUT", name=dev_name, seconds=wait_s),
            "error_class": "remote_timeout",
            "invocation_id": invocation_id,
            "device_id": device_id,
        }

    # Il result e' gia' verificato (device_sig) da complete_invocation.
    # L'output COMPLETO dell'executor (§2.6: entries|results MA anche le chiavi
    # di dominio come total_lines/by_path/summary) viaggia in `payload`: il
    # runtime lo consuma ESATTAMENTE come un result locale. Senza questo il
    # round-trip perdeva tutto ciò che non era `entries` (bug live 3/7:
    # compute_files_loc → n_processed 5 ma entries [] e nessun dato LOC).
    # I metadati di trasporto (sandbox, elapsed_ms) vanno sotto `_remote`,
    # namespaced per non collidere con le chiavi dell'executor.
    payload = result.get("payload")
    if isinstance(payload, dict) and payload:
        merged = dict(payload)
        merged.setdefault("ok", bool(result.get("ok")))
        merged["_remote"] = {
            "device_id": device_id,
            "invocation_id": invocation_id,
            "sandbox": result.get("sandbox"),
            "elapsed_ms": result.get("elapsed_ms"),
        }
        return merged
    # Client pre-payload (0.2.5 e precedenti, non ancora reinstallato): thin
    # body coi soli campi §6.3. Fallback trasparente durante il rollout.
    result.setdefault("ok", False)
    return result
