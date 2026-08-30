"""from_contains_resolver.py — risoluzione DETERMINISTICA del filtro-mittente.

Bug live 22/6: «Cerca nelle mie email i pagamenti Anthropic» / «le fatture da
Anthropic» → l'LLM NON setta `from_contains=Anthropic` (copia la FORMA dal PATTERN
del manifest §2.5, non l'entità della query) → read broad (centinaia di mail) →
`extract_entries` capa a 50 sorgenti → trova solo poche fatture (incompleto).
Il segnale robusto è la QUERY: una preposizione di provenienza «da/from
<NomeProprio>», oppure un nome-commerciale «(fatture/pagamenti/ordini/…)
<NomeProprio>», NOMINA il mittente/vendor.

Gemello di `mail_account_resolver` / `time_window_resolver`. Deterministico §7.9.
CONSERVATIVO (un from_contains sbagliato filtra troppo → read 0 → nota onesta
§2.11, recuperabile, MAI un'azione mutating): scatta SOLO se
- tool == read_messages, canale email;
- `from_contains` E `subject_contains` NON già settati (l'LLM/utente vince);
- l'entità è un NomeProprio CAPITALIZZATO (segnale «brand/persona», non parola
  comune), non una stopword/giorno/mese/parola-mail, non un account configurato
  (quello è dell'account-resolver);
- candidato UNICO: 0 o ≥2 entità distinte → noop (ambiguo → decide il planner).

NB casing: query tutta-minuscola («pagamenti anthropic») → nessun NomeProprio →
noop (l'LLM resta responsabile). È un limite VOLUTO: la maiuscola è ciò che
rende il segnale sicuro.
"""
from __future__ import annotations
import re

import detection_lexicon as _detlex
import detection_lexicon_seed_resolvers as _resolver_seed

# NomeProprio candidato: inizia con lettera, >=3 char (lettere/cifre/&.+-). La
# CAPITALIZZAZIONE si verifica in codice (le regex usano IGNORECASE per i
# nomi-comuni → [A-Z] non basterebbe).
_TOKEN = r"([A-Za-z][\w&.+-]{2,})"


def _alternation(forms, *, suffix: str = "") -> str:
    return "|".join(
        re.escape(str(form)) + suffix
        for form in sorted(forms or (), key=lambda item: -len(str(item)))
        if str(form).strip()
    )


def _patterns_and_stopwords():
    """Compone la grammatica stabile usando solo superfici catalogate."""
    _resolver_seed.ensure_registered()
    lexicon = _detlex.mapping("resolver.from_contains")
    vendor = _alternation(lexicon.get("vendor_root"), suffix=r"\w*")
    direct = _alternation(lexicon.get("direct_preposition"))
    vendor_prep = _alternation(lexicon.get("vendor_preposition"))
    if not vendor or not direct or not vendor_prep:
        return (), set(), None
    patterns = (
        re.compile(
            r"(?<!\w)(?:" + direct + r")\s+" + _TOKEN,
            re.IGNORECASE,
        ),
        re.compile(
            r"(?<!\w)(?:" + vendor + r")(?!\w)"
            r"(?:\s+\S+){0,2}?\s+"
            r"(?:(?:" + vendor_prep + r")\s+)?" + _TOKEN,
            re.IGNORECASE,
        ),
    )
    stop = {
        str(form).casefold() for form in lexicon.get("stopword", ())
    }
    vendor_full = re.compile(r"(?:" + vendor + r")", re.IGNORECASE)
    return patterns, stop, vendor_full


def _candidates(query: str, *, require_capital: bool = True) -> list[str]:
    """NomiPropri (capitalizzati, non-stop) introdotti da «da/from» o da un
    nome-commerciale. Ordine di apparizione, deduplicati case-insensitive.

    `require_capital=False` (fix 3/7, recupero autoreferenza sotto): rilassa
    il segnale-maiuscola SOLO quando gia' sappiamo che il valore attuale e'
    provatamente sbagliato (vedi `resolve_from_contains`) — la STOP-list resta
    l'unico guard, come prima."""
    patterns, stopwords, vendor_full = _patterns_and_stopwords()
    found: list[str] = []
    for pat in patterns:
        for m in pat.finditer(query):
            tok = m.group(1)
            if require_capital and not tok[:1].isupper():
                continue  # NomeProprio richiede maiuscola iniziale
            if tok.casefold() in stopwords:
                continue
            # Mai il nome-commerciale stesso come candidato: e' la parola che
            # INTRODUCE il vendor ("bollette"), non il vendor ("plenitude").
            if vendor_full is not None and vendor_full.fullmatch(tok):
                continue
            found.append(tok)
    # dedup preservando l'ordine (case-insensitive)
    seen, uniq = set(), []
    for t in found:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq


def resolve_from_contains(tool: str, args: dict, query: str) -> dict:
    """Inietta `from_contains=<NomeProprio>` su read_messages quando la query
    nomina il mittente ma l'arg è vuoto. Ritorna args (copia se modificati).
    Mai eccezioni: su dubbio, noop.

    Fix 3/7 (bug live): l'LLM a volte ripete la parola-categoria stessa come
    from_contains ("bollette plenitude ed enel" → from_contains="bollette")
    invece del vendor nominato — DEFINIZIONALMENTE sbagliato (un mittente non
    si chiama mai "bollette"/"fatture"/...), quindi qui NON vince: si ritenta
    la risoluzione (maiuscola non richiesta, il valore e' gia' provato
    sbagliato) e si sovrascrive; se nessun candidato univoco, si AZZERA il
    valore (mai tenere un filtro che garantisce 0 risultati onesti ma inutili
    — meglio una ricerca piu' ampia, §2.8).
    """
    if tool != "read_messages" or not isinstance(args, dict) or not query:
        return args
    existing_from = str(args.get("from_contains") or "").strip()
    _patterns, _stops, vendor_full = _patterns_and_stopwords()
    self_referential = bool(existing_from) and bool(
        vendor_full is not None and vendor_full.fullmatch(existing_from))
    if args.get("subject_contains"):
        return args  # filtro testuale già presente: l'LLM/utente vince
    if args.get("from_contains") and not self_referential:
        return args  # filtro plausibile già presente: l'LLM/utente vince
    via = str(args.get("via_channel") or "").strip().lower()
    if via not in ("", "email", "mail"):
        return args
    cands = _candidates(query, require_capital=not self_referential)
    if not cands:
        if self_referential:
            out = dict(args)
            out["from_contains"] = None
            return out
        return args
    # Escludi gli account configurati (li canonicalizza l'account-resolver).
    try:
        from mail_client import list_known_accounts
        known = {a.lower() for a in (list_known_accounts() or [])}
    except Exception:
        known = set()
    cands = [c for c in cands if c.lower() not in known]
    if len(cands) != 1:
        if self_referential:
            out = dict(args)
            out["from_contains"] = None
            return out
        return args  # 0 o ambiguo (≥2 entità distinte) → decide il planner
    out = dict(args)
    out["from_contains"] = cands[0]
    return out
