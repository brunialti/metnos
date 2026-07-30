#!/usr/bin/env python3
"""migrate_manifest_descriptions.py — migrazione one-shot Phase 4 ADR 0092.

Converte i manifest TOML degli executor (handcrafted + synth) dal vecchio
schema flat:

    description = "Cerca file..."

al nuovo schema multilingua:

    [description]
    it = "Cerca file..."

Stesso pattern per ogni `[args.properties.<arg>]` con campo `description`:

    [args.properties.urls]
    type = "array"
    description = "Lista di URL HTTP/HTTPS"

diventa:

    [args.properties.urls]
    type = "array"
    [args.properties.urls.description]
    it = "Lista di URL HTTP/HTTPS"

In aggiunta crea il file siblings `manifest.lang_state.json` con la
firma sha256 per ogni risorsa testuale (description + args descriptions),
e rifirma il manifest con `sign_executor`.

Idempotente: salta i manifest gia' migrati (description e' table,
non stringa flat).

Uso:
    python3 migrate_manifest_descriptions.py [--dry-run] [--lang LANG] [DIR ...]

Default LANG = METNOS_LANG dall'env (default 'it').
Default DIR = ['<install_root>/executors', '~/.local/share/metnos/executors'].
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

import config as _C  # §7.11

sys.path.insert(0, str(Path(__file__).parent))


# Pattern che matcha `description = "..."` come riga flat top-level del
# manifest (non dentro [args.properties.<arg>]).
# Cattura: tutto il valore stringa (con eventuali escape `\"`).
_DESC_FLAT_LINE_RE = re.compile(
    r'^description\s*=\s*"((?:[^"\\]|\\.)*)"\s*$',
    re.MULTILINE,
)

# Pattern multi-line per `description = """..."""` (triple-quoted basic string).
# Lazy match (.*?) per non sconfinare sulla prossima triple-quote.
# Lo `\` di line continuation TOML e' incluso letteralmente nel body.
_DESC_TRIPLE_QUOTE_RE = re.compile(
    r'^description\s*=\s*"""(.*?)"""[ \t]*$',
    re.MULTILINE | re.DOTALL,
)

# Sotto la sezione `[args.properties.<arg>]`, una riga `description = "..."`.
# Per migrarlo serve riconoscere la sezione e poi la riga al suo interno,
# e produrre una sotto-tabella `[args.properties.<arg>.description]`.

# Riga che apre una sezione `[args.properties.<arg>]`.
_ARGS_SECTION_RE = re.compile(
    r'^\[args\.properties\.([a-zA-Z_][a-zA-Z0-9_]*)\]\s*$',
    re.MULTILINE,
)


def _sha256_str(text: str) -> str:
    from hashutil import sha256_prefixed
    return sha256_prefixed(text)


def _toml_escape(s: str) -> str:
    """Escape per stringa basic TOML: \\", \\\\, \\n, \\r, \\t."""
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t"))


def _find_first_section_offset(text: str) -> int:
    """Ritorna l'offset del primo header di sezione `[section]` nel testo,
    oppure len(text) se nessuno presente. Usato per restringere la ricerca
    della top-level description al pre-amble del manifest.
    """
    m = re.search(r'^\[', text, re.MULTILINE)
    return m.start() if m else len(text)


def _migrate_top_level_description(text: str, lang: str,
                                    state: dict) -> tuple[str, bool, str]:
    # Sostituisce la `description = "..."` (top-level) flat con tabella
    # `[description]` + sub-key lang. Aggiorna `state` con version_hash.
    #
    # Supporta:
    #   - Stringa basic single-line: description = "...".
    #   - Stringa basic triple-quoted multi-line (con eventuali backslash
    #     di line continuation TOML).
    #
    # Ritorna (new_text, changed, raw_value). Se la description top-level
    # non e' presente o e' gia' un table, ritorna (text, False, "").
    #
    # Strategia (TOML-safe):
    #   1. Restringe la ricerca al pre-amble (PRIMA del primo header `[...]`).
    #   2. Rimuove la riga `description = "..."` dal pre-amble (qualunque sia
    #      la posizione fra i top-level scalars).
    #   3. Inserisce la tabella `[description]` + sub-key SUBITO PRIMA del
    #      primo header esistente — oppure alla fine del file se nessuno
    #      esiste. Questo evita che successivi top-level field (es. affinity,
    #      lifecycle) finiscano dentro `[description]`.
    head_end = _find_first_section_offset(text)
    head = text[:head_end]
    tail = text[head_end:]

    # Tenta prima triple-quoted (per non perdere il body se il single-line
    # match e' parziale e cattura solo i `"` di apertura/chiusura).
    m = _DESC_TRIPLE_QUOTE_RE.search(head)
    triple = m is not None
    if m is None:
        m = _DESC_FLAT_LINE_RE.search(head)
    if m is None:
        return text, False, ""

    raw = m.group(1)
    if triple:
        # Triple-quoted basic string: lo `\` come ultimo char di una riga
        # e' "line continuation" in TOML — la riga successiva e' splice senza
        # newline.
        # Step 1: rimuovi line-continuation TOML `\\\n` (= "\\n" sequence).
        decoded = re.sub(r'\\\n', '', raw)
        # Step 2: strip leading/trailing whitespace.
        decoded = decoded.strip()
    else:
        decoded = (raw.replace('\\n', '\n')
                      .replace('\\r', '\r')
                      .replace('\\t', '\t')
                      .replace('\\"', '"')
                      .replace('\\\\', '\\'))

    # Step 1: rimuovi la riga `description = ...` dal pre-amble.
    # Includi il `\n` di chiusura per non lasciare riga vuota orfana.
    rm_start = m.start()
    rm_end = m.end()
    # Estendi rm_end per includere il `\n` immediatamente dopo (se presente).
    if rm_end < len(head) and head[rm_end] == "\n":
        rm_end += 1
    head_no_desc = head[:rm_start] + head[rm_end:]

    # Step 2: blocco da inserire.
    block = f'[description]\n{lang} = "{_toml_escape(decoded)}"\n\n'

    # Step 3: inserisci il blocco PRIMA del tail (= primo `[...]` originale)
    # oppure alla fine del file se non c'e' tail.
    # Se head_no_desc non termina con `\n`, normalizziamo.
    if head_no_desc and not head_no_desc.endswith("\n"):
        head_no_desc += "\n"
    # Se head_no_desc termina con multiple newline, riduci a una sola
    # (la `\n\n` di `block` fornira' lo spazio prima del prossimo header).
    while head_no_desc.endswith("\n\n\n"):
        head_no_desc = head_no_desc[:-1]
    # Garantisci una riga vuota di separazione fra i top-level scalars
    # rimasti e il blocco `[description]`.
    if head_no_desc and not head_no_desc.endswith("\n\n"):
        head_no_desc += "\n"

    new_text = head_no_desc + block + tail

    state.setdefault("description", {})[lang] = {
        "version_hash": _sha256_str(decoded),
        "source_lang": None,
        "source_hash": None,
    }
    return new_text, True, decoded


def _migrate_args_descriptions(text: str, lang: str,
                                state: dict) -> tuple[str, int]:
    """Sostituisce ogni `description = "..."` dentro `[args.properties.<arg>]`
    con `[args.properties.<arg>.description]` + sub-key lang. Aggiorna `state`.

    Ritorna (new_text, n_replaced). Idempotente: se sotto-tabella gia'
    presente, lascia stare.

    Strategia (TOML-safe): per ogni `[args.properties.<arg>]`:
      1. Rimuove la riga `description = "..."` dal corpo della sezione.
      2. Inserisce la sotto-tabella `[args.properties.<arg>.description]`
         + sub-key lang ALLA FINE della sezione (dopo gli altri fields,
         prima della prossima `[...]`).

    Questo garantisce parse TOML valido: i sibling field (enum, default,
    items, ...) restano nella sezione parent, la sub-table viene introdotta
    dopo, e ogni successivo `[...]` la chiude.
    """
    # Trova tutte le sezioni args.properties.* + il loro span.
    sections = list(_ARGS_SECTION_RE.finditer(text))
    if not sections:
        return text, 0
    # Aggiungi sentinel finale per delimitare l'ultima sezione.
    starts = [m.start() for m in sections] + [len(text)]
    arg_names = [m.group(1) for m in sections]
    section_bodies = [
        (starts[i], starts[i+1], arg_names[i])
        for i in range(len(sections))
    ]
    # Lavora in reverse per non invalidare gli offset.
    out = text
    n_replaced = 0
    for start, end, arg_name in reversed(section_bodies):
        section_text = out[start:end]
        # Se contiene gia' [args.properties.<arg>.description] sotto-tabella, skip.
        sub_table_marker = f'[args.properties.{arg_name}.description]'
        if sub_table_marker in section_text:
            continue
        # Cerca la riga description="..." DENTRO la sezione corrente
        # (escluso sub-tables: stop al primo `[` di una sotto-sezione).
        lines = section_text.split("\n")
        target_line_idx = None
        target_raw = ""
        # Prima riga e' la header `[args.properties.<arg>]`. Body inizia
        # dall'indice 1. Trova prima sub-section [...] o EOF.
        body_end_idx = len(lines)
        for i in range(1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith("["):
                body_end_idx = i
                break
        for i in range(1, body_end_idx):
            line = lines[i]
            mm = re.match(r'^description\s*=\s*"((?:[^"\\]|\\.)*)"\s*$', line)
            if mm:
                target_line_idx = i
                target_raw = mm.group(1)
                break
        if target_line_idx is None:
            continue
        decoded = (target_raw.replace('\\n', '\n')
                              .replace('\\r', '\r')
                              .replace('\\t', '\t')
                              .replace('\\"', '"')
                              .replace('\\\\', '\\'))
        # Costruisci la sotto-tabella da inserire al fondo del body.
        sub_block_lines = [
            f'[args.properties.{arg_name}.description]',
            f'{lang} = "{_toml_escape(decoded)}"',
        ]
        # Rimuovi la riga description originale.
        new_lines = list(lines)
        del new_lines[target_line_idx]
        # Riassorbi eventuali doppie righe vuote create dalla cancellazione
        # (es. era `field\n\ndescription = "..."\nitems = "..."` → diventa
        # `field\n\nitems = "..."` con una doppia riga di troppo).
        # Compatta: massimo una riga vuota consecutiva nel body.
        cleaned: list[str] = []
        prev_empty = False
        for ln in new_lines:
            empty = (ln.strip() == "")
            if empty and prev_empty:
                continue
            cleaned.append(ln)
            prev_empty = empty
        new_lines = cleaned
        # Ricalcola body_end_idx (la prima sub-section dopo il body).
        body_end_idx = len(new_lines)
        for i in range(1, len(new_lines)):
            if new_lines[i].strip().startswith("["):
                body_end_idx = i
                break
        # Inserisci la sub-table ALLA FINE del body, prima della prossima
        # sub-section (o EOF). Garantisci una riga vuota davanti per
        # separare dai field del parent.
        insert_at = body_end_idx
        if insert_at > 0 and new_lines[insert_at - 1].strip() != "":
            sub_block_lines = [""] + sub_block_lines
        # Aggiungi UNA riga vuota di terminazione (cosi' la prossima
        # sub-section / EOF e' separata).
        sub_block_lines = sub_block_lines + [""]
        new_lines[insert_at:insert_at] = sub_block_lines
        new_section_text = "\n".join(new_lines)
        out = out[:start] + new_section_text + out[end:]
        n_replaced += 1
        key = f"args.{arg_name}.description"
        state.setdefault(key, {})[lang] = {
            "version_hash": _sha256_str(decoded),
            "source_lang": None,
            "source_hash": None,
        }
    return out, n_replaced


def _state_from_parsed(parsed: dict) -> dict:
    """Ricostruisce `manifest.lang_state.json` da un manifest gia' in schema
    multilingua. Per ogni description (top-level + args.properties.<arg>),
    salva `version_hash` per ciascuna lingua presente.
    """
    state: dict = {}
    desc = parsed.get("description")
    if isinstance(desc, dict):
        for lang, val in desc.items():
            if isinstance(val, str):
                state.setdefault("description", {})[lang] = {
                    "version_hash": _sha256_str(val),
                    "source_lang": None,
                    "source_hash": None,
                }
    props = (parsed.get("args") or {}).get("properties") or {}
    for arg_name, arg_def in props.items():
        if not isinstance(arg_def, dict):
            continue
        arg_desc = arg_def.get("description")
        if not isinstance(arg_desc, dict):
            continue
        key = f"args.{arg_name}.description"
        for lang, val in arg_desc.items():
            if isinstance(val, str):
                state.setdefault(key, {})[lang] = {
                    "version_hash": _sha256_str(val),
                    "source_lang": None,
                    "source_hash": None,
                }
    return state


def migrate_one(manifest_path: Path, *, lang: str = "it",
                dry_run: bool = False, sign: bool = True) -> dict:
    """Migra un singolo manifest. Ritorna dict con esito.

    - Se gia' migrato (description e' table), non fa nulla → status='already_migrated'.
    - Altrimenti: edit testuale + scrive manifest.lang_state.json + (opz.) re-firma.

    `dry_run=True`: stampa il diff senza scrivere.
    """
    manifest_dir = manifest_path.parent
    text_orig = manifest_path.read_text(encoding="utf-8")

    # Already-migrated check: tomllib parse + verifica che description sia dict.
    try:
        parsed = tomllib.loads(text_orig)
    except tomllib.TOMLDecodeError as e:
        return {"ok": False, "path": str(manifest_path),
                "status": "parse_error", "error": str(e)}

    desc_val = parsed.get("description")
    if isinstance(desc_val, dict):
        # Verifica anche che TUTTI gli args.properties.<arg>.description (se
        # presenti) siano table. Se anche uno e' string, non e' completo.
        props = (parsed.get("args") or {}).get("properties") or {}
        leftovers = []
        for arg_name, arg_def in props.items():
            if isinstance(arg_def, dict) and "description" in arg_def:
                if not isinstance(arg_def["description"], dict):
                    leftovers.append(arg_name)
        if not leftovers:
            # Se il companion `manifest.lang_state.json` manca, ricostruisci
            # dallo schema corrente. Copre executor creati direttamente nel
            # nuovo schema multilingua senza passare per la migrazione.
            state_path = manifest_dir / "manifest.lang_state.json"
            if not state_path.is_file() and not dry_run:
                state = _state_from_parsed(parsed)
                state_path.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return {"ok": True, "path": str(manifest_path),
                        "status": "state_companion_written",
                        "lang_state_keys": list(state.keys())}
            return {"ok": True, "path": str(manifest_path),
                    "status": "already_migrated"}

    # state che verra' scritto in manifest.lang_state.json
    state: dict = {}

    # Carica state esistente (se gia' presente per via di una migrazione parziale).
    state_path = manifest_dir / "manifest.lang_state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            state = {}

    # 1) Top-level description.
    text_new, desc_changed, _desc_value = _migrate_top_level_description(
        text_orig, lang, state,
    )

    # 2) args.properties.<arg>.description (in qualunque sezione).
    text_new, args_changed = _migrate_args_descriptions(text_new, lang, state)

    if not desc_changed and args_changed == 0:
        # Niente da fare: top-level e' gia' table, o non c'e' description.
        # Ma se desc_val e' dict e nessun args_changed, e' already_migrated.
        if isinstance(desc_val, dict):
            return {"ok": True, "path": str(manifest_path),
                    "status": "already_migrated"}
        return {"ok": True, "path": str(manifest_path),
                "status": "no_description_field"}

    # Pre-write: verifica che il nuovo testo parsi correttamente come TOML.
    try:
        re_parsed = tomllib.loads(text_new)
    except tomllib.TOMLDecodeError as e:
        return {"ok": False, "path": str(manifest_path),
                "status": "post_migration_parse_error",
                "error": str(e), "diff": text_new[:500]}

    # Verifica che description sia diventato un dict.
    new_desc = re_parsed.get("description")
    if not isinstance(new_desc, dict):
        return {"ok": False, "path": str(manifest_path),
                "status": "migration_failed",
                "error": "description NON e' un table dopo la migrazione"}

    if dry_run:
        return {"ok": True, "path": str(manifest_path),
                "status": "dry_run", "desc_changed": desc_changed,
                "args_changed": args_changed,
                "lang_state": state}

    # Scrivi il nuovo manifest.
    manifest_path.write_text(text_new, encoding="utf-8")

    # Scrivi manifest.lang_state.json
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    # Re-firma (digest dipende dal codice non dal manifest, ma la firma
    # e' sui bytes del manifest che e' cambiato).
    sign_status = None
    if sign:
        try:
            from sign import sign_executor
            sign_executor(manifest_dir)
            sign_status = "signed"
        except Exception as e:
            sign_status = f"sign_failed: {type(e).__name__}: {e}"

    return {"ok": True, "path": str(manifest_path),
            "status": "migrated",
            "desc_changed": desc_changed,
            "args_changed": args_changed,
            "sign": sign_status,
            "lang_state_keys": list(state.keys())}


def migrate_dirs(dirs: list[Path], *, lang: str = "it",
                 dry_run: bool = False, sign: bool = True) -> list[dict]:
    """Migra tutti i manifest.toml sotto le directory date."""
    results = []
    for d in dirs:
        if not d.exists():
            continue
        for manifest_path in sorted(d.glob("*/manifest.toml")):
            res = migrate_one(manifest_path, lang=lang, dry_run=dry_run, sign=sign)
            results.append(res)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dirs", nargs="*", default=[
        str(_C.PATH_EXECUTORS),
        str(_C.PATH_USER_DATA / "executors"),
    ], help="Directory radice degli executor (sub-dir = manifest.toml).")
    parser.add_argument("--lang", default=os.environ.get("METNOS_LANG", "it"),
                        help="Lingua corrente (default da METNOS_LANG, fallback 'it').")
    parser.add_argument("--dry-run", action="store_true",
                        help="Non scrivere file; mostra solo cosa cambierebbe.")
    parser.add_argument("--no-sign", action="store_true",
                        help="Non rifirmare i manifest dopo la migrazione.")
    args = parser.parse_args()

    dirs = [Path(d).expanduser() for d in args.dirs]
    results = migrate_dirs(dirs, lang=args.lang, dry_run=args.dry_run,
                            sign=not args.no_sign)
    n_migrated = sum(1 for r in results if r.get("status") == "migrated")
    n_already = sum(1 for r in results if r.get("status") == "already_migrated")
    n_failed = sum(1 for r in results if not r.get("ok"))
    n_skipped = sum(1 for r in results
                    if r.get("status") in ("no_description_field",))
    n_dryrun = sum(1 for r in results if r.get("status") == "dry_run")
    print(f"\nManifest scanned: {len(results)}")
    print(f"  migrated: {n_migrated}")
    print(f"  already_migrated: {n_already}")
    print(f"  dry_run: {n_dryrun}")
    print(f"  no_description: {n_skipped}")
    print(f"  failed: {n_failed}")
    for r in results:
        if not r.get("ok"):
            print(f"  FAIL {r.get('path')}: {r.get('status')} {r.get('error', '')}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
