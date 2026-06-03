"""Task scheduler v2: traduce le righe pending del DB i18n.

Trigger automatico `daily@02:00`. Pesca fino a N righe con
`needs_translation=1` dal DB `~/.local/share/metnos/i18n.sqlite`,
invoca il LLM per ogni riga con prompt strict-JSON
`{"translation": "..."}` preservando i placeholder `{var}`, e salva il
risultato con `needs_translation=0`. Idempotente sul `source_hash`: se
la riga e' gia' stata tradotta e il testo sorgente non e' cambiato dal
salvataggio precedente, viene saltata.

Override del tier LLM via env `METNOS_I18N_QUALITY` (`middle|wise|frontier`,
default `wise`). Cap N=20 per fire per non saturare la GPU notturna.
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
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("metnos.jobs.i18n_translate_pending")


# Cap throttling per fire. La GPU locale Strix Halo serve VLM + planner;
# ~20 traduzioni a tier wise (Gemma 4 26B) assorbono ~60s GPU/fire. Ora
# configurabile (era hardcoded): con cadenza every_6h, 4 fire/giorno × cap.
# Alzalo per drenare prima il backlog (al costo di burst GPU diurni piu' lunghi).
CAP_PER_FIRE = int(os.environ.get("METNOS_I18N_CAP_PER_FIRE", "20"))

# Lingue note → nome leggibile per il prompt LLM.
_LANG_NAMES = {
    "it": "Italian",
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
}

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


def _tier() -> str:
    """LLM tier per la traduzione (default `wise`; override env)."""
    return os.environ.get("METNOS_I18N_QUALITY", "wise").lower()


def _now_iso() -> str:
    """ISO8601 UTC con suffisso `Z` (timespec=seconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_iso_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sha256_short(text: str) -> str:
    """Hash 16-char (allineato a `i18n._hash_text`)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _sha256_full(text: str) -> str:
    """Hash sha256 hex completo (per audit del prompt usato)."""
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
            text, _meta = call_llm(prompt, sys_prompt, tier=tier,
                                     max_tokens=400, temperature=0.0)
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
        # UPDATE entrambe le lingue + flag.
        try:
            conn.execute(
                "UPDATE i18n SET text=?, auto_translated=1, "
                "needs_translation=1, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE key=? AND lang='it' AND text LIKE ?",
                (it_text.strip(), key, _AUTO_SYNTH_PREFIX + "%"),
            )
            conn.execute(
                "UPDATE i18n SET text=?, auto_translated=1, "
                "needs_translation=1, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE key=? AND lang='en' AND text LIKE ?",
                (en_text.strip(), key, _AUTO_SYNTH_PREFIX + "%"),
            )
            conn.commit()
            generated += 1
        except sqlite3.Error as ex:
            log.warning("materialize stub UPDATE failed key=%s: %r", key, ex)
            errors += 1
    return {"processed": processed, "generated": generated, "errors": errors}


def _fetch_pending(conn: sqlite3.Connection, cap: int) -> list[dict]:
    """Pending rows con il loro source text resolved.

    Per ogni riga `needs_translation=1` cerca il source nella stessa key con
    `lang=source_lang`. Se `source_lang` e' NULL, fallback a `it`. Se il
    source non esiste o e' vuoto, la riga viene saltata (non si traduce
    il nulla).
    """
    rows = conn.execute(
        "SELECT key, lang AS target_lang, source_lang, source_hash, "
        "       translated_at_iso "
        "FROM i18n WHERE needs_translation=1 "
        "ORDER BY key, lang LIMIT ?",
        (cap,),
    ).fetchall()
    pending: list[dict] = []
    for row in rows:
        key, target_lang, source_lang, stored_hash, translated_at = row
        src_lang = (source_lang or "it").lower()
        src_row = conn.execute(
            "SELECT text FROM i18n WHERE key=? AND lang=?",
            (key, src_lang),
        ).fetchone()
        src_text = (src_row[0] if src_row else "") or ""
        pending.append({
            "key": key,
            "target_lang": target_lang,
            "source_lang": src_lang,
            "source_text": src_text,
            "stored_hash": stored_hash,
            "translated_at_iso": translated_at,
        })
    return pending


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
    stored = row.get("stored_hash")
    return bool(stored) and stored == current_hash


_PROMPT_TMPL = (
    "Traduci la frase IT in {target_name} preservando placeholder "
    "{{var}} e stile imperativo. Output JSON `{{\"translation\": \"...\"}}`. "
    "Frase IT: {source_text}"
)


def _build_prompt(source_text: str, target_lang: str) -> str:
    target_name = _LANG_NAMES.get(target_lang.lower(), target_lang)
    # Doppia chiave nel template: usiamo .format con escape `{{` `}}`.
    return _PROMPT_TMPL.format(target_name=target_name, source_text=source_text)


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


def _llm_translate(source_text: str, target_lang: str, tier: str) -> tuple[str | None, dict]:
    """Chiama il LLM per UNA riga. Retry 1x su JSON malformato.

    Ritorna `(translation_or_None, meta)`. Meta include `model`/`tier`/
    `prompt_hash`/`attempts`. In caso di crash dell'LLM (provider down),
    propaga l'eccezione al chiamante che fara' skip + log.
    """
    from llm_helpers import call_llm

    prompt = _build_prompt(source_text, target_lang)
    prompt_hash_full = _sha256_full(prompt)
    sys_prompt = (
        "Sei un traduttore tecnico per Metnos. Rispondi SOLO con JSON "
        "valido nel formato richiesto. NIENTE prosa extra."
    )
    attempts = 0
    last_text = ""
    for attempt in range(2):  # tentativo iniziale + 1 retry
        attempts += 1
        text, _meta = call_llm(
            prompt, sys_prompt, tier=tier, max_tokens=600, temperature=0.0,
        )
        last_text = text or ""
        parsed = _parse_translation_json(last_text)
        if parsed:
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
    }
    return None, meta


def _audit_append(events: list[dict]) -> Path:
    """Append append-only su `<audit_dir>/<YYYY-MM-DD>.jsonl`.

    Crea la dir se manca, scrive una riga JSON compatta per event,
    flush+fsync per durabilita'. Ritorna il path del file.
    """
    audit_dir = _audit_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"{_today_iso_date()}.jsonl"
    # `'a'` mode + fsync su POSIX e' atomico per linee <PIPE_BUF (~4KB);
    # le righe di audit sono brevi (<1KB) quindi safe-by-construction.
    with open(audit_path, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return audit_path


def _update_translated(conn: sqlite3.Connection, key: str, target_lang: str,
                       text: str, source_hash: str, translated_by: str) -> None:
    """UPDATE atomico della riga tradotta (vedi schema §10.6.x).

    Setta `text`, `needs_translation=0`, `source_hash`, `translated_at_iso`,
    `translated_by`. `updated_at` rinfrescato per compat con i fetch
    legacy in `i18n.py`.
    """
    conn.execute(
        "UPDATE i18n SET text=?, needs_translation=0, source_hash=?, "
        "translated_at_iso=?, translated_by=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE key=? AND lang=?",
        (text, source_hash, _now_iso(), translated_by, key, target_lang),
    )
    conn.commit()


def task_i18n_translate_pending(payload: dict | None = None) -> dict:
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
                "tier_used": _tier(),
                "audit_path": None,
                "reason": "db_absent",
            },
        }

    conn = sqlite3.connect(str(db))
    try:
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
            return {
                "ok": True,
                "ok_count": 0,
                "error_count": 0,
                "metadata": {
                    "cap": CAP_PER_FIRE,
                    "tier_used": _tier(),
                    "audit_path": None,
                    "reason": "no_pending",
                    "schema_migration": cols_added,
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
                events.append({**base_ev, "status": "skipped",
                               "reason": "idempotent_source_unchanged"})
                continue

            try:
                translation, meta = _llm_translate(source_text, target_lang, tier)
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
                               "last_raw": meta.get("last_raw")})
                error_count += 1
                continue

            model_id = meta.get("model") or "unknown"
            translated_by = f"{model_id}:{meta.get('prompt_hash', '')}"
            try:
                _update_translated(conn, key, target_lang, translation,
                                   current_hash, translated_by)
            except Exception as exc:  # noqa: BLE001
                log.warning("i18n UPDATE failed key=%s lang=%s: %s",
                            key, target_lang, exc)
                events.append({**base_ev, "status": "failed",
                               "reason": "db_update_error",
                               "error": str(exc)[:200]})
                error_count += 1
                continue

            events.append({**base_ev, "status": "ok",
                           "translated_by": translated_by,
                           "translation_len": len(translation)})
            ok_count += 1

        audit_path = _audit_append(events) if events else None
        elapsed_ms = int((time.time() - t0_total) * 1000)

        return {
            "ok": True,
            "ok_count": ok_count,
            "error_count": error_count,
            "metadata": {
                "cap": CAP_PER_FIRE,
                "tier_used": tier,
                "audit_path": str(audit_path) if audit_path else None,
                "elapsed_ms": elapsed_ms,
                "pending_seen": len(pending),
                "schema_migration": cols_added,
            },
        }
    finally:
        conn.close()
