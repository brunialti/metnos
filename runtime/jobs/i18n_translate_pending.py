"""Task scheduler v2: traduce le righe pending del DB i18n.

Trigger automatico `daily@02:00`. Pesca fino a N righe con
`needs_translation=1` dal DB `~/.local/share/metnos/i18n.sqlite`,
invoca il LLM per ogni riga con prompt strict-JSON
`{"translation": "..."}` preservando i placeholder `{var}`, e salva il
risultato con `needs_translation=0`. Idempotente sul `source_hash`: se
la riga e' gia' stata tradotta e il testo sorgente non e' cambiato dal
salvataggio precedente, viene saltata.

Il tier LLM e' il contratto centrale ``translation.i18n``. Cap N=20 per fire
per non saturare la GPU notturna.
Audit JSONL append-only in `~/.local/share/metnos/i18n_audit/<YYYY-MM-DD>.jsonl`.

Determinismo §7.9: tutto deterministico tranne la singola call LLM di
traduzione (irriducibilmente generativa). Migration colonne idempotente;
fallback LLM crash → skip riga + log.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("metnos.jobs.i18n_translate_pending")


# Cap throttling per fire. La GPU locale Strix Halo serve VLM + planner;
# ~20 traduzioni col contratto `translation.i18n` → wise assorbono ~60s GPU/fire. Ora
# configurabile (era hardcoded): con cadenza every_6h, 4 fire/giorno × cap.
# Alzalo per drenare prima il backlog (al costo di burst GPU diurni piu' lunghi).
CAP_PER_FIRE = int(os.environ.get("METNOS_I18N_CAP_PER_FIRE", "20"))

# Default DB e audit dir; override via env per i test.
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11
_DEFAULT_DB = _C.DB_I18N
_DEFAULT_AUDIT_DIR = _C.PATH_USER_DATA / "i18n_audit"


def _db_path() -> Path:
    """Permette ai test di puntare a un DB temporaneo via env."""
    env = os.environ.get("METNOS_I18N_DB")
    return Path(env) if env else _DEFAULT_DB


def _audit_dir() -> Path:
    env = os.environ.get("METNOS_I18N_AUDIT_DIR")
    return Path(env) if env else _DEFAULT_AUDIT_DIR


def _lock_path() -> Path:
    return _db_path().with_name(_db_path().name + ".translate.lock")


@contextmanager
def _exclusive_translation_lock():
    """Lock cross-process tra timer systemd e fallback scheduler v2.

    Il file resta intenzionalmente sul disco; `flock` è rilasciato dal kernel
    anche su crash/kill del processo, quindi non esistono lock stantii.
    """
    import fcntl

    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _tier() -> str:
    """Tier dichiarativo della traduzione; nessun tuning per-call."""
    from llm_workloads import tier_for
    return tier_for("translation.i18n")


def _tier_label(tier: str) -> str:
    level = getattr(tier, "level", None)
    return f"{tier}.{level}" if level else str(tier)


from timefmt import now_iso_z as _now_iso


from timefmt import today_iso as _today_iso_date


from i18n import _hash_text as _sha256_short  # SoT 16-char (era copia: drift)
from i18n import _sha256_full as _version_hash  # `sha256:<hex>` catalogo


def _sha256_full(text: str) -> str:
    """Hash sha256 hex NUDO (per audit del prompt usato). NB: NON usare
    `i18n._sha256_full` qui — quello ritorna la forma prefissata `sha256:<hex>`
    (version_hash latest-wins), mentre `prompt_hash_full` in DB e' hex nudo.
    Semantiche diverse: tenuto separato di proposito (no DRY su forme divergenti)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _ensure_schema(conn: sqlite3.Connection) -> list[str]:
    """Migration idempotente per le colonne richieste dal task.

    `source_hash` esiste gia' nello schema base (vedi `i18n.py`). Aggiungo
    `translated_at_iso` e `translated_by` se mancano. Ritorna la lista
    delle colonne effettivamente aggiunte in questa chiamata.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(i18n)").fetchall()}
    added: list[str] = []
    if "source_hash" not in cols:
        conn.execute("ALTER TABLE i18n ADD COLUMN source_hash TEXT")
        added.append("source_hash")
    if "translated_at_iso" not in cols:
        conn.execute("ALTER TABLE i18n ADD COLUMN translated_at_iso TEXT")
        added.append("translated_at_iso")
    if "translated_by" not in cols:
        conn.execute("ALTER TABLE i18n ADD COLUMN translated_by TEXT")
        added.append("translated_by")
    if "auto_translated" not in cols:
        # Fase 11(c) wire-in 19/5/2026 v4: flag per stub `<auto-synth: ...>`
        # registrati post-stage5 e poi materializzati dal daemon via LLM.
        # 1 = testo generato da LLM auto, da review admin.
        conn.execute("ALTER TABLE i18n ADD COLUMN auto_translated INTEGER DEFAULT 0")
        added.append("auto_translated")
    if added:
        conn.commit()
    return added


_AUTO_SYNTH_PREFIX = "<auto-synth: "


def _materialize_auto_synth_stubs(conn: sqlite3.Connection, tier: str, cap: int) -> dict:
    """Fase 11(c) c1+flag 19/5/2026: stub registrati da synt_multistage.

    Trova row con `text LIKE '<auto-synth: KEY>'` (entrambe le lingue),
    genera testo user-facing via LLM dato il nome semantico della chiave,
    UPDATE text + `auto_translated=1` + mantiene `needs_translation=1`
    per review admin.

    Per la stessa key processa IT+EN nella stessa call LLM (output JSON
    `{"it": "...", "en": "..."}`). Idempotente: se la riga non e' piu'
    stub (gia' materializzata o editata da admin) viene saltata.

    Ritorna metadata: `{processed, generated, errors}`.
    """
    from llm_helpers import call_llm

    rows = conn.execute(
        "SELECT DISTINCT key FROM i18n "
        "WHERE text LIKE ? ORDER BY key LIMIT ?",
        (_AUTO_SYNTH_PREFIX + "%", cap),
    ).fetchall()
    if not rows:
        return {"processed": 0, "generated": 0, "errors": 0}

    sys_prompt = (
        "Sei un esperto di UX per Metnos. Dato il NOME di una chiave i18n "
        "(es. ERR_XML_PARSE_FAIL, MSG_OPERATION_DONE), genera UNA frase "
        "breve user-facing in italiano E in inglese che spieghi cosa "
        "comunica al utente. Includi placeholder {var} se la chiave "
        "suggerisce parametri. Output SOLO JSON `{\"it\":\"...\",\"en\":\"...\"}`. "
        "Tono coerente: ERR_=problema/errore, MSG_=info/conferma, "
        "WARN_=avviso, LOG_=audit tecnico (1 riga concisa)."
    )
    processed = 0
    generated = 0
    errors = 0
    for (key,) in rows:
        processed += 1
        prompt = (
            f"Chiave i18n: `{key}`\n"
            f"Famiglia: {key.split('_', 1)[0]}_\n"
            f"Genera testo IT+EN."
        )
        try:
            text, _meta = call_llm(
                prompt, sys_prompt, tier=tier, max_tokens=400)
        except Exception as ex:
            log.warning("materialize stub LLM crash key=%s: %r", key, ex)
            errors += 1
            continue
        # Parse JSON `{"it": "...", "en": "..."}`
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n", "", text)
            text = re.sub(r"\n```\s*$", "", text)
        try:
            obj = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, flags=re.DOTALL)
            obj = None
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    obj = None
        if not isinstance(obj, dict):
            log.warning("materialize stub bad JSON key=%s text=%r", key, text[:120])
            errors += 1
            continue
        it_text = obj.get("it")
        en_text = obj.get("en")
        if not isinstance(it_text, str) or not isinstance(en_text, str):
            errors += 1
            continue
        # UPDATE entrambe le lingue. `auto_translated=1` e' la coda di review;
        # `needs_translation` resta esclusivamente la coda di esecuzione.
        try:
            it_text = it_text.strip()
            en_text = en_text.strip()
            it_short = _sha256_short(it_text)
            it_version = _version_hash(it_text)
            conn.execute(
                "UPDATE i18n SET text=?, auto_translated=1, "
                "needs_translation=0, source_lang=NULL, source_hash=NULL, "
                "version_hash=?, source_text_hash=NULL, "
                "translated_at_iso=?, translated_by=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE key=? AND lang='it' AND text LIKE ?",
                (it_text, it_version, _now_iso(), f"{tier}:auto-synth",
                 key, _AUTO_SYNTH_PREFIX + "%"),
            )
            conn.execute(
                "UPDATE i18n SET text=?, auto_translated=1, "
                "needs_translation=0, source_lang='it', source_hash=?, "
                "version_hash=?, source_text_hash=?, "
                "translated_at_iso=?, translated_by=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE key=? AND lang='en' AND text LIKE ?",
                (en_text, it_short, _version_hash(en_text), it_version,
                 _now_iso(), f"{tier}:auto-synth", key,
                 _AUTO_SYNTH_PREFIX + "%"),
            )
            conn.commit()
            generated += 1
        except sqlite3.Error as ex:
            log.warning("materialize stub UPDATE failed key=%s: %r", key, ex)
            errors += 1
    return {"processed": processed, "generated": generated, "errors": errors}


def _fetch_pending(conn: sqlite3.Connection, cap: int) -> list[dict]:
    """Pending rows con il loro source text resolved.

    Include soltanto righe con sorgente esplicita, diversa dal target e non
    vuota. Il filtro avviene PRIMA del LIMIT: flag legacy non azionabili non
    possono affamare indefinitamente le traduzioni valide dietro di loro.
    """
    rows = conn.execute(
        "SELECT i.key, i.lang AS target_lang, i.source_lang, i.source_hash, "
        "       i.source_text_hash, i.translated_at_iso, s.text "
        "FROM i18n i "
        "JOIN i18n s ON s.key=i.key AND s.lang=i.source_lang "
        "WHERE i.needs_translation=1 "
        "AND i.source_lang IS NOT NULL AND i.source_lang!=i.lang "
        "AND s.text IS NOT NULL AND trim(s.text)!='' "
        "ORDER BY i.key, i.lang LIMIT ?",
        (cap,),
    ).fetchall()
    pending: list[dict] = []
    for row in rows:
        (key, target_lang, source_lang, stored_hash, stored_hash_v2,
         translated_at, src_text) = row
        src_lang = source_lang.lower()
        pending.append({
            "key": key,
            "target_lang": target_lang,
            "source_lang": src_lang,
            "source_text": src_text,
            "stored_hash": stored_hash,
            "stored_hash_v2": stored_hash_v2,
            "translated_at_iso": translated_at,
        })
    return pending


def _pending_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """Ritorna `(totali, azionabili)` con la stessa semantica del fetch."""
    total = int(conn.execute(
        "SELECT COUNT(*) FROM i18n WHERE needs_translation=1"
    ).fetchone()[0])
    actionable = int(conn.execute(
        "SELECT COUNT(*) FROM i18n i "
        "JOIN i18n s ON s.key=i.key AND s.lang=i.source_lang "
        "WHERE i.needs_translation=1 "
        "AND i.source_lang IS NOT NULL AND i.source_lang!=i.lang "
        "AND s.text IS NOT NULL AND trim(s.text)!=''"
    ).fetchone()[0])
    return total, actionable


def _is_already_translated(row: dict, current_hash: str) -> bool:
    """Idempotency check.

    Una riga e' considerata gia' tradotta se:
    (a) ha `translated_at_iso` non-NULL (e' stata processata da questo task);
    (b) il `source_hash` salvato corrisponde a quello del source attuale.
    Se invece il source e' cambiato (hash diverso), la riga va ritradotta
    anche se gia' presente in `translated_at_iso`.
    """
    if not row.get("translated_at_iso"):
        return False
    stored_v2 = row.get("stored_hash_v2")
    if stored_v2:
        return stored_v2 == _version_hash(row.get("source_text") or "")
    stored = row.get("stored_hash")
    return bool(stored) and stored == current_hash


_PROMPT_TMPL = (
    "Traduci la frase da {source_name} a {target_name} preservando placeholder "
    "{{var}} e stile imperativo. Output JSON `{{\"translation\": \"...\"}}`. "
    "Testo sorgente: {source_text}"
)


def _build_prompt(source_text: str, source_lang: str, target_lang: str) -> str:
    source_name = f"the language identified by BCP-47 tag {source_lang}"
    target_name = f"the language identified by BCP-47 tag {target_lang}"
    # Doppia chiave nel template: usiamo .format con escape `{{` `}}`.
    return _PROMPT_TMPL.format(
        source_name=source_name, target_name=target_name,
        source_text=source_text,
    )


def _parse_translation_json(raw: str) -> str | None:
    """Estrae `translation` da output LLM strict JSON.

    Robusto a fence markdown `` ```json ... ``` `` e prosa accidentale: cerca
    la prima `{` e l'ultima `}` come fallback. Restituisce None se non
    riesce a estrarre una stringa non vuota.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip fence markdown se presente.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    try:
        obj = json.loads(text)
    except Exception:
        # Fallback: estrai blocco `{...}` greedy.
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    val = obj.get("translation")
    if not isinstance(val, str):
        return None
    val = val.strip()
    return val or None


_FORMAT_FIELD_RE = re.compile(
    r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_.-]*)"
    r"(?:![rsa])?(?::[^{}]+)?\}(?!\})"
)


def _validate_candidate(source_text: str, translated_text: str) -> tuple[bool, str | None]:
    """Gate deterministico minimo prima di rendere persistente una traduzione.

    I placeholder format-style sono contratto runtime: perderne o inventarne
    uno rende la stringa inutilizzabile. NUL e output vuoti sono sempre
    corrotti. La qualità linguistica resta al prompt/LLM, non a euristiche
    fragili di language detection.
    """
    if not translated_text.strip():
        return False, "empty_translation"
    if "\x00" in translated_text:
        return False, "nul_in_translation"
    source_fields = set(_FORMAT_FIELD_RE.findall(source_text))
    target_fields = set(_FORMAT_FIELD_RE.findall(translated_text))
    if source_fields != target_fields:
        missing = sorted(source_fields - target_fields)
        extra = sorted(target_fields - source_fields)
        return False, f"placeholder_mismatch missing={missing} extra={extra}"
    return True, None


def _llm_translate(source_text: str, source_lang: str, target_lang: str,
                   tier: str) -> tuple[str | None, dict]:
    """Chiama il LLM per UNA riga. Retry 1x su JSON malformato.

    Ritorna `(translation_or_None, meta)`. Meta include `model`/`tier`/
    `prompt_hash`/`attempts`. In caso di crash dell'LLM (provider down),
    propaga l'eccezione al chiamante che fara' skip + log.
    """
    from llm_helpers import call_llm

    prompt = _build_prompt(source_text, source_lang, target_lang)
    prompt_hash_full = _sha256_full(prompt)
    sys_prompt = (
        "Sei un traduttore tecnico per Metnos. Rispondi SOLO con JSON "
        "valido nel formato richiesto. NIENTE prosa extra."
    )
    attempts = 0
    last_text = ""
    last_rejection = None
    for attempt in range(2):  # tentativo iniziale + 1 retry
        attempts += 1
        text, _meta = call_llm(
            prompt, sys_prompt, tier=tier, max_tokens=600)
        last_text = text or ""
        parsed = _parse_translation_json(last_text)
        if parsed:
            valid, rejection = _validate_candidate(source_text, parsed)
            if not valid:
                last_rejection = rejection
                log.warning(
                    "i18n candidate rejected target=%s attempt=%d: %s",
                    target_lang, attempts, rejection,
                )
                continue
            meta = {
                "model": _meta.get("model"),
                "tier": tier,
                "prompt_hash": prompt_hash_full[:8],
                "attempts": attempts,
            }
            return parsed, meta
    meta = {
        "model": None,
        "tier": tier,
        "prompt_hash": prompt_hash_full[:8],
        "attempts": attempts,
        "last_raw": last_text[:200],
        "rejection": last_rejection,
    }
    return None, meta


def _audit_append(events: list[dict]) -> Path:
    """Append append-only su `<audit_dir>/<YYYY-MM-DD>.jsonl`.

    Crea la dir se manca, scrive una riga JSON compatta per event,
    flush+fsync per durabilita'. Ritorna il path del file.
    """
    from audit_jsonl import append_jsonl
    audit_path = _audit_dir() / f"{_today_iso_date()}.jsonl"
    return append_jsonl(audit_path, events)


def _update_translated(conn: sqlite3.Connection, key: str, target_lang: str,
                       text: str, source_lang: str, source_text: str,
                       source_hash: str, translated_by: str) -> bool:
    """UPDATE atomico della riga tradotta (vedi schema §10.6.x).

    Setta `text`, `needs_translation=0`, `source_hash`, `translated_at_iso`,
    `translated_by`. `updated_at` rinfrescato per compat con i fetch
    legacy in `i18n.py`.
    """
    cur = conn.execute(
        "UPDATE i18n SET text=?, needs_translation=0, source_hash=?, "
        "version_hash=?, source_text_hash=?, "
        "translated_at_iso=?, translated_by=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE key=? AND lang=? AND EXISTS ("
        "SELECT 1 FROM i18n s WHERE s.key=? AND s.lang=? AND s.text=?"
        ")",
        (text, source_hash, _version_hash(text), _version_hash(source_text),
         _now_iso(), translated_by, key, target_lang,
         key, source_lang, source_text),
    )
    conn.commit()
    return cur.rowcount == 1


def _acknowledge_idempotent(conn: sqlite3.Connection, key: str,
                            target_lang: str) -> None:
    """Drena una pending già tradotta sullo stesso hash sorgente."""
    conn.execute(
        "UPDATE i18n SET needs_translation=0 WHERE key=? AND lang=?",
        (key, target_lang),
    )
    conn.commit()


def _task_i18n_translate_pending_unlocked(payload: dict | None = None) -> dict:
    """Callback scheduler v2: traduce fino a N=20 righe pending del DB i18n.

    Payload ignorato (firma uniforme con gli altri callback v2). Ritorna
    un dict-shape RunResult-like:
    `{ok, ok_count, error_count, metadata: {cap, tier_used, audit_path, ...}}`.
    """
    db = _db_path()
    if not db.exists():
        # Non e' un errore: il DB i18n viene creato pigramente al primo
        # accesso da `i18n._open()`. In assenza, niente da tradurre.
        return {
            "ok": True,
            "ok_count": 0,
            "error_count": 0,
            "metadata": {
                "cap": CAP_PER_FIRE,
                "tier_used": _tier_label(_tier()),
                "audit_path": None,
                "reason": "db_absent",
            },
        }

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cols_added = _ensure_schema(conn)
        if cols_added:
            log.info("i18n schema migration: added %s", cols_added)
        # Fase 11(c) wire-in 19/5/2026 v4: materializza stub auto-synth
        # PRIMA del normal translate, cosi' il source text non e' piu' il
        # placeholder `<auto-synth: KEY>` ma testo significativo.
        stub_meta = _materialize_auto_synth_stubs(conn, tier=_tier(), cap=CAP_PER_FIRE)
        if stub_meta["processed"]:
            log.info("i18n auto-synth materialize: %s", stub_meta)
        pending = _fetch_pending(conn, cap=CAP_PER_FIRE)
        if not pending:
            pending_total, pending_actionable = _pending_counts(conn)
            return {
                "ok": True,
                "ok_count": 0,
                "error_count": 0,
                "metadata": {
                    "cap": CAP_PER_FIRE,
                    "tier_used": _tier_label(_tier()),
                    "audit_path": None,
                    "reason": (
                        "no_actionable_pending" if pending_total else "no_pending"
                    ),
                    "pending_total": pending_total,
                    "pending_actionable": pending_actionable,
                    "schema_migration": cols_added,
                    "auto_synth": stub_meta,
                },
            }

        tier = _tier()
        ok_count = 0
        error_count = 0
        events: list[dict] = []
        t0_total = time.time()

        for row in pending:
            key = row["key"]
            target_lang = row["target_lang"]
            source_text = row["source_text"]
            current_hash = _sha256_short(source_text)

            base_ev = {
                "ts": _now_iso(),
                "key": key,
                "target_lang": target_lang,
                "source_lang": row["source_lang"],
                "source_hash": current_hash,
                "tier": tier,
            }

            # Source mancante o vuoto: skip esplicito + log.
            if not source_text.strip():
                events.append({**base_ev, "status": "skipped",
                               "reason": "empty_source"})
                error_count += 1
                continue

            # Idempotency: source invariato + gia' tradotta in passato.
            if _is_already_translated(row, current_hash):
                _acknowledge_idempotent(conn, key, target_lang)
                events.append({**base_ev, "status": "acknowledged",
                               "reason": "idempotent_source_unchanged"})
                continue

            try:
                translation, meta = _llm_translate(
                    source_text, row["source_lang"], target_lang, tier,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("i18n LLM crash key=%s lang=%s: %s",
                            key, target_lang, exc)
                events.append({**base_ev, "status": "failed",
                               "reason": "llm_crash", "error": str(exc)[:200]})
                error_count += 1
                continue

            if not translation:
                events.append({**base_ev, "status": "failed",
                               "reason": "llm_unparseable",
                               "attempts": meta.get("attempts"),
                               "rejection": meta.get("rejection"),
                               "last_raw": meta.get("last_raw")})
                error_count += 1
                continue

            model_id = meta.get("model") or "unknown"
            translated_by = f"{model_id}:{meta.get('prompt_hash', '')}"
            try:
                updated = _update_translated(
                    conn, key, target_lang, translation, row["source_lang"],
                    source_text, current_hash, translated_by,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("i18n UPDATE failed key=%s lang=%s: %s",
                            key, target_lang, exc)
                events.append({**base_ev, "status": "failed",
                               "reason": "db_update_error",
                               "error": str(exc)[:200]})
                error_count += 1
                continue

            if not updated:
                events.append({
                    **base_ev,
                    "status": "deferred",
                    "reason": "source_changed_during_translation",
                })
                continue

            events.append({**base_ev, "status": "ok",
                           "translated_by": translated_by,
                           "translation_len": len(translation)})
            ok_count += 1

        audit_path = _audit_append(events) if events else None
        elapsed_ms = int((time.time() - t0_total) * 1000)
        pending_total, pending_actionable = _pending_counts(conn)

        return {
            "ok": True,
            "ok_count": ok_count,
            "error_count": error_count,
            "metadata": {
                "cap": CAP_PER_FIRE,
                "tier_used": _tier_label(tier),
                "audit_path": str(audit_path) if audit_path else None,
                "elapsed_ms": elapsed_ms,
                "pending_seen": len(pending),
                "pending_total": pending_total,
                "pending_actionable": pending_actionable,
                "schema_migration": cols_added,
                "auto_synth": stub_meta,
            },
        }
    finally:
        conn.close()


def task_i18n_translate_pending(payload: dict | None = None) -> dict:
    """Entry-point unico per timer systemd e fallback scheduler v2."""
    with _exclusive_translation_lock() as acquired:
        if not acquired:
            return {
                "ok": True,
                "ok_count": 0,
                "error_count": 0,
                "metadata": {
                    "cap": CAP_PER_FIRE,
                    "tier_used": _tier_label(_tier()),
                    "audit_path": None,
                    "reason": "already_running",
                },
            }
        return _task_i18n_translate_pending_unlocked(payload)
