#!/usr/bin/env python3
"""i18n_translator — daemon background per lazy translation.

Design 1/5/2026 sera (vedi `metnos_design_i18n_final.md` punto 15):
- Sweep DB i18n per entries con needs_translation=1
- Batch LLM call (modello locale middle tier, ~5-10s per batch 50 entries)
- UPDATE entries con text + needs_translation=0
- Self-healing: retry su fallimento
- Throttle: 30s al boot → 5min steady state

Lancio:
    standalone:    python3 -m i18n_translator
    one-shot:      python3 -m admin.i18n_cli translate-pending
    systemd:       metnos-i18n-translator.service (futuro)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
import i18n  # noqa: E402
from vocab import ACTIONS, OBJECTS, QUALIFIERS  # noqa: E402

log = logging.getLogger("metnos.i18n_translator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

BATCH_SIZE = 25  # entries per LLM call (compromesso latenza/throughput)
INTERVAL_BOOT_S = 30
INTERVAL_STEADY_S = 300

# Mapping codice → nome lingua per il prompt LLM
_LANG_NAMES = {
    "it": "Italian",
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
}

_VOCAB_PROMPT_ARGS = {
    "canonical_actions": "/".join(ACTIONS),
    "canonical_objects": "/".join(OBJECTS),
    "canonical_qualifiers": "/".join(QUALIFIERS),
}

_PROMPT_TEMPLATE_USER_FACING = """You are translating user-facing UI strings for Metnos, a personal AI assistant.

Source language: {source_name}
Target language: {target_name}

Rules:
- Preserve ALL placeholders {{name}}, {{path}}, {{count}}, {{tool}} EXACTLY as written
- Keep same tone: concise, imperative, professional
- Keep same line breaks (\\n), emojis, punctuation, capitalization style
- For technical terms (API, JSON, sqlite, IMAP, etc.) keep English form
- For pipe-separated keyword lists ("annulla|cancel|stop"): translate each word, preserve pipe separator

Output ONE JSON object mapping each input key to its translation. NO prose, NO markdown fences, NO explanations.

Strings to translate (key → source text):
{json_strings}

Output JSON:"""

# Prompt usato per traduzione di TESTI INDIRIZZATI A LLM (PLANNER prompt,
# synt prompt, tool description, executor description). Massima fedelta'
# semantica: il testo tradotto sara' consumato da un altro LLM e DEVE
# guidarlo identicamente all'originale.
_PROMPT_TEMPLATE_LLM_TARGETED = """You are translating prompts for Metnos that will be consumed by ANOTHER LLM (planner, intent extractor, code generator). The translated text MUST guide the downstream LLM EQUIVALENTLY to the original. Maximum semantic fidelity required.

Source language: {source_name}
Target language: {target_name}

CRITICAL PRESERVATION RULES (DO NOT VIOLATE):

1. PLACEHOLDERS — preserve EXACTLY: {{name}}, {{step}}, {{path}}, __VOCAB_ACTIONS__, __VOCAB_OBJECTS__, __VOCAB_QUALIFIERS__, etc. Never translate or alter placeholder names.

2. CANONICAL IDENTIFIERS — keep as-is in both languages (vocabulary closed, EN-only): tool names (find_places, get_location, request_new_executor, request_location_from_user, scratchpad_read, classify_entries, describe_entries, sort_entries, filter_entries, compute_entries, get_now, get_places, find_files, find_dirs, find_packages, list_processes, read_messages, send_messages, move_messages, read_files, write_files, delete_files, move_files, create_dirs, delete_dirs, get_files, get_urls, filter_texts_lines, undo_last_turn, change_images, compress_dirs_gz, compress_files_gz, compute_files, describe_dirs, extract_files_zip, list_dirs, find_files, ...), arg names (from_step, near, radius_km, bounded, queries, max_results, entries, paths, content, dst_template, ...), action verbs ({canonical_actions}), object nouns ({canonical_objects}), modifiers ({canonical_qualifiers}). Metnos DOMAIN NOUNS — keep in ENGLISH in ALL target languages, do NOT translate them (e.g. NOT "esecutore"/"manifesto"): executor, executors, manifest, manifests, runtime, planner, synt, fastpath, autopath, scratchpad.

3. STRUCTURAL FORMATTING — preserve LITERALLY: section headers (═══ separators, ## headings), numbered rules (1., 2., 2-bis, 2-ter, 2-quater), DEVI/NON DEVI/OK/ERRORE pattern (translate to YOU MUST/YOU MUST NOT/CORRECT/WRONG keeping uppercase emphasis), bullet lists, indentation, code blocks, JSON examples (translate text fields BUT keep keys/identifiers/values that are technical literally).

4. FEW-SHOT EXAMPLES — translate the user's natural-language query but PRESERVE the structured output exactly. E.g., translate "User: 'comprimi /tmp/log.txt con gzip'" to "User: 'compress /tmp/log.txt with gzip'", but keep `{{"name": "compress_files_gz", ...}}` IDENTICAL.

5. PRESCRIPTIVE TONE — Metnos prompts use strong imperative ("DEVI"=YOU MUST, "NON DEVI"=YOU MUST NOT). Keep the imperative force and ALL CAPS for emphasis. Do NOT soften ("you should").

6. DOMAIN VOCABULARY — preserve technical Italian metnos-specific concepts when no exact EN equivalent: "vaglio", "scratchpad", "mnest", "mnestoma" can stay Italian if defining a Metnos-specific entity.

7. COMMENT/PARENTHETICAL HINTS — translate fully. E.g., "(es. find_files)" → "(e.g., find_files)".

8. NEVER OMIT, NEVER ADD content. Same number of rules, same number of examples, same structure. The translated text must have the same instructional weight as the original.

Output ONE JSON object mapping each input key to its translation. NO prose, NO markdown fences, NO explanations.

Strings to translate (key → source text):
{json_strings}

Output JSON:"""


def _is_llm_targeted_key(key: str) -> bool:
    """Chiavi che terminano in prompt LLM-consumed → richiedono fedelta' massima.
    Pattern: prompt.* / tool.*.description / *.description (executor desc).
    """
    if key.startswith("prompt."):
        return True
    if key.startswith("tool.") and key.endswith(".description"):
        return True
    if key.endswith(".description"):
        return True  # executor description: visto dal PLANNER LLM
    return False


def _llm_call(prompt: str, max_tokens: int = 4000, tier: str = "middle") -> str | None:
    """LLM call via LLMRouter. Tier: middle (modello locale) per testi
    user-facing brevi; wise (stesso o frontier) per prompt LLM-targeted
    grandi che richiedono massima fedelta' semantica."""
    try:
        from llm_router import LLMRouter
    except ImportError as e:
        log.error("LLMRouter non disponibile: %s", e)
        return None
    try:
        r = LLMRouter()
        provider = r.provider(tier)
        res = provider.chat("", prompt, max_tokens=max_tokens, temperature=0,
                              think=False)
        return res.text or ""
    except Exception as e:
        log.warning("LLM call failed (tier=%s): %s", tier, e)
        return None


def _parse_llm_json(raw: str) -> dict | None:
    """Estrae JSON dict dal raw output LLM. Tollerante: trova {...} bilanciato."""
    if not raw:
        return None
    raw = raw.strip()
    # Strip markdown fences se presenti
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    # Trova primo { e ultimo }
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j < i:
        return None
    snippet = raw[i:j+1]
    try:
        d = json.loads(snippet)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


def translate_batch(entries: list[dict]) -> dict[str, str]:
    """Batch translate. Ritorna {key: translated_text}. Pipeline:
    - Separa entries in due classi: user-facing (prompt corto) vs LLM-targeted
      (prompt grande, fedelta' massima richiesta). Le ultime usano prompt
      template piu' rigoroso e tier `wise` per maggior capacita'.
    - Per ogni classe, raggruppa per (source_lang, target_lang) e invoca LLM.
    """
    if not entries:
        return {}
    # Split: LLM-targeted vs user-facing
    user_facing, llm_targeted = [], []
    for e in entries:
        if not e.get("source_text"):
            continue
        if _is_llm_targeted_key(e["key"]):
            llm_targeted.append(e)
        else:
            user_facing.append(e)

    out: dict[str, str] = {}
    for cls_name, cls_entries, template, tier, max_tok in [
        ("user-facing", user_facing, _PROMPT_TEMPLATE_USER_FACING, "middle", 4000),
        ("llm-targeted", llm_targeted, _PROMPT_TEMPLATE_LLM_TARGETED, "wise", 24000),
    ]:
        if not cls_entries:
            continue
        by_pair: dict[tuple[str, str], list[dict]] = {}
        for e in cls_entries:
            pair = (e["source_lang"], e["target_lang"])
            by_pair.setdefault(pair, []).append(e)
        for (src, tgt), batch in by_pair.items():
            src_name = _LANG_NAMES.get(src, src)
            tgt_name = _LANG_NAMES.get(tgt, tgt)
            # Per LLM-targeted (prompt giganti) processa UNA chiave per call
            # — riduce errori parser su output mega.
            sub_batches = [[e] for e in batch] if cls_name == "llm-targeted" else [batch]
            for sub in sub_batches:
                json_strings = json.dumps(
                    {e["key"]: e["source_text"] for e in sub},
                    ensure_ascii=False, indent=2,
                )
                prompt = template.format(
                    source_name=src_name, target_name=tgt_name,
                    json_strings=json_strings,
                    **_VOCAB_PROMPT_ARGS,
                )
                log.info("Translating [%s] %d entries %s → %s (tier=%s)",
                          cls_name, len(sub), src, tgt, tier)
                raw = _llm_call(prompt, max_tokens=max_tok, tier=tier)
                translated = _parse_llm_json(raw or "")
                if not translated:
                    log.warning("LLM output unparseable [%s] keys=%s, retry next cycle",
                                  cls_name, [e["key"] for e in sub])
                    continue
                for key, text in translated.items():
                    if isinstance(text, str) and text.strip():
                        out[key + "::" + tgt] = text
    return out


def run_one_cycle() -> tuple[int, int]:
    """Esegue UN ciclo: prende pending, traduce, UPDATE. Ritorna (n_processed, n_remaining)."""
    pending = i18n.list_pending(limit=BATCH_SIZE)
    if not pending:
        return 0, 0
    translated = translate_batch(pending)
    n_ok = 0
    for e in pending:
        marker = e["key"] + "::" + e["target_lang"]
        if marker in translated:
            i18n.set_translated(e["key"], e["target_lang"], translated[marker])
            n_ok += 1
    remaining = len(i18n.list_pending(limit=1))  # check se altri rimangono
    return n_ok, remaining


def run_loop(boot_interval: float = INTERVAL_BOOT_S,
              steady_interval: float = INTERVAL_STEADY_S):
    """Loop infinito. Boot rapido (30s) finché ha pending; steady 5min."""
    log.info("i18n_translator daemon avviato. boot_interval=%ds, steady_interval=%ds",
              boot_interval, steady_interval)
    interval = boot_interval
    while True:
        try:
            n_ok, remaining = run_one_cycle()
            if n_ok > 0:
                log.info("Translated %d entries (remaining: %d)", n_ok, remaining)
            if remaining == 0:
                interval = steady_interval  # nessun pending → modalità steady
            else:
                interval = boot_interval  # ancora pending → boot rapido
        except Exception as e:
            log.exception("Errore nel ciclo: %s", e)
        time.sleep(interval)


# ===========================================================================
# Prompt-file translation (ADR 0092 Phase 3, 5/5/2026)
#
# I 26 file `.j2` in `runtime/prompts/it/` sono prompt LLM lunghi (1-25 KB)
# strutturati con sezioni IT-specifiche (DEVI/NON DEVI/OK/ERRORE), placeholder
# Jinja2 (`{{ var }}`, `{% raw %}{% endraw %}`) e blocchi codice. Servono
# regole di preservazione piu' rigorose di quelle dei messaggi brevi.
#
# Flusso:
#   1. translate_prompt_file(role) legge `prompts/it/<role>.j2`
#   2. pre-pass: maschera placeholder Jinja2 + code-fences con sentinel UUID
#   3. UNA call LLM tier='wise' (frontier locale: modello locale, oppure online
#      via config tiers — Sonnet/GPT-5)
#   4. post-pass: restore sentinel; canonical map DEVI/NON DEVI/OK/ERRORE→
#      MUST/MUST NOT/OK/ERROR; "E' UN ERRORE"→"THIS IS AN ERROR"
#   5. validation: sintassi MiniJinja, set placeholder identico, len ratio
#      0.7-1.4× (drift detector)
#   6. salva candidato in `prompts/<lang>/_pending/<role>.j2.candidate`.
#      §K (15/6/2026): il candidato è USATO IN-VIVO dal loader (catena
#      live→candidato→EN in `prompt_loader._resolve_prompt_source`) senza
#      attendere una promozione manuale — l'approvazione NON è più un gate
#      bloccante (nessuno revisiona centinaia di stringhe; ADR 0173). La
#      promozione a `prompts/<lang>/<role>.j2` resta possibile (opt-in,
#      canonicalizza + abilita il linter prescrittivo §6.1).
# ===========================================================================
import re as _re
import uuid as _uuid

# Mapping fisso prescrittivo IT→EN per le sezioni stilistiche §6 di the design guide.
# Applicato in post-pass (case-sensitive) per garantire la stessa forma
# prescrittiva nella versione EN. NON delegare al LLM perche' tende a
# parafrasi (es. "you should" che indebolisce l'imperativo).
#
# ORDINE IMPORTANTE: i pattern piu' specifici devono venire PRIMA dei piu'
# generici (NON DEVI: prima di DEVI:, altrimenti DEVI: matcha dentro NON DEVI:).
# Dict Python 3.7+ preserva l'ordine di insertion → safe.
PRESCRIPTIVE_MAP_IT_EN: dict[str, str] = {
    # Marker di anti-pattern (the design guide §6): plurali e maiuscolo preservato.
    "E' UN ERRORE": "THIS IS AN ERROR",
    "È UN ERRORE": "THIS IS AN ERROR",
    # NON DEVI: deve precedere DEVI: per evitare overlap.
    "NON DEVI:": "MUST NOT:",
    "DEVI:": "MUST:",
    # ERRORE: deve precedere "ERRORE" da solo (qui non c'e', ma per sicurezza).
    "ERRORE:": "ERROR:",
    "OK:": "OK:",
}

# Mappa INVERSA EN→IT: canonicalizza i marker §6 quando il TARGET è l'italiano.
# Senza questa, `_apply_prescriptive_map` applicava la mappa IT→EN ANCHE alle
# traduzioni EN→IT, RI-INGLESIZZANDO i marker (l'LLM produce «DEVI:» e la mappa
# lo ributtava a «MUST:» dentro il prompt italiano — bug live candidato
# synt_code.j2.candidate con «MUST:»/«THIS IS AN ERROR» in IT, 30/6). Ordine:
# chiavi più lunghe prima (MUST NOT: prima di MUST:).
PRESCRIPTIVE_MAP_EN_IT: dict[str, str] = {
    "THIS IS AN ERROR": "E' UN ERRORE",
    "MUST NOT:": "NON DEVI:",
    "MUST:": "DEVI:",
    "ERROR:": "ERRORE:",
    "OK:": "OK:",
}

# Selettore per-target: il marker canonico §6 è definito solo per IT↔EN. Per
# altre lingue (fr/de/es) nessuna mappa fissa → la traduzione LLM dei marker
# resta com'è (niente ri-canonicalizzazione forzata).
_PRESCRIPTIVE_MAP_BY_TARGET: dict[str, dict[str, str]] = {
    "en": PRESCRIPTIVE_MAP_IT_EN,
    "it": PRESCRIPTIVE_MAP_EN_IT,
}

# Pattern regex per "preservare letteralmente": il translator NON deve
# tradurre questi span. Marcati con sentinel UUID prima della call e
# ripristinati dopo.
#
# Ordine importante: prima i pattern piu' specifici (raw block, code fence
# con linguaggio), poi i piu' generici (inline backticks, `{{ ... }}`).
_PRESERVE_PATTERNS = (
    # {% raw %}...{% endraw %} block (multi-line, non-greedy)
    _re.compile(r"\{%\s*raw\s*%\}.*?\{%\s*endraw\s*%\}", _re.DOTALL),
    # {% if/else/endif %} e altri tag di controllo Jinja2
    _re.compile(r"\{%[-+]?.*?[-+]?%\}"),
    # Jinja2 expression {{ ... }}
    _re.compile(r"\{\{[-+]?.*?[-+]?\}\}"),
    # Comment {# ... #}
    _re.compile(r"\{#.*?#\}", _re.DOTALL),
    # Triple-backtick code blocks (multi-line)
    _re.compile(r"```[a-zA-Z0-9_+\-]*\n.*?\n```", _re.DOTALL),
    # Inline backticks (single backtick pairs)
    _re.compile(r"`[^`\n]+`"),
)


def _mask_invariant_spans(text: str) -> tuple[str, dict[str, str]]:
    """Sostituisce ogni span da preservare con un sentinel `__METNOS_INV_<uuid>__`
    e ritorna (testo_mascherato, mapping_sentinel→span_originale).

    Sentinel formato: 32 hex char (uuid4 senza trattini) + suffisso `__`.
    NON viene tradotto dal LLM perche' non contiene parole semantiche.
    """
    masked = text
    mapping: dict[str, str] = {}
    for pat in _PRESERVE_PATTERNS:
        def _repl(m: _re.Match[str]) -> str:
            sentinel = f"__METNOS_INV_{_uuid.uuid4().hex}__"
            mapping[sentinel] = m.group(0)
            return sentinel
        masked = pat.sub(_repl, masked)
    return masked, mapping


def _restore_invariant_spans(translated: str, mapping: dict[str, str]) -> str:
    """Ripristina i sentinel con gli span originali.

    Loop fino a fixpoint: i pattern di mask vengono applicati in cascata
    (es. inline-backticks viene dopo {% raw %}...{% endraw %}, quindi
    backticks attorno a un raw block masked diventano un secondo sentinel
    che CONTIENE il primo). Restore deve fare piu' passate finche' non
    ci sono piu' sentinel da espandere. Cap: max 10 iterazioni anti-loop
    (uno span non dovrebbe annidarsi cosi' profondamente).
    """
    out = translated
    for _ in range(10):
        prev = out
        for sentinel, original in mapping.items():
            out = out.replace(sentinel, original)
        if out == prev:
            return out
    return out


def _apply_prescriptive_map(text: str, target_lang: str = "en") -> str:
    """Canonicalizza i marker §6 (DEVI/NON DEVI/OK/ERRORE) nella forma della
    lingua TARGET, dopo la traduzione LLM. DIREZIONE-AWARE: target=en usa la
    mappa IT→EN, target=it la mappa EN→IT, altre lingue nessuna mappa (il
    marker resta come tradotto dall'LLM). Forza l'imperativo prescrittivo che
    l'LLM tende a parafrasare/lasciare nella lingua sorgente."""
    mapping = _PRESCRIPTIVE_MAP_BY_TARGET.get(target_lang)
    if not mapping:
        return text
    out = text
    for src, dst in mapping.items():
        out = out.replace(src, dst)
    return out


def _extract_jinja_placeholders(text: str) -> set[str]:
    """Estrae l'insieme dei nomi di variabili `{{ name }}` (escluso filtri)."""
    names = set()
    for m in _re.finditer(r"\{\{[-+]?\s*([a-zA-Z_][a-zA-Z0-9_]*)", text):
        names.add(m.group(1))
    return names


def _validate_translation(it_text: str, en_text: str) -> tuple[bool, list[str]]:
    """Validation post-traduzione. Ritorna (ok, errors).

    Quattro check obbligatori:
    1. Sintassi Jinja2 valida (parse OK via MiniJinja undeclared_variables_in_str).
    2. Set placeholder Jinja2 identico (numero + nomi).
    3. Lunghezza EN nel range 0.7-1.4× IT (semantic drift detector).
    4. Nessun sentinel leak (`__METNOS_INV_<hex>__`) — segnala corruzione
       della pipeline mask/restore (LLM ha hallucinato o dropped un sentinel).
    """
    errors: list[str] = []
    # 1) Sintassi Jinja2
    try:
        import minijinja
        env = minijinja.Environment()
        env.undeclared_variables_in_str(en_text)
    except Exception as exc:
        errors.append(f"jinja_syntax: {exc}")
    # 2) Placeholder set match
    it_phs = _extract_jinja_placeholders(it_text)
    en_phs = _extract_jinja_placeholders(en_text)
    missing = it_phs - en_phs
    extra = en_phs - it_phs
    if missing:
        errors.append(f"placeholder_missing_in_en: {sorted(missing)}")
    if extra:
        errors.append(f"placeholder_extra_in_en: {sorted(extra)}")
    # 3) Length ratio
    it_len = len(it_text)
    en_len = len(en_text)
    if it_len > 0:
        ratio = en_len / it_len
        if ratio < 0.7 or ratio > 1.4:
            errors.append(
                f"len_ratio_out_of_range: it={it_len} en={en_len} "
                f"ratio={ratio:.2f} (expected 0.7-1.4)"
            )
    # 4) Sentinel leak: nessun `__METNOS_INV_<hex>__` deve sopravvivere.
    leaked = _re.findall(r"__METNOS_INV_[a-f0-9]+__", en_text)
    if leaked:
        errors.append(f"sentinel_leak: {len(leaked)} sentinel non ripristinati "
                       f"(LLM ha hallucinato un uuid o dropped uno span). "
                       f"Esempio: {leaked[0]}")
    return (not errors), errors


# Prompt-of-prompt: istruzioni al LLM per tradurre un prompt LLM.
# Scritto in inglese: il LLM riceve istruzioni operative meta + il
# contenuto IT da tradurre. Le istruzioni sono PIU' rigorose di quelle
# per messaggi brevi (vedi _PROMPT_TEMPLATE_LLM_TARGETED) perche' qui
# l'output e' direttamente un prompt destinato a un altro LLM.
_PROMPT_FILE_TEMPLATE = """You are translating a long Metnos LLM prompt from {source_name} to {target_name}.

The output will be consumed DIRECTLY by another LLM (planner, intent extractor, code generator). Maximum semantic and structural fidelity is required.

CRITICAL PRESERVATION RULES:

1. SENTINELS — preserve EXACTLY tokens of the form `__METNOS_INV_<32 lowercase hex>__`. They are unique opaque placeholders standing in for code blocks, Jinja2 expressions, raw blocks, and inline backticks. RULES:
   - COPY the sentinel character-by-character. Do NOT modify, do NOT abbreviate, do NOT shorten.
   - Do NOT invent NEW sentinels of this format. If you need a placeholder in a sentence, use natural English (e.g., "the appropriate value", "{{var}}", "<placeholder>"), NEVER `__METNOS_INV_<random hex>__`.
   - Do NOT drop a sentinel from your output. If you see N sentinels in the source, you MUST emit AT LEAST N sentinels in the output (one for each).
   - Sentinels are idempotent: surrounding text can be translated freely, but the sentinel itself is opaque.

2. CANONICAL IDENTIFIERS — keep as-is in both languages (vocabulary closed, EN-only): tool/executor names (find_files, read_messages, get_now, request_new_executor, classify_entries, describe_entries, sort_entries, filter_entries, compute_entries, compute_files, get_files, get_urls, get_location, get_places, find_packages, list_processes, find_processes, list_dirs, find_dirs, find_urls, login_sites, group_entries, send_messages, move_messages, read_files, write_files, delete_files, move_files, create_dirs, delete_dirs, undo_last_turn, change_images, compress_dirs_gz, compress_files_gz, describe_dirs, extract_files_zip, filter_texts_lines, get_inputs, admin, sudoer, scratchpad_read, request_location_from_user, ...), arg names (from_step, near, radius_km, bounded, queries, max_results, entries, paths, content, dst_template, time_window, account, subject_contains, ...), action verbs ({canonical_actions}), object nouns ({canonical_objects}), modifiers ({canonical_qualifiers}). Metnos DOMAIN NOUNS — keep in ENGLISH in ALL target languages, do NOT translate them (e.g. NOT "esecutore"/"manifesto"): executor, executors, manifest, manifests, runtime, planner, synt, fastpath, autopath, scratchpad.

3. STRUCTURAL FORMATTING — preserve LITERALLY: section headers (═══ separators, ## headings), numbered rules (1., 2., 2-bis, 2-ter, 2-quater), bullet lists, indentation, JSON examples (translate human-readable text fields BUT keep keys/identifiers/values that are technical literally).

4. PRESCRIPTIVE TONE — Metnos prompts use strong imperative. Translate "DEVI:" to "MUST:", "NON DEVI:" to "MUST NOT:", "OK:" stays "OK:", "ERRORE:" to "ERROR:". Translate "E' UN ERRORE" to "THIS IS AN ERROR". Keep ALL CAPS for emphasis. Do NOT soften ("you should").

5. FEW-SHOT EXAMPLES — translate the user's natural-language query but PRESERVE the structured output exactly. E.g., translate `User: 'comprimi /tmp/log.txt con gzip'` to `User: 'compress /tmp/log.txt with gzip'`, but keep `Output: {{"name": "compress_files_gz", ...}}` IDENTICAL.

6. NEVER OMIT, NEVER ADD content. Same number of rules, same number of examples, same structure. The translated text must have the same instructional weight as the original.

7. Output ONLY the translated text. NO prose meta-commentary, NO markdown fences, NO "Here is the translation:", NO trailing notes. Begin output with the first character of the translated content.

Source ({source_name}) prompt below. Translate to {target_name}, applying ALL preservation rules.

────────────────────────────────────────────
{source_text}
────────────────────────────────────────────

Output the translated prompt now (no fences, no prose):"""


def _llm_call_for_prompt(prompt: str, max_tokens: int = 32000,
                          tier: str = "wise") -> str | None:
    """LLM call dedicato per traduzione prompt lunghi.

    Tier `wise` = frontier locale (modello locale) o online (Anthropic/OpenAI/
    Google/Mistral) come configurato in `~/.config/metnos/llm_tiers.toml`.

    `frontier` non e' un tier separato in Metnos: il quality floor del
    `wise` (vedi llm_router.WISE_QUALITY_WHITELIST_*) impone di per se'
    il modello locale o superiore.
    """
    try:
        from llm_router import LLMRouter
    except ImportError as exc:
        log.error("LLMRouter non disponibile: %s", exc)
        return None
    try:
        r = LLMRouter()
        provider = r.provider(tier)
        # max_tokens generoso: alcuni prompt arrivano a 27 KB; con ratio
        # 1.4 max servono ~38 KB. 32000 token ≈ 100-130 KB testo, sufficiente.
        res = provider.chat("", prompt, max_tokens=max_tokens, temperature=0,
                              think=False)
        return res.text or ""
    except Exception as exc:
        log.warning("LLM call failed (tier=%s): %s", tier, exc)
        return None


def _scrub_unknown_sentinels(text: str, known: set[str]) -> tuple[str, int]:
    """Sostituisce qualsiasi `__METNOS_INV_<hex>__` non presente in `known`
    con la stringa fissa `{{var}}` (placeholder Jinja2 generico, sintatticamente
    valido — il maintainer in review umana lo correggera').

    Workaround per LLM che hallucina sentinel (LLM "ricorda" il pattern e
    inventa un uuid). Documentato come issue noto: il candidato viene
    comunque marcato `ok=False` per la perdita di sentinel originale, ma
    almeno non lascia stringhe rotte di tipo `__METNOS_INV_xyz__` nel
    prompt finale che verra' consumato da un altro LLM.
    """
    leaked = 0
    def _repl(m: _re.Match[str]) -> str:
        nonlocal leaked
        if m.group(0) in known:
            return m.group(0)  # known sentinel survived — leave it (will restore)
        leaked += 1
        return "{{var}}"
    out = _re.sub(r"__METNOS_INV_[a-f0-9]+__", _repl, text)
    return out, leaked


def translate_prompt_file(role: str, target_lang: str = "en",
                           source_lang: str = "it",
                           tier: str = "wise",
                           max_retries: int = 1) -> dict:
    """Traduce un singolo prompt file da `prompts/<source_lang>/<role>.j2`
    a `prompts/<target_lang>/_pending/<role>.j2.candidate`.

    Pipeline:
    1. legge sorgente
    2. maschera span da preservare (Jinja2 + code blocks)
    3. UNA call LLM tier=`tier` con _PROMPT_FILE_TEMPLATE
    4. scrub di eventuali sentinel hallucinati dal LLM
    5. unmask + applica mappa prescrittiva (DEVI→MUST etc.)
    6. valida (sintassi, placeholder, len ratio, sentinel leak)
    7. retry UNA volta se sentinel leak detected (LLM puo' essere stocastico)
    8. salva candidato in `_pending/`

    Ritorna dict {ok, role, source_path, candidate_path, validation,
    it_len, en_len, ratio, error?, retries}. Il file candidato e' salvato
    anche se validation fallisce (per debug); `ok=False` segnala il problema.
    Il maintainer chiama `mark-synced` SOLO se ok=True.
    """
    runtime_dir = Path(__file__).parent
    src_path = runtime_dir / "prompts" / source_lang / f"{role}.j2"
    pending_dir = runtime_dir / "prompts" / target_lang / "_pending"
    candidate_path = pending_dir / f"{role}.j2.candidate"

    if not src_path.is_file():
        return {"ok": False, "role": role, "error": f"source not found: {src_path}"}

    src_text = src_path.read_text(encoding="utf-8")

    last_errors: list[str] = []
    last_final: str = ""
    last_ratio: float = 0.0
    last_scrubbed: int = 0
    for attempt in range(max_retries + 1):
        masked, mapping = _mask_invariant_spans(src_text)

        src_name = _LANG_NAMES.get(source_lang, source_lang)
        tgt_name = _LANG_NAMES.get(target_lang, target_lang)
        full_prompt = _PROMPT_FILE_TEMPLATE.format(
            source_name=src_name, target_name=tgt_name, source_text=masked,
            **_VOCAB_PROMPT_ARGS,
        )

        log.info("translate_prompt_file role=%s %s→%s tier=%s src_len=%d "
                  "attempt=%d/%d",
                  role, source_lang, target_lang, tier,
                  len(src_text), attempt + 1, max_retries + 1)
        raw = _llm_call_for_prompt(full_prompt, max_tokens=32000, tier=tier)
        if not raw:
            return {"ok": False, "role": role,
                    "error": "llm call returned empty/None (provider unreachable?)"}

        # Strip eventuali markdown fences che il LLM potrebbe aggiungere
        # nonostante l'istruzione contraria (Claude/GPT-5 a volte le mettono).
        cleaned = raw.strip()
        cleaned = _re.sub(r"^```(?:\w+)?\s*\n?", "", cleaned)
        cleaned = _re.sub(r"\n?```\s*$", "", cleaned)

        # Scrub sentinel hallucinati prima del restore (sostituisce con {{var}})
        scrubbed, n_scrubbed = _scrub_unknown_sentinels(cleaned, set(mapping.keys()))
        if n_scrubbed > 0:
            log.warning("role=%s attempt=%d: scrubbed %d hallucinated sentinels",
                          role, attempt + 1, n_scrubbed)
        restored = _restore_invariant_spans(scrubbed, mapping)
        final = _apply_prescriptive_map(restored, target_lang)

        ok, errors = _validate_translation(src_text, final)
        last_errors = errors
        last_final = final
        last_ratio = round(len(final) / max(1, len(src_text)), 3)
        last_scrubbed = n_scrubbed
        if ok:
            break  # validation OK → no retry needed
        # Se la prossima iterazione e' l'ultima, non riprovare per
        # placeholder_missing (legato alla struttura, non transitorio).
        # Retry solo se gli errori includono qualcosa di stocastico
        # (sentinel_leak o len_ratio_out_of_range).
        retryable = any("sentinel_leak" in e or "len_ratio_out_of_range" in e
                          for e in errors)
        if not retryable or attempt >= max_retries:
            break

    pending_dir.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(last_final, encoding="utf-8")

    return {
        "ok": (not last_errors),
        "role": role,
        "source_path": str(src_path),
        "candidate_path": str(candidate_path),
        "validation": last_errors,
        "it_len": len(src_text),
        "en_len": len(last_final),
        "ratio": last_ratio,
        "scrubbed_hallucinated_sentinels": last_scrubbed,
    }


def translate_all_prompts(target_lang: str = "en", source_lang: str = "it",
                           tier: str = "wise",
                           skip_existing_synced: bool = True) -> list[dict]:
    """Traduce tutti i `.j2` di `prompts/<source_lang>/` verso `<target_lang>`.

    `skip_existing_synced=True`: se un file gia' esiste in `prompts/<target_lang>/`
    (sincronizzato manualmente in passato), NON viene ritradotto.
    """
    runtime_dir = Path(__file__).parent
    src_dir = runtime_dir / "prompts" / source_lang
    tgt_dir = runtime_dir / "prompts" / target_lang
    if not src_dir.is_dir():
        log.error("source dir non esiste: %s", src_dir)
        return []
    results: list[dict] = []
    for j2 in sorted(src_dir.glob("*.j2")):
        role = j2.stem
        if skip_existing_synced and (tgt_dir / f"{role}.j2").is_file():
            results.append({
                "ok": True, "role": role, "skipped": True,
                "reason": "already synced in target dir",
            })
            continue
        res = translate_prompt_file(role, target_lang=target_lang,
                                     source_lang=source_lang, tier=tier)
        results.append(res)
    return results


# ===========================================================================
# Manifest description alignment (ADR 0092 Phase 4, 5/5/2026)
#
# I 54 manifest TOML degli executor (43 handcrafted + 11 synth) hanno
# `[description]` table con sub-keys per lingua + `[args.properties.<arg>.description]`
# in formato analogo. Ogni manifest ha un siblings `manifest.lang_state.json`
# che traccia version_hash + source_lang per risorsa testuale per lingua.
#
# Pattern latest-wins simmetrico (ADR 0092, 5/5/2026 sera): nessuna lingua
# canonica IT-only. La lingua con version_hash piu' fresco (rispetto al
# precedente snapshot) e' la "edit source" per le altre lingue: viene
# ritradotta in ognuna che presenta source_hash divergente.
#
# Daemon notturno integration: chiamata da `deploy/run_prompts_translator.sh`
# dopo `align_prompts()` (esistente).
# ===========================================================================
import hashlib as _hashlib


def _sha256_text(text: str) -> str:
    """SHA-256 hex prefix-encoded (`sha256:<hex>`). Wrapper deterministico."""
    from hashutil import sha256_prefixed
    return sha256_prefixed(text)


def _translate_short_text(source_text: str, *, source_lang: str,
                           target_lang: str, tier: str = "wise",
                           max_retries: int = 1) -> tuple[str | None, list[str]]:
    """Traduce una stringa breve (description manifest, 100-1500 char) usando
    il pattern di `translate_prompt_file` (mask Jinja2 placeholder, prescriptive
    map IT→EN, validation len-ratio + sentinel leak), ma SENZA chunking
    (non serve: la stringa entra in una singola call LLM).

    Ritorna (translated_text, errors). `translated_text=None` su fallimento
    irrecuperabile.
    """
    src_name = _LANG_NAMES.get(source_lang, source_lang)
    tgt_name = _LANG_NAMES.get(target_lang, target_lang)

    last_errors: list[str] = []
    last_final = ""
    for attempt in range(max_retries + 1):
        masked, mapping = _mask_invariant_spans(source_text)
        prompt = _PROMPT_FILE_TEMPLATE.format(
            source_name=src_name, target_name=tgt_name, source_text=masked,
            **_VOCAB_PROMPT_ARGS,
        )
        log.info("translate_short_text %s→%s tier=%s len=%d attempt=%d/%d",
                  source_lang, target_lang, tier,
                  len(source_text), attempt + 1, max_retries + 1)
        # max_tokens conservativo: una description non supera ~3000 tokens.
        raw = _llm_call_for_prompt(prompt, max_tokens=4000, tier=tier)
        if not raw:
            return None, ["llm_empty: provider unreachable"]
        cleaned = raw.strip()
        cleaned = _re.sub(r"^```(?:\w+)?\s*\n?", "", cleaned)
        cleaned = _re.sub(r"\n?```\s*$", "", cleaned)
        # Per stringhe brevi: eventuali newline interni sono dovuti a markdown
        # del LLM. Le description manifest sono single-line in TOML basic
        # string. Collassiamo qualsiasi newline residuo a spazio.
        scrubbed, n_scrubbed = _scrub_unknown_sentinels(cleaned, set(mapping.keys()))
        restored = _restore_invariant_spans(scrubbed, mapping)
        final = _apply_prescriptive_map(restored, target_lang)
        # Collassa newline (description e' single-line in TOML).
        final = _re.sub(r"[ \t]*\n[ \t]*", " ", final).strip()

        ok, errors = _validate_translation(source_text, final)
        last_errors = errors
        last_final = final
        if ok:
            return final, []
        retryable = any("sentinel_leak" in e or "len_ratio_out_of_range" in e
                          for e in errors)
        if not retryable or attempt >= max_retries:
            break
    return last_final, last_errors


def _load_lang_state(state_path: Path) -> dict:
    """Carica `manifest.lang_state.json` (vuoto se assente)."""
    if not state_path.is_file():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("lang_state corrupt at %s: %s — repristino vuoto",
                      state_path, exc)
        return {}


def _save_lang_state(state_path: Path, state: dict) -> None:
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _enumerate_textual_resources(manifest: dict) -> list[tuple[str, dict]]:
    """Itera (resource_key, lang_table_dict) per tutte le risorse testuali
    multilingua del manifest.

    resource_key = "description" per top-level, "args.<arg>.description"
    per arg-level. Il dict ritornato e' RIFERIMENTO al table in-memory:
    chi modifica il dict modifica il manifest dict in-place.

    Salta risorse che non sono table (es. legacy scalari, anche se loader
    li rifiuta gia' a load time).
    """
    out: list[tuple[str, dict]] = []
    desc = manifest.get("description")
    if isinstance(desc, dict):
        out.append(("description", desc))
    args = manifest.get("args") or {}
    props = args.get("properties") or {}
    for arg_name, arg_def in props.items():
        if isinstance(arg_def, dict):
            d = arg_def.get("description")
            if isinstance(d, dict):
                out.append((f"args.{arg_name}.description", d))
    return out


def _decide_edit_source(state: dict, resource_key: str,
                         lang_table: dict) -> str | None:
    """Determina la "edit source" per una risorsa.

    Pattern latest-wins (ADR 0092 Phase 4):
    - Per ogni lang, calcola `current_hash = sha256(lang_table[lang])`.
    - Se `current_hash` differisce dal `version_hash` salvato in state →
      la lingua e' stata "newly edited" rispetto al precedente snapshot.
    - Se 0 lingue edite: nessun edit, return None (nessuna ritraduzione).
    - Se 1+ lingue edite: la "edit source" e' la prima in ordine alfabetico
      fra le edite (deterministic, niente mtime perche' file unico).

    Nota: la spec parlava di mtime + alphabetic tie-break. Dato che il
    manifest e' UN file, il mtime non discrimina fra lingue. Quindi
    ricadiamo sul tie-break alfabetico (deterministic).
    """
    res_state = state.get(resource_key, {})
    edited_langs = []
    for lang, text in lang_table.items():
        if not isinstance(text, str):
            continue
        current_hash = _sha256_text(text)
        prev_hash = (res_state.get(lang) or {}).get("version_hash")
        if prev_hash != current_hash:
            edited_langs.append(lang)
    if not edited_langs:
        return None
    return sorted(edited_langs)[0]


def align_manifest_descriptions(executor_dirs: list[Path] | None = None,
                                  *, target_langs: list[str] | None = None,
                                  tier: str = "wise",
                                  resign: bool = True,
                                  dry_run: bool = False) -> list[dict]:
    """Sweep dei manifest e auto-allineamento delle description multilingua.

    Per ogni manifest:
      1. Carica lang_state.json (vuoto se assente).
      2. Enumera risorse testuali multilingua (description + args.* descriptions).
      3. Per ogni risorsa: detect edit-source via hash compare, ritraduce
         le altre lingue con `_translate_short_text`.
      4. Aggiorna lang_state con nuovi version_hash + source_lang/source_hash.
      5. Salva manifest.toml + manifest.lang_state.json.
      6. Re-firma Ed25519 (se `resign=True`).

    Args:
        executor_dirs: directory radici degli executor. Default = handcrafted
                       + synth standard.
        target_langs: lingue target da allineare. Default = tutte le sub-dir
                       in `runtime/prompts/` tranne 'it' canonical (per
                       coerenza col daemon prompts). Se nessuna sub-dir
                       trovata, fallback ['en'].
        tier: tier LLM da usare (default 'wise' per fedelta' massima).
        resign: se True, rifirma il manifest dopo ogni modifica.
        dry_run: se True, non scrive file. Solo report.

    Returns:
        Lista di dict per-manifest con esito.
    """
    if executor_dirs is None:
        from pathlib import Path as _P
        from config import PATH_EXECUTORS as _PE
        executor_dirs = [
            _PE,
            _P.home() / ".local" / "share" / "metnos" / "executors",
        ]
    if target_langs is None:
        prompts_dir = Path(__file__).parent / "prompts"
        target_langs = []
        if prompts_dir.is_dir():
            for sub in sorted(prompts_dir.iterdir()):
                if sub.is_dir() and sub.name != "it" and not sub.name.startswith("_"):
                    target_langs.append(sub.name)
        if not target_langs:
            target_langs = ["en"]

    results: list[dict] = []
    for root in executor_dirs:
        if not root.exists():
            continue
        for manifest_path in sorted(root.glob("*/manifest.toml")):
            res = _align_one_manifest(
                manifest_path,
                target_langs=target_langs,
                tier=tier,
                resign=resign,
                dry_run=dry_run,
            )
            results.append(res)
    return results


def _align_one_manifest(manifest_path: Path, *, target_langs: list[str],
                         tier: str, resign: bool, dry_run: bool) -> dict:
    """Allinea un singolo manifest. Ritorna dict con esito."""
    import tomllib
    manifest_dir = manifest_path.parent
    state_path = manifest_dir / "manifest.lang_state.json"

    text_orig = manifest_path.read_text(encoding="utf-8")
    try:
        manifest = tomllib.loads(text_orig)
    except tomllib.TOMLDecodeError as e:
        return {"ok": False, "path": str(manifest_path),
                "status": "parse_error", "error": str(e)}

    state = _load_lang_state(state_path)
    resources = _enumerate_textual_resources(manifest)
    if not resources:
        return {"ok": True, "path": str(manifest_path),
                "status": "no_multilang_descriptions"}

    n_translated = 0
    errors_per_resource: dict[str, list[str]] = {}
    text_replacements: list[tuple[str, str, str, str]] = []
    # text_replacements: list of (resource_key, target_lang, src_text, translated_text)
    # Applicate sul text_orig dopo aver completato tutte le decisioni.

    state_changed = False

    for resource_key, lang_table in resources:
        edit_source = _decide_edit_source(state, resource_key, lang_table)
        # Sempre aggiorna i version_hash delle lingue presenti
        # (anche senza edit_source, possiamo avere lingue nuove o
        # diverse rispetto al snapshot).
        for lang, text in lang_table.items():
            if not isinstance(text, str):
                continue
            current_hash = _sha256_text(text)
            res_state = state.setdefault(resource_key, {})
            entry = res_state.setdefault(lang, {
                "version_hash": "",
                "source_lang": None,
                "source_hash": None,
            })
            if entry.get("version_hash") != current_hash:
                entry["version_hash"] = current_hash
                state_changed = True

        # Determina la "best source" per le traduzioni.
        # - Se c'e' un edit_source, e' quella (lingua appena modificata).
        # - Altrimenti, se almeno un target_lang e' MANCANTE (genesi
        #   monolingua, prima esecuzione daemon), si usa la prima lingua
        #   alfabetica disponibile in lang_table come source verso il
        #   target mancante.
        if edit_source is None:
            missing_targets = [
                t for t in target_langs if t not in lang_table
            ]
            if not missing_targets:
                continue  # tutte le target lang esistono e sono in-sync
            if not lang_table:
                continue  # niente su cui basarsi
            # Best source = prima alfabetica fra lingue presenti.
            best_source = sorted(lang_table.keys())[0]
            translation_source = best_source
        else:
            translation_source = edit_source

        # Per ogni target lang DIVERSA dalla source: se source_hash
        # divergente o lingua mancante → ritraduci.
        src_text = lang_table[translation_source]
        src_hash = _sha256_text(src_text)
        for tgt_lang in target_langs:
            if tgt_lang == translation_source:
                continue
            res_state = state.get(resource_key, {})
            tgt_entry = res_state.get(tgt_lang) or {}
            cur_source_hash = tgt_entry.get("source_hash")
            if cur_source_hash == src_hash and tgt_lang in lang_table:
                # In-sync con la edit-source: nulla da fare per questa lingua.
                continue
            if dry_run:
                # Skip LLM call. Solo annota nel report.
                errors_per_resource.setdefault(
                    resource_key, []
                ).append(f"would_translate {translation_source}→{tgt_lang}")
                continue
            # Retranslate.
            translated, errs = _translate_short_text(
                src_text,
                source_lang=translation_source,
                target_lang=tgt_lang, tier=tier,
            )
            if errs:
                errors_per_resource.setdefault(
                    resource_key, []
                ).extend([f"{translation_source}→{tgt_lang}: {e}" for e in errs])
            if translated is None:
                continue
            n_translated += 1
            text_replacements.append(
                (resource_key, tgt_lang, src_text, translated),
            )
            # Aggiorna state per la lingua tradotta.
            tgt_state = state.setdefault(resource_key, {}).setdefault(tgt_lang, {})
            tgt_state["source_lang"] = translation_source
            tgt_state["source_hash"] = src_hash
            tgt_state["version_hash"] = _sha256_text(translated)
            state_changed = True

    # Applica le sostituzioni testuali sul TOML.
    text_new = text_orig
    if text_replacements and not dry_run:
        text_new = _apply_lang_table_replacements(
            text_orig, text_replacements,
        )

    if dry_run:
        return {
            "ok": True, "path": str(manifest_path),
            "status": "dry_run",
            "n_would_translate": n_translated + sum(
                len(v) for v in errors_per_resource.values()
            ),
            "report": errors_per_resource,
        }

    if text_new != text_orig:
        manifest_path.write_text(text_new, encoding="utf-8")
    if state_changed:
        _save_lang_state(state_path, state)

    sign_status = None
    if resign and (text_new != text_orig or state_changed):
        try:
            from sign import sign_executor
            sign_executor(manifest_dir)
            sign_status = "signed"
        except Exception as exc:
            sign_status = f"sign_failed: {type(exc).__name__}: {exc}"

    return {
        "ok": True, "path": str(manifest_path),
        "status": "aligned" if n_translated > 0 else "noop",
        "n_translated": n_translated,
        "errors": errors_per_resource,
        "sign": sign_status,
    }


def _apply_lang_table_replacements(text: str,
                                     replacements: list[tuple[str, str, str, str]]) -> str:
    """Applica sostituzioni in `[description]` o `[args.properties.<arg>.description]`
    del manifest TOML.

    Ogni replacement = (resource_key, target_lang, src_text, translated_text).
    `src_text` non viene usato per il match (la sostituzione e' basata sulla
    posizione nel TOML, non sul contenuto). Strategia:

    1. Trova la sezione `[description]` (o `[args.properties.<arg>.description]`).
    2. Cerca riga `<target_lang> = "..."` dentro la sezione.
    3. Se presente → sostituisce il valore.
       Se assente → aggiunge la riga `<target_lang> = "..."` alla fine
       della sezione (prima della prossima `[...]` o EOF).

    Modifica testuale, non re-render del TOML (preserva commenti / ordine).
    """
    out = text
    for resource_key, tgt_lang, _src, translated in replacements:
        if resource_key == "description":
            section_header = "[description]"
        elif resource_key.startswith("args.") and resource_key.endswith(".description"):
            arg_name = resource_key[len("args."):-len(".description")]
            section_header = f"[args.properties.{arg_name}.description]"
        else:
            log.warning("unknown resource_key %r — skipping", resource_key)
            continue

        out = _replace_lang_in_section(out, section_header, tgt_lang, translated)
    return out


def _replace_lang_in_section(text: str, section_header: str, lang: str,
                               new_value: str) -> str:
    """Trova `section_header` in `text`, sostituisce o aggiunge la riga
    `<lang> = "..."` con `new_value`. Ritorna text modificato.

    Idempotente: se non trova il section_header, ritorna text invariato
    (il caller dovrebbe averlo gia' verificato).
    """
    # Trova l'inizio della sezione (linea che inizia con section_header).
    pattern = _re.compile(
        r'^' + _re.escape(section_header) + r'\s*$',
        _re.MULTILINE,
    )
    m = pattern.search(text)
    if m is None:
        log.warning("section %r non trovata in manifest", section_header)
        return text

    # Trova la fine della sezione: prossima riga che inizia con `[`, oppure EOF.
    section_start = m.end()  # inizio del corpo (dopo il `\n` del header)
    next_section = _re.compile(r'^\[', _re.MULTILINE)
    nxt = next_section.search(text, pos=section_start)
    section_end = nxt.start() if nxt else len(text)

    section_body = text[section_start:section_end]

    # Cerca la riga `<lang> = "..."` esistente.
    lang_pattern = _re.compile(
        r'^' + _re.escape(lang) + r'\s*=\s*"(?:[^"\\]|\\.)*"\s*$',
        _re.MULTILINE,
    )
    lm = lang_pattern.search(section_body)
    escaped_value = _toml_escape_basic(new_value)
    new_line = f'{lang} = "{escaped_value}"'

    if lm is not None:
        # Sostituisci in place.
        new_body = section_body[:lm.start()] + new_line + section_body[lm.end():]
    else:
        # Aggiungi alla fine del body (prima della prossima section).
        # Strip newline finali multipli per uniformita'.
        body = section_body.rstrip("\n")
        if body and not body.endswith("\n"):
            body += "\n"
        new_body = body + new_line + "\n"
        # Preserva la riga vuota di separazione prima della prossima section.
        if nxt is not None:
            new_body += "\n"
    return text[:section_start] + new_body + text[section_end:]


def _toml_escape_basic(s: str) -> str:
    """Escape per stringa basic TOML single-line."""
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t"))


# ===========================================================================
# Layer 1 / Layer 3 latest-wins alignment (estensione ADR 0092, 6/5/2026)
#
# Pattern unificato sui 3 layer multilingua:
#   Layer 1: runtime/prompts/<lang>/<role>.j2  (prompt LLM)
#   Layer 2: <executor>/manifest.toml [description]  (gia' implementato Phase 4)
#   Layer 3: i18n.sqlite                       (DB i18n centralizzato)
#
# Per Layer 1 e Layer 3 vediamo qui la versione latest-wins simmetrica:
# qualsiasi lingua editata diventa "edit-source" per le altre, detect via
# hash content (no mtime per Layer 3 - mtime per Layer 1 e' tie-break).
# Niente bias canonical IT.
# ===========================================================================


def _file_mtime_safe(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def align_prompts(*, target_langs: list[str] | None = None,
                    tier: str = "wise",
                    dry_run: bool = False) -> list[dict]:
    """Sweep dei `runtime/prompts/<lang>/<role>.j2` e auto-allineamento
    multilingua via pattern latest-wins simmetrico (estensione ADR 0092).

    Per ogni `role` (file `.j2`):
      1. Per ogni lang sub-dir: calcola `current_hash = sha256(file_content)`.
         Se diverso da `lang_state[role].version_hash` salvato → mark
         "newly edited" e aggiorna version_hash nel state.
      2. edit-source = lang con file mtime piu' recente fra le edite
         (tie-break alfabetico). Se 0 lingue edite e tutti i target esistono
         e sono in-sync → noop.
      3. Per ogni altra lang: se source_hash != edit_source.version_hash o
         file mancante → ritraduci da edit_source via `translate_prompt_file`.
         Salva candidato in `prompts/<lang>/_pending/<role>.j2.candidate`
         (mai promosso direttamente — review umana via mark-synced).
      4. Aggiorna lang_state.json per ogni lang con i nuovi hash.

    Robusto a touch/sed/edit fuori canale: hash content, no mtime per
    detect change. Mtime usato solo per scegliere edit-source fra lingue
    multiple edite simultaneamente (tie-break deterministic alfabetico).
    """
    runtime_dir = Path(__file__).parent
    prompts_dir = runtime_dir / "prompts"
    if not prompts_dir.is_dir():
        log.warning("align_prompts: %s does not exist", prompts_dir)
        return []

    sys.path.insert(0, str(runtime_dir))
    import prompt_loader  # type: ignore  # noqa: E402

    # Enumera lingue (sub-dir non-_pending non-_*).
    all_langs = sorted(
        d.name for d in prompts_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )
    if not all_langs:
        return []

    if target_langs is None:
        # Default: tutte le lingue presenti (latest-wins simmetrico).
        target_langs = all_langs[:]
    # Always ensure target_langs are subset of existing (or "to-be-created").
    # Roles: union dei .j2 presenti in qualunque lang sub-dir.
    roles: set[str] = set()
    for lang in all_langs:
        for j2 in (prompts_dir / lang).glob("*.j2"):
            roles.add(j2.stem)
    if not roles:
        return []

    results: list[dict] = []
    # Carica lang_state per ogni lang once (mutated in place, salvato a fine).
    lang_states: dict[str, dict] = {
        lang: prompt_loader.load_lang_state(lang) for lang in all_langs
    }
    state_dirty: set[str] = set()

    for role in sorted(roles):
        # Step 1: per ogni lang con file presente, hash content + detect edit
        present: dict[str, tuple[str, float, str]] = {}
        # present[lang] = (file_text, mtime, current_hash)
        for lang in all_langs:
            p = prompts_dir / lang / f"{role}.j2"
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("align_prompts: cannot read %s: %s", p, exc)
                continue
            cur_hash = _sha256_text(text)
            present[lang] = (text, _file_mtime_safe(p), cur_hash)
            # Update version_hash nel state se diverso.
            entry = lang_states[lang].setdefault(role, {
                "version_hash": "",
                "source_lang": None,
                "source_hash": None,
            })
            if entry.get("version_hash") != cur_hash:
                entry["version_hash"] = cur_hash
                state_dirty.add(lang)

        if not present:
            results.append({"role": role, "status": "no_files"})
            continue

        # Step 2: detect edit-source = lang con hash diverso dal precedente
        # snapshot. Calcoliamo direttamente confrontando entry corrente
        # con `prev_state` (caricato fresh sotto, prima del mutate sopra).
        # In pratica: tracciamo i lang dove `prev_version_hash != cur_hash`.
        # Ricaviamo prev_version_hash usando un secondo pass tramite
        # `_prev_version_hash_for(role, lang)` che legge file del state
        # ORA aggiornato — ma noi avevamo gia' aggiornato. Trick: se
        # state_dirty contiene lang E lang_states[lang][role] e' valorizzato
        # con cur_hash post-update, allora era diverso prima → newly_edited.
        newly_edited: list[str] = []
        for lang, (_text, _mt, cur_hash) in present.items():
            if (lang in state_dirty
                    and lang_states[lang][role]["version_hash"] == cur_hash):
                newly_edited.append(lang)

        # First-run detection (state vuoto a freddo): se TUTTE le entry per
        # questo role hanno source_hash=None nel state pre-aggiornamento,
        # significa che il daemon non ha mai processato questo role. In tal
        # caso, ritraduciamo SOLO le lingue MANCANTI (le presenti si
        # assume siano allineate manualmente, backfill source_hash).
        first_run = all(
            (lang_states[lang].get(role, {}).get("source_hash") is None)
            for lang in present.keys()
        )

        # edit_src: lang scelta come sorgente per ritradurre eventuali target.
        # - Se newly_edited != []: la piu' recente (mtime), tie-break alfabetico.
        # - Altrimenti, se ci sono target_langs MANCANTI da popolare: la
        #   lingua piu' recente fra le presenti.
        # - Altrimenti: noop.
        missing_targets = [t for t in target_langs if t not in present]
        if newly_edited:
            edit_src = sorted(
                newly_edited,
                key=lambda lg: (-present[lg][1], lg),
            )[0]
        elif missing_targets:
            edit_src = sorted(
                present.keys(),
                key=lambda lg: (-present[lg][1], lg),
            )[0]
        else:
            # Nessun edit + nessun target mancante → niente da fare.
            # Su first_run, backfilliamo i source_hash delle lingue
            # presenti (pick edit_src arbitraria mtime+alpha) cosi'
            # al prossimo cycle non riconosciamo come edit i file
            # immutati.
            if first_run:
                edit_src = sorted(
                    present.keys(),
                    key=lambda lg: (-present[lg][1], lg),
                )[0]
                edit_src_hash = present[edit_src][2]
                for tgt in target_langs:
                    if tgt == edit_src or tgt not in present:
                        continue
                    lang_states.setdefault(tgt, {}).setdefault(role, {
                        "version_hash": present[tgt][2],
                        "source_lang": None,
                        "source_hash": None,
                    })
                    lang_states[tgt][role]["source_lang"] = edit_src
                    lang_states[tgt][role]["source_hash"] = edit_src_hash
                    state_dirty.add(tgt)
                results.append({
                    "role": role, "edit_source": edit_src, "newly_edited": [],
                    "candidates": [], "status": "first_run_backfill",
                })
            else:
                results.append({
                    "role": role, "edit_source": None, "newly_edited": [],
                    "candidates": [], "status": "noop",
                })
            continue

        edit_src_hash = present[edit_src][2]

        # Step 3: per ogni target_lang != edit_src, decidi se ritradurre.
        candidates_emitted: list[str] = []
        for tgt in target_langs:
            if tgt == edit_src:
                continue
            tgt_state = lang_states.get(tgt, {}).get(role, {})
            tgt_present = tgt in present
            cur_source_hash = tgt_state.get("source_hash")
            # Se target presente e source_hash combacia con edit_src.version_hash
            # → in-sync, skip.
            if tgt_present and cur_source_hash == edit_src_hash:
                continue
            # First run + target presente: backfill source_hash senza
            # ritradurre (assumiamo allineamento manuale pre-pattern).
            if first_run and tgt_present:
                lang_states.setdefault(tgt, {}).setdefault(role, {
                    "version_hash": present[tgt][2],
                    "source_lang": None,
                    "source_hash": None,
                })
                lang_states[tgt][role]["source_lang"] = edit_src
                lang_states[tgt][role]["source_hash"] = edit_src_hash
                state_dirty.add(tgt)
                continue
            if dry_run:
                candidates_emitted.append(f"would_translate {edit_src}→{tgt}")
                continue
            # Translate. Usa translate_prompt_file ma override source dir
            # via parametro source_lang.
            try:
                res = translate_prompt_file(role,
                                             target_lang=tgt,
                                             source_lang=edit_src,
                                             tier=tier)
            except Exception as exc:
                log.warning("align_prompts translate %s %s→%s failed: %s",
                              role, edit_src, tgt, exc)
                continue
            if res.get("ok") or res.get("candidate_path"):
                candidates_emitted.append(
                    f"{edit_src}→{tgt} candidate={res.get('candidate_path')} "
                    f"ok={res.get('ok')}"
                )
                # Aggiorna state per target (track source_hash per next cycle).
                lang_states.setdefault(tgt, {}).setdefault(role, {
                    "version_hash": "",
                    "source_lang": None,
                    "source_hash": None,
                })
                lang_states[tgt][role]["source_lang"] = edit_src
                lang_states[tgt][role]["source_hash"] = edit_src_hash
                state_dirty.add(tgt)

        results.append({
            "role": role,
            "edit_source": edit_src,
            "newly_edited": newly_edited,
            "candidates": candidates_emitted,
            "status": "aligned" if candidates_emitted else "noop",
        })

    # Salva lang_state.json per ogni lang dirty.
    if not dry_run:
        for lang in state_dirty:
            try:
                prompt_loader.save_lang_state(lang, lang_states[lang])
            except Exception as exc:
                log.warning("align_prompts: save_lang_state %s failed: %s",
                              lang, exc)
    return results


def align_messages(*, target_langs: list[str] | None = None,
                     tier: str = "wise",
                     dry_run: bool = False) -> list[dict]:
    """Sweep DB i18n.sqlite e auto-allineamento via pattern latest-wins
    simmetrico (estensione ADR 0092 al Layer 3, 6/5/2026).

    Per ogni `key` raggruppa rows per lingua. La row con `updated_at` piu'
    recente e' la "edit-source" (proxy di ultima edit nella lingua).
    Per ogni altra lang: se `source_text_hash` != edit_source.version_hash
    → marca `needs_translation=1` (il translator daemon esistente
    `run_one_cycle` poi processa la coda).

    Niente trigger SQLite: tutto runtime-side, stateless.

    Args:
        target_langs: lingue da allineare. Default = tutte le lingue
                       presenti nel DB.
        tier: tier LLM. Passato a `translate_batch` se inline-translate
              richiesto.
        dry_run: se True, solo report (no UPDATE).

    Returns:
        lista di dict per-key con esito.
    """
    conn = i18n._open()
    rows = conn.execute(
        "SELECT key, lang, text, version_hash, source_text_hash, updated_at "
        "FROM i18n WHERE text IS NOT NULL"
    ).fetchall()
    if not rows:
        return []

    # Group per key.
    by_key: dict[str, list[tuple[str, str, str | None, str | None, str]]] = {}
    for row in rows:
        key, lang, text, vhash, src_hash, upd_at = row
        # Backfill version_hash on the fly se mancante.
        if not vhash:
            vhash = _sha256_text(text)
            if not dry_run:
                conn.execute(
                    "UPDATE i18n SET version_hash=? WHERE key=? AND lang=?",
                    (vhash, key, lang),
                )
        by_key.setdefault(key, []).append((lang, text, vhash, src_hash, upd_at))
    if not dry_run:
        conn.commit()

    if target_langs is None:
        target_langs = sorted({r[1] for r in rows})

    results: list[dict] = []
    n_marked = 0
    for key, entries in by_key.items():
        if len(entries) < 2:
            results.append({"key": key, "status": "single_lang"})
            continue
        # Edit-source: row con updated_at piu' recente, tie-break alfabetico
        # sul lang.
        sorted_entries = sorted(entries, key=lambda e: (e[4], e[0]), reverse=True)
        edit_src_lang, edit_src_text, edit_src_vhash, _, _ = sorted_entries[0]
        # Per ogni altra lang in target_langs: se source_text_hash !=
        # edit_src_vhash → mark needs_translation=1.
        needing: list[str] = []
        for lang, _text, _vh, src_hash, _upd in entries:
            if lang == edit_src_lang:
                continue
            if lang not in target_langs:
                continue
            if src_hash != edit_src_vhash:
                needing.append(lang)
                if not dry_run:
                    conn.execute(
                        "UPDATE i18n SET needs_translation=1 "
                        "WHERE key=? AND lang=?", (key, lang),
                    )
                    n_marked += 1
        results.append({
            "key": key,
            "edit_source": edit_src_lang,
            "marked_for_retranslate": needing,
            "status": "marked" if needing else "in_sync",
        })

    if not dry_run:
        conn.commit()
    log.info("align_messages: %d keys, %d rows marked for retranslate",
              len(results), n_marked)
    return results


def _resolve_tier_arg(argv: list[str]) -> str:
    """Resolve `--quality {wise,frontier}` da argv. Default `wise`.

    Permette anche `--quality=wise` (= sintassi). Restituisce nome tier
    canonico in `DEFAULT_TIERS` (`wise` o `frontier`).
    """
    for i, a in enumerate(argv):
        if a == "--quality" and i + 1 < len(argv):
            v = argv[i + 1]
            if v in ("wise", "frontier"):
                return v
        if a.startswith("--quality="):
            v = a.split("=", 1)[1]
            if v in ("wise", "frontier"):
                return v
    return "wise"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        n, r = run_one_cycle()
        print(f"Translated {n} entries, remaining {r}")
    elif len(sys.argv) > 1 and sys.argv[1] == "align-prompts":
        # Layer 1: prompts .j2 latest-wins.
        dry = "--dry-run" in sys.argv
        tier = _resolve_tier_arg(sys.argv)
        results = align_prompts(dry_run=dry, tier=tier)
        n_aligned = sum(1 for r in results if r.get("status") == "aligned")
        n_noop = sum(1 for r in results if r.get("status") == "noop")
        print(f"prompts scanned: {len(results)}  aligned={n_aligned}  "
              f"noop={n_noop}  tier={tier}")
        for r in results:
            if r.get("candidates"):
                print(f"  {r['role']} (edit_src={r.get('edit_source')}): "
                      f"{len(r['candidates'])} candidate(s)")
                for c in r["candidates"]:
                    print(f"    {c}")
    elif len(sys.argv) > 1 and sys.argv[1] == "align-messages":
        # Layer 3: DB i18n.sqlite latest-wins.
        dry = "--dry-run" in sys.argv
        tier = _resolve_tier_arg(sys.argv)
        results = align_messages(dry_run=dry, tier=tier)
        n_marked = sum(1 for r in results if r.get("status") == "marked")
        n_sync = sum(1 for r in results if r.get("status") == "in_sync")
        n_single = sum(1 for r in results if r.get("status") == "single_lang")
        print(f"messages scanned: {len(results)}  "
              f"marked={n_marked}  in_sync={n_sync}  single_lang={n_single}  "
              f"tier={tier}")
        if dry:
            for r in results:
                if r.get("status") == "marked":
                    print(f"  {r['key']} edit_src={r.get('edit_source')} "
                          f"needs_retranslate={r.get('marked_for_retranslate')}")
    elif len(sys.argv) > 1 and sys.argv[1] == "align-manifests":
        # ADR 0092 Phase 4: allineamento description manifest multilingua.
        dry = "--dry-run" in sys.argv
        no_resign = "--no-resign" in sys.argv
        tier = _resolve_tier_arg(sys.argv)
        results = align_manifest_descriptions(dry_run=dry, resign=not no_resign,
                                                tier=tier)
        n_aligned = sum(1 for r in results if r.get("status") == "aligned")
        n_noop = sum(1 for r in results if r.get("status") == "noop")
        n_failed = sum(1 for r in results if not r.get("ok"))
        n_dry = sum(1 for r in results if r.get("status") == "dry_run")
        print(f"\nManifest scanned: {len(results)} (tier={tier})")
        print(f"  aligned: {n_aligned}")
        print(f"  noop (in-sync): {n_noop}")
        print(f"  dry_run: {n_dry}")
        print(f"  failed: {n_failed}")
        for r in results:
            if not r.get("ok"):
                print(f"  FAIL {r.get('path')}: {r.get('error')}")
            elif r.get("errors"):
                for k, v in r["errors"].items():
                    for msg in v:
                        print(f"  WARN {r.get('path')}: {k}: {msg}")
    else:
        run_loop()
