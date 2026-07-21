# SPDX-License-Identifier: AGPL-3.0-only
"""engine/cache_validity — firme di validità per le decisioni cachate (ADR 0182).

Principio: ogni piano cachato (L0 fastpath, L1 autopath, alternative-cache del
proposer) porta la FIRMA del mondo in cui fu deciso; alla lettura, se il mondo
è cambiato in un modo che può cambiare la decisione, l'entry è un MISS.

Due assi per piano:
  - `tools_sig`  — anti POSITIVO-stantio: sha256 dei (nome, digest-manifest)
    ordinati dei tool referenziati dal piano. Digest = quello della firma
    §7.10 (codice+schema): re-sign post-edit ⇒ sig diversa ⇒ MISS. Tool
    sparito ⇒ sentinella `nome:!missing` ⇒ MISS (C1 a lettura).
  - `pool_sig`   — anti NEGATIVO-stantio (gemello di `_compute_intent_sig`):
    per ogni clausola (verbo, oggetto) dell'intent, la FAMIGLIA di candidati
    del catalogo; sha256 dell'unione ordinata. Fratello nuovo/rimosso nella
    famiglia ⇒ la decisione va ripresa. Query-indipendente, deterministica
    (niente prefilter/affinity nella firma).

`catalog_epoch` (grana grossa, per la LRU in-process del proposer): sha256 di
TUTTI i (nome, digest) del catalogo — ogni cambio di catalogo di fatto azzera
la cache alternative (costo: un retry-LLM; beneficio: mai un framework di un
mondo passato).

Deterministico §7.9, zero LLM/IO. I costi sono hash su ~80 stringhe corte.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional

# Famiglia produttori (§2.2): per un verbo produttore la famiglia include i
# fratelli-produttori sullo stesso oggetto (find/read/get/list interscambiabili
# nel derive → una capacità nuova in QUALSIASI produttore della famiglia può
# cambiare la decisione).
_PRODUCER_VERBS = ("find", "read", "get", "list")


def _h(parts: Iterable[str]) -> str:
    m = hashlib.sha256()
    for p in parts:
        m.update(p.encode("utf-8"))
        m.update(b"\x00")
    return m.hexdigest()


def digest_map(catalog) -> dict:
    """{nome: digest} dal catalogo caricato (digest='' per builtin/virtual)."""
    out: dict = {}
    for e in (catalog or []):
        n = getattr(e, "name", None)
        if n:
            out[n] = getattr(e, "digest", "") or ""
    return out


def catalog_epoch(catalog) -> str:
    """Firma dell'INTERO catalogo (nome+digest, ordinati). Per cache in-process
    a grana grossa (alternative-cache proposer). Catalogo assente → '' (nessuna
    discriminazione; il chiamante di prod lo passa sempre)."""
    if catalog is None:
        return ""
    dm = digest_map(catalog)
    return _h(f"{n}:{dm[n]}" for n in sorted(dm))[:16]


def _plan_tools(framework) -> list[str]:
    steps = getattr(framework, "steps", None) or []
    out = []
    for s in steps:
        t = getattr(s, "tool", None) or (s.get("tool") if isinstance(s, dict) else None)
        if t and t != "final_answer":
            out.append(t)
    return out


# ROUTING/PRESENTATION EPOCH (Roberto 9/7): la validità di un piano cachato
# dipende anche dalla LOGICA di routing/presentazione con cui fu deciso, non solo
# dal MONDO degli executor (tools_sig/pool_sig). Un fix a routing (target_device,
# prefilter/object-hints, guard dispatch) o presentazione (output_policy) NON
# cambia i digest degli executor → i piani stantìi sopravvivevano e servivano
# esiti sbagliati (bug ricorrenti sessione 9/7: @table doppia, size-bake,
# server-status misroute). Folded in `tools_sig`: BUMP di questa costante ⇒
# tutti i piani L0/L1/alternative diventano MISS per costruzione.
#
# CONVENZIONE: incrementa a OGNI cambio di logica routing/presentazione che può
# cambiare la scelta-tool o il terminale di un piano già-cachabile.
# 2026-07-10.1: provider google_photos (marker+gate), pin-server skill-backed,
# demotion meta-oggetto `entries` nell'intent — tutti cambiano la scelta-tool.
# 2026-07-10.2: magic `@note` nei terminali G/S/L (presentazione: la voce
# `message` dell'executor si appende al final deterministico).
# 2026-07-10.3: carrier §2.2 in _fs_equivalent (files/dirs soddisfano
# images/texts) + teste §2.5 find_images_indices/describe_images (boundary
# find_files) — cambiano la scelta-tool dei piani cachati.
# 2026-07-10.4: disambiguazione overlap marker provider (più-specifico-vince:
# «google» dentro «google photos» non attiva gw) — cambia client/pool.
# 2026-07-10.5: boundary write/send («carica/upload a servizio» = write, send =
# destinatari/canali) + sinonimi carica/upload→write — cambia l'intent.
# 2026-07-10.6: @gallery_fallback nel terminale G (presentazione: entries
# remote senza path → bullet dei campi salienti).
ROUTING_EPOCH = "2026-07-10.6"


def tools_sig(framework, catalog) -> str:
    """Firma dei tool REFERENZIATI dal piano contro i digest correnti + il
    ROUTING_EPOCH (logica routing/presentazione)."""
    dm = digest_map(catalog)
    parts = [f"@epoch:{ROUTING_EPOCH}"]
    for t in sorted(set(_plan_tools(framework))):
        parts.append(f"{t}:{dm.get(t, '!missing')}")
    return _h(parts)[:16]


def _family(verb: str, obj: str, names: set) -> set:
    """Famiglia di candidati per una clausola: canonici + varianti qualifier
    + fratelli-produttori (per i verbi produttori)."""
    fam = set()
    verbs = _PRODUCER_VERBS if verb in _PRODUCER_VERBS else (verb,)
    for v in verbs:
        base = f"{v}_{obj}"
        for n in names:
            if n == base or n.startswith(base + "_"):
                fam.add(n)
    return fam


def pool_sig(intent, catalog) -> str:
    """Firma delle FAMIGLIE di candidati per le clausole dell'intent.
    Intent senza actions → clausola primaria (verb, object)."""
    names = {getattr(e, "name", None) for e in (catalog or [])}
    names.discard(None)
    clauses = []
    for a in (getattr(intent, "actions", None) or []):
        if isinstance(a, dict) and a.get("verb") and a.get("object"):
            clauses.append((a["verb"].lower(), a["object"].lower()))
    if not clauses:
        v = (getattr(intent, "verb", "") or "").lower()
        o = (getattr(intent, "object", "") or "").lower()
        if v and o:
            clauses.append((v, o))
    fam: set = set()
    for v, o in clauses:
        fam |= _family(v, o, names)
    return _h(sorted(fam))[:16]


def plan_sigs(framework, intent, catalog) -> tuple[str, str]:
    """(tools_sig, pool_sig) per la registrazione di un piano. SENZA catalogo
    (chiamante fuori dal turno: test/tool diretti) → firme VUOTE: la riga sarà
    MISS al primo hit e si ri-registrerà dal dispatch (che il catalogo lo ha
    sempre). NIENTE fallback implicito a load_catalog: ha side-effect (stamp
    aging §first_seen) e firmerebbe contro un mondo diverso da quello del
    chiamante."""
    if catalog is None:
        return "", ""
    return tools_sig(framework, catalog), pool_sig(intent, catalog)


def validate(stored_tools_sig: Optional[str], stored_pool_sig: Optional[str],
             framework, intent, catalog) -> tuple[bool, str]:
    """Verifica a LETTURA (hit L0/L1). Ritorna (valida, motivo-se-no).
    Sig vuote/assenti (righe pre-migrazione) = NON valide: MISS una volta,
    la ri-registrazione naturale le rimpiazza con le firme fresche."""
    if not stored_tools_sig or not stored_pool_sig:
        return False, "sig assente (riga pre-ADR-0182)"
    # C1 esplicita: un tool referenziato ASSENTE dal catalogo corrente è
    # invalido a prescindere dalle firme — la sentinella `!missing` pareggia
    # «mancante al record» con «mancante ora» (riga patologica registrata già
    # monca) e senza questo check la riga validerebbe. Mai eseguire un piano
    # con tool fantasma (wrong_tool garantito).
    _names = {getattr(e, "name", None) for e in (catalog or [])}
    _missing = [t for t in set(_plan_tools(framework)) if t not in _names]
    if _missing:
        return False, f"tool mancanti dal catalogo: {sorted(_missing)}"
    ts = tools_sig(framework, catalog)
    if ts != stored_tools_sig:
        return False, f"tools_sig {stored_tools_sig}→{ts} (executor cambiato/sparito)"
    ps = pool_sig(intent, catalog)
    if ps != stored_pool_sig:
        return False, f"pool_sig {stored_pool_sig}→{ps} (famiglia candidati cambiata)"
    return True, ""
