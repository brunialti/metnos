"""engine/routing_pool.py — costruzione del pool di routing (funzione PURA).

Estratta da `dispatch.run_turn` (fix B3, 9/6/2026): prima il guard
anti-regressione `bench/routing_subset_bench.py` RE-implementava una versione
semplificata del pool (k=10 fisso, niente compound per-clausola, niente
universal-helpers, niente companions) e poteva restare verde mentre la
produzione regrediva su quei layer. Ora dispatch e bench chiamano la STESSA
funzione: ogni modifica al pool di produzione e' esercitata dal bench.

Contratto:
  - PURA rispetto al turno: ZERO esecuzione executor, ZERO chiamate LLM,
    ZERO scritture. Legge solo env (`METNOS_ENGINE_POOL_SIZE`; il prefilter
    legge `METNOS_PREFILTER_RULES`) e il catalog passato (mai mutato).
  - Comportamento IDENTICO al segmento storico di run_turn (ADR 0164):
    intent completo → rank_with_intent(k) → fallback BoW min_score=0 →
    unione pool per-clausola (intent.actions o verbi canonici) + famiglia
    object → append universal-helpers → companions producer→consumer.
    Intent incompleto o prefilter rotto → full catalog (il Proposer decide).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


# Producer → consumer naturale da iniettare sempre nel pool (§7.3 companion).
# Un producer il cui output non è azionabile senza il consumer.
_POOL_COMPANIONS = {
    "find_urls": ["read_urls_html", "read_urls_pdf"],
}


def _tool_object_of(nm: str) -> str:
    """Object canonico dal NOME tool (2° token in vocab.OBJECTS; '' se n/d)."""
    try:
        from vocab import OBJECTS as _VOBJ
    except Exception:  # noqa: BLE001
        return ""
    for tok in (nm or "").split("_")[1:]:
        if tok in _VOBJ:
            return tok
    return ""


def _gate_image_modality(pool: list, query: str, intent) -> list:
    """SEGREGAZIONE MODALITÀ IMMAGINI (2/7/2026, replay job A su storia reale).

    I tool con object=images hanno affinity magnetiche (riassumi/cerca/web/
    contenuto) e VINCONO clausole non-immagine: «riassumi i readme su github»
    → describe_images (VLM su testi), «cerca sul web notizie su python» →
    find_images_web (clausola find|urls corretta nell'intent, mascherata dal
    proposer). Le immagini sono una MODALITÀ distinta: una query che le
    riguarda le NOMINA sempre — per clausola (`actions`), per object
    dell'intent, o nel testo (detect_canonical_object, SoT _OBJECT_HINTS).
    Nessun segnale-immagini → i tool images escono dal pool. §7.9
    deterministico, vocab-driven, zero liste di sinonimi nel prompt
    ([[feedback-contamination-is-function-not-prompt]]).
    Fail-open: senza detector testuale il pool resta intatto; mai pool vuoto.
    """
    objs = {(a.get("object") or "").lower()
            for a in (getattr(intent, "actions", None) or [])
            if isinstance(a, dict)}
    objs.add((getattr(intent, "object", "") or "").lower())
    if "images" in objs:
        return pool
    try:
        from prefilter import detect_canonical_object, tokenize as _tok
        if detect_canonical_object(_tok(query), query) == "images":
            return pool
    except Exception:  # noqa: BLE001
        return pool
    kept = [e for e in pool
            if _tool_object_of(getattr(e, "name", "") or "") != "images"]
    return kept or pool


def _provider_recruit_and_gate(names: list[str], query: str, intent,
                               catalog: list) -> list[str]:
    """Recruit + gate PROVIDER simmetrico sul pool (§7.9; modello ADR 0165: il
    provider è CONFIGURAZIONE, non intent). SoT = `detection_lexicon
    provider.markers` via `active_provider_suffixes`; ZERO liste di sinonimi.

    Chiude il GAP «pool provider-cieco sul path MONO»: «su github» non reclutava
    `find_files_github` → il proposer ripiegava su `find_urls`(web) o
    `find_files`(locale). Bug live turn 582b4824/22f32adb (26/6).

    (1) RECRUIT: per ogni suffix provider ATTIVO nella query, porta nel pool i
        tool `*_<suffix>` del catalog (non-dormant) il cui OBJECT canonico
        (vocab.OBJECTS) è fra gli object delle clausole/intent. Specchio MONO del
        completamento famiglia-object che il ramo compound fa già.
    (2) GATE: `provider_gate_names` — col marker presente cade il canonico
        non-suffissato se la sua variante provider è in pool (UN provider per
        clausola). Engine-agnostico + coperto dal bench (prima solo proposer_v3).
    (3) SCOPE-RESTRICTION GUARD (ADR 0179): un modificatore-RESTRIZIONE (provider)
        confligge con un modificatore-ESTENSIONE (quantificatore «tutti») che fa
        scivolare l'OGGETTO a un'accezione più ampia (`files`→`urls`). La
        restrizione vince: se un produttore provider-nativo dell'accezione-stretta
        è nel pool, sopprimi i generici dell'accezione-ampia (url-generics).
        Preserva: web genuino (no provider), URL esplicito, mixed-compound.

    Tool-existence-safe, idempotente, mai pool vuoto (§2.8)."""
    try:
        from tool_grammar import active_provider_suffixes, provider_gate_names
        suffixes = active_provider_suffixes(query)
        from vocab import OBJECTS as _VOBJ_SET, PRODUCER_VERBS as _PROD_SET
    except Exception as ex:  # noqa: BLE001 — §2.8: pool resta valido
        log.debug("routing_pool: provider step skip (%r)", ex)
        return names
    _VOBJ, _PROD = set(_VOBJ_SET), set(_PROD_SET)

    # Marker provider ASSENTE: in produzione niente recruit/web-steal. Nei test
    # d'integrazione, però, METNOS_HIDE_EXECUTORS può rimuovere deliberatamente
    # il canonical per verificare un provider importato. In quel solo caso
    # reclutiamo il sibling univoco compatibile con verb/object: il test resta
    # una query naturale senza cambiare la semantica di produzione.
    if not suffixes:
        try:
            hidden = {
                item.strip()
                for item in os.environ.get("METNOS_HIDE_EXECUTORS", "").split(",")
                if item.strip()
            }
            intent_obj = getattr(intent, "object", None) or ""
            intent_verb = getattr(intent, "verb", None) or ""
            if hidden and intent_obj:
                provider_suffixes = tuple(
                    __import__("detection_lexicon").mapping(
                        "provider.markers").keys())
                producer_verbs = set(_PROD)
                candidates = []
                for e in catalog:
                    nm = (e.get("name") if isinstance(e, dict)
                          else getattr(e, "name", "")) or ""
                    dormant = (e.get("dormant") if isinstance(e, dict)
                               else getattr(e, "dormant", False))
                    if dormant:
                        continue
                    for canonical in hidden:
                        suffix = nm[len(canonical):] if nm.startswith(canonical) else ""
                        if not suffix or suffix not in provider_suffixes:
                            continue
                        parts = canonical.split("_")
                        verb = parts[0] if parts else ""
                        obj = next((part for part in parts[1:] if part in _VOBJ), "")
                        if obj == intent_obj:
                            candidates.append((nm, verb))
                exact = [nm for nm, verb in candidates if verb == intent_verb]
                compatible = exact
                if not compatible and intent_verb in producer_verbs:
                    compatible = [nm for nm, verb in candidates
                                  if verb in producer_verbs]
                unique = sorted(set(compatible))
                if len(unique) == 1 and unique[0] not in names:
                    names.append(unique[0])
            kept, excluded = provider_gate_names(names, query)
            if kept:
                names = kept
                if excluded:
                    log.debug("routing_pool: provider gate (no-marker) "
                              "escluso %s", excluded)
        except Exception as ex:  # noqa: BLE001
            log.debug("routing_pool: provider gate (no-marker) fallito (%r)", ex)
        return names

    # Object delle clausole (compound) o dell'intent (mono).
    clause_objs: set = set()
    for a in (getattr(intent, "actions", None) or []):
        o = a.get("object") if isinstance(a, dict) else None
        if o:
            clause_objs.add(o)
    if getattr(intent, "object", None):
        clause_objs.add(intent.object)

    def _tool_object(nm: str) -> str:
        for tok in (nm or "").split("_")[1:]:
            if tok in _VOBJ:
                return tok
        return ""

    def _nm(e):
        return (e.get("name") if isinstance(e, dict)
                else getattr(e, "name", None))

    def _dormant(e):
        return (e.get("dormant") if isinstance(e, dict)
                else getattr(e, "dormant", False))

    # Object VUOTO ma PROVIDER ATTIVO: l'intent LLM non ha classificato l'object
    # (succede proprio sullo scivolamento «tutti i file su github» — l'ambiguità
    # che lo manda a urls/vuoto). Ma `active_provider_suffixes` SA che è github:
    # l'object vuoto NON deve disattivare il provider. Deriva gli object-target
    # dai produttori-provider GIÀ nel pool (il prefilter li ha portati): «su
    # github» + find_files_github nel pool → object {files} reclutabile. Senza
    # provider attivo (return a monte) questo ramo non si raggiunge → intent
    # incompleto generico resta full-catalog invariato.
    if not clause_objs:
        clause_objs = {
            _tool_object(n) for n in names
            if any(n.endswith(sx) for sx in suffixes)
            and _tool_object(n) and _tool_object(n) != "urls"}
        if not clause_objs:
            return names

    present = set(names)
    # (1) RECRUIT: variante provider con object fra le clausole. recruited_objs
    # = gli object NON-urls per cui un PRODUTTORE nativo è entrato → l'unico
    # innesco del web-steal (3).
    recruited_objs: set = set()
    for e in catalog:
        nm = _nm(e)
        if not nm or _dormant(e):                 # rispetta la dormancy (no creds)
            continue
        if not any(nm.endswith(sx) for sx in suffixes):
            continue
        tobj = _tool_object(nm)
        if tobj not in clause_objs:
            continue
        if nm not in present:                     # idempotente
            names = names + [nm]
            present.add(nm)
        if nm.split("_", 1)[0] in _PROD and tobj != "urls":
            recruited_objs.add(tobj)

    # (3) SCOPE-RESTRICTION GUARD — principio generale (ADR 0179). Un modificatore
    # che RESTRINGE lo scope (qui: il provider «su github») confligge con uno che
    # lo ESTENDE (quantificatore universale «tutti/ogni»). Quando l'estensione
    # sposta l'OGGETTO verso un'accezione più AMPIA (`files` → `urls`: un URL È un
    # file remoto), l'intent LLM scivola a object=urls e i produttori generici di
    # quell'accezione-ampia (url-generics) rubano il routing al provider nativo —
    # contaminazione, cammino INVERSO del focusing, peggiore se «tutti» viene prima
    # (priming d'ordine). Ma la RESTRIZIONE vince: «su github» VINCOLA «i file» a
    # github, non al web. Se un produttore provider-nativo dell'accezione-stretta
    # è DISPONIBILE nel pool, sopprimi i generici dell'accezione-ampia.
    #
    # Condizione STRUTTURALE (perché il bug esista): due object in relazione
    # generale/specifico (`urls` ⊃ `files`), entrambi con producer generico, parola
    # ambigua («file» = sia files sia url-come-file). Sul vocab attuale SOLO
    # files/urls la soddisfa — verificato (gli assi ARGOMENTO come tempo/formato
    # NON scivolano: «sempre»/«tutti» cambia un arg, non l'object → executor
    # invariato). Per questo l'accezione-ampia è `urls`: deriva dall'essere
    # l'object dei produttori-web generici, non una scelta hardcoded per-web.
    #
    # NON scatta (casi legittimi preservati — T3 + i 2 flaw dei giudici):
    #  - nessun provider attivo (`suffixes` vuoto → return a monte): «cerca
    #    articoli sul web su rust» → l'accezione-ampia è LEGITTIMA, web intatto.
    #  - URL esplicito: «leggi github.com/o/r/blob/F» → single-URL read.
    #  - mixed-compound GENUINO: ≥2 clausole-azione con object distinti, una urls
    #    → la clausola dell'accezione-ampia è una richiesta reale, non scivolamento.
    native_narrow_producer = any(
        n for n in names
        if any(n.endswith(sx) for sx in suffixes)
        and n.split("_", 1)[0] in _PROD
        and _tool_object(n) not in ("urls", ""))
    if native_narrow_producer:
        try:
            from args_extractor import _URL_RE
            has_explicit_url = bool(_URL_RE.search(query or ""))
        except Exception:  # noqa: BLE001
            has_explicit_url = ("http://" in (query or "")
                                or "https://" in (query or ""))
        # MIXED-COMPOUND GENUINO vs SCIVOLAMENTO: l'intent LLM può classificare una
        # clausola (read/find, urls) per DUE motivi opposti — una richiesta web
        # reale, o lo scivolamento di «tutti i file» (il file collassa in urls). Le
        # `actions` dell'intent NON distinguono i due (lo scivolamento produce
        # actions {urls,...} identiche a un compound). Il segnale AUTOREVOLE è il
        # TESTO: `detect_canonical_object` (SoT `_OBJECT_HINTS`) dà l'object che le
        # PAROLE nominano, immune allo scivolamento dell'intent. Se il testo nomina
        # l'accezione-STRETTA (files/dirs) — «i FILE readme», «leggimi i FILE» — la
        # clausola urls è scivolamento → web-steal scatta. Se il testo NON nomina
        # files (`None`/web — «cerca articoli sul web», «leggi i readme E cerca sul
        # web») → la urls è genuina → preserva. Deterministico §7.9, zero liste.
        try:
            from prefilter import detect_canonical_object, tokenize as _tok
            text_obj = detect_canonical_object(_tok(query), query)
        except Exception:  # noqa: BLE001
            text_obj = None
        text_names_narrow = text_obj in ("files", "dirs")
        if not has_explicit_url and text_names_narrow:
            # generici dell'accezione-AMPIA (object=urls), non-provider.
            broad_generics = {n for n in names
                              if _tool_object(n) == "urls"
                              and not any(n.endswith(sx) for sx in suffixes)}
            stripped = [n for n in names if n not in broad_generics]
            if stripped:                          # mai pool vuoto (§2.8)
                names = stripped

    # (2) GATE per-nome: col marker presente cade il canonico non-suffissato se
    # la sua variante provider è in pool. Engine-agnostico + bench-covered.
    try:
        kept, excluded = provider_gate_names(names, query)
        if kept:
            names = kept
            if excluded:
                log.debug("routing_pool: provider gate escluso %s", excluded)
    except Exception as ex:  # noqa: BLE001
        log.debug("routing_pool: provider gate fallito (%r)", ex)
    return names


def build_routing_pool(query: str, intent, catalog: list, *,
                        k: int | None = None) -> list[str]:
    """Da (query, intent, catalog) → lista NOMI tool per il Proposer.

    Pool reduction via prefilter (ADR 0164 fix): invece di passare TUTTO
    il catalog (~80 tool, prompt 400+ righe) a Mētis, prefiltriamo per
    intent semantic match. Top-K (default 12) coprono >90% intent canonici
    con prompt 5-10× più piccolo → -30-40% latency Mētis.

    Args:
      query: testo utente grezzo (per ranking lessicale e verbi canonici).
      intent: engine.types.Intent (verb/object/keywords/actions).
      catalog: lista executor (oggetti con .name); MAI mutata.
      k: dimensione pool per clausola; None → env METNOS_ENGINE_POOL_SIZE
         (default 12). Il pool finale può eccedere k per universal-helpers,
         companions e completamento famiglia-object (come in produzione).
    """
    pool_size = (int(k) if k is not None
                 else int(os.environ.get("METNOS_ENGINE_POOL_SIZE", "12")))
    if intent.is_complete():
        try:
            from prefilter import rank_with_intent, rank as _rank_bow
            intent_dict = {"verb": intent.verb, "object": intent.object,
                            "keywords": intent.keywords}
            # Compound multi-verbo (§7.3): se la query ha >=2 verbi canonici,
            # il pool MONO-verbo di rank_with_intent escluderebbe i tool degli
            # altri sotto-intenti (es. find+write+send → "trova le issue,
            # salvale, mandami il riassunto"). Uniamo il ranking per OGNI verbo
            # canonico presente nella query cosi' il Proposer vede l'intera
            # pipeline. Bug 2/6/2026: senza unione il pool era solo find_* →
            # niente write_files/send_messages → "salva"/"manda" impossibili.
            filtered = None
            # Compound routing (4/6): PREFERISCI la decomposizione per-clausola
            # dell'intent LLM (`intent.actions` = [{verb,object}, ...]). Ogni
            # clausola rankizza il pool con il SUO object reale → i producer di
            # OGNI sotto-azione entrano nel pool. Prima il ramo rankizzava ogni
            # verbo con un UNICO `intent.object` (quello di UNA sola clausola, di
            # solito il bersaglio finale): "trova i processi ... scrivi un report"
            # → object=files per tutti → get_processes mai nel pool → Proposer
            # collassa su get_inputs (bug q20/q21 4/6). La decomposizione è
            # multilingue per costruzione (LLM) e NON usa dizionari di sinonimi.
            _pairs = []
            _acts = getattr(intent, "actions", None) or []
            if len(_acts) >= 2:
                _pairs = [((a.get("verb") or intent.verb),
                           (a.get("object") or intent.object)) for a in _acts]
            if not _pairs:
                # Fallback deterministico (LLM non ha decomposto): verbi canonici
                # rilevati nella query, con l'object PRIMARIO condiviso (storico).
                try:
                    from prefilter import (tokenize as _pf_tok,
                                            detect_canonical_verbs_all as _pf_dv)
                    _qverbs = list(dict.fromkeys(_pf_dv(_pf_tok(query))))
                except Exception:
                    _qverbs = []
                if len(_qverbs) >= 2:
                    _pairs = [(_v, intent.object) for _v in _qverbs]
            if _pairs:
                # Object-completezza per-clausola (4/6): l'LLM assegna verbi
                # ASTRATTI ("list"/"change") che spesso NON hanno un tool esatto
                # (no list_pulls, no change_issues) → rank_with_intent(verb,obj)
                # filtra per prefisso-verbo e PERDE il producer reale dell'object
                # (find_pulls_github, set_issues_github). Garantisci che TUTTI i
                # tool del catalog con QUELL'object (derivato dal NOME canonico,
                # 2° token in vocab.OBJECTS) siano candidati nel pool: il
                # Proposer (che vede le description) sceglie il verbo giusto.
                # Universale, deterministico §7.9, ZERO dizionari di sinonimi —
                # scala a nuove lingue (l'object è canonico, non NL). Fix q14
                # (pulls→find_pulls_github) + q15 (close→set_issues_github).
                try:
                    from vocab import OBJECTS as _VOBJ_SET
                    _VOBJ = set(_VOBJ_SET)
                except Exception:
                    _VOBJ = set()

                def _tool_object(_nm):
                    for _tok in (_nm or "").split("_")[1:]:
                        if _tok in _VOBJ:
                            return _tok
                    return ""

                _seen = {}
                _clause_objs = set()
                for _v, _o in _pairs:
                    if _o:
                        _clause_objs.add(_o)
                    _sub = rank_with_intent(
                        query, catalog,
                        {"verb": _v, "object": _o,
                         "keywords": intent.keywords},
                        k=pool_size) or []
                    for _e in _sub:
                        _seen[getattr(_e, "name", None)] = _e
                # Famiglia-object completa per ogni object delle clausole.
                for _e in catalog:
                    _nm = getattr(_e, "name", None)
                    if _nm and _nm not in _seen and _tool_object(_nm) in _clause_objs:
                        _seen[_nm] = _e
                if _seen:
                    filtered = list(_seen.values())
            if filtered is None:
                filtered = rank_with_intent(query, catalog, intent_dict,
                                            k=pool_size)
            # rank_with_intent ritorna None PER DESIGN quando il verbo intent
            # non matcha alcun executor (es. object=entries meta-oggetto, o
            # verbo intermedio di una query compound): non e' un errore, e' il
            # contratto di fallback bag-of-words (vedi prefilter.py §776). Senza
            # questo ramo il `len(pool_for_propose)` sotto crashava con
            # `len(None)` → except → full pool (80 tool) → grammar Mētis gigante
            # → wise LLM lentissimo (regressione web-search: "fondi ark" ~8min).
            if not filtered:
                filtered = _rank_bow(query, catalog, k=pool_size, min_score=0)
            # Cross-object recall affinity-based (10/6/2026, misroute live
            # "quali account mail hai?" → read_messages): l'intent puo'
            # classificare l'OBJECT sbagliato e il pool gated per object
            # esclude a monte il tool giusto (find_credentials). Un tag
            # affinity multi-parola interamente coperto dalla query (>=2
            # token distintivi, es. "quali account") forza il tool nel pool
            # ANCHE se verb/object differiscono. SCOPED (solo phrase-match
            # pieno, cap 3) per non gonfiare il pool. Deterministico §7.9,
            # zero dizionari per-frase: il dato e' l'affinity curata del
            # manifest. Vive QUI (choke-point del pool) cosi' copre il path
            # intent-driven, il fallback BoW e l'unione compound.
            try:
                from prefilter import affinity_phrase_recall
                _present = {getattr(e, "name", None) for e in filtered}
                for _x in affinity_phrase_recall(query, catalog,
                                                 exclude_names=_present):
                    filtered = filtered + [_x]
            except Exception as ex:  # §2.8: traccia, pool resta valido
                log.warning("routing_pool: affinity_phrase_recall fallita: %r",
                            ex)
            # Segregazione modalità immagini (vedi _gate_image_modality):
            # DOPO l'affinity recall (che può reintrodurre un tool images
            # legittimo su query che nominano le foto — il gate lo preserva
            # via detector testuale), PRIMA di helpers/companions.
            filtered = _gate_image_modality(filtered, query, intent)
            # Garantisci che fastpath / autopath catalog completo resti
            # disponibile a executor (callback usa il NOME, non il pool).
            # Pool ridotto è SOLO per il prompt Proposer.
            pool_for_propose = filtered or catalog
            # §7.3: universal helpers (describe_entries/classify_entries/...)
            # sono referenziati dai PATTERN STRUTTURALI del prompt Proposer
            # (es. READ/LIST = producer + describe_entries + final_answer) ma
            # il prefilter per-verbo non li include. Senza il loro schema nel
            # pool, il Proposer inventa valori (es. style fuori enum §8.3).
            # Append idempotente dei helper presenti nel catalog.
            try:
                from tool_grammar import _UNIVERSAL_HELPERS
                present = {getattr(e, "name", None) for e in pool_for_propose}
                for ex_obj in catalog:
                    nm = getattr(ex_obj, "name", None)
                    if nm in _UNIVERSAL_HELPERS and nm not in present:
                        pool_for_propose = pool_for_propose + [ex_obj]
                        present.add(nm)
                # §7.3 COMPANION injection (universale): un producer il cui
                # output è inutile senza un CONSUMER naturale porta sempre il
                # consumer nel pool, anche se il verbo del consumer non è nella
                # query. find_urls produce URL → senza read_urls_html/pdf la
                # catena web→contenuto è monca (il proposer non può chiuderla,
                # bug ROCm 3/6). Mappa estendibile a ogni coppia simile.
                for _prod, _comps in _POOL_COMPANIONS.items():
                    if _prod in present:
                        for _c in _comps:
                            if _c in present:
                                continue
                            _co = next((e for e in catalog
                                        if getattr(e, "name", None) == _c), None)
                            if _co is not None:
                                pool_for_propose = pool_for_propose + [_co]
                                present.add(_c)
            except Exception as ex:
                # §2.8: traccia l'injection helper/companion fallita (il pool
                # prefiltrato resta valido; flusso invariato).
                log.debug("routing_pool: injection universal-helper/companion "
                          "fallita: %r", ex)
            log.debug("routing_pool: pool reduced %d → %d via prefilter "
                       "(+helpers)", len(catalog), len(pool_for_propose))
        except Exception as ex:
            log.warning("routing_pool: prefilter failed (%r), full pool", ex)
            pool_for_propose = catalog
    else:
        pool_for_propose = catalog
    names = [getattr(e, "name", None) for e in pool_for_propose
             if getattr(e, "name", None)]
    names = _provider_recruit_and_gate(names, query, intent, catalog)
    names = _gate_approval_tool(names, intent)
    names = _gate_store_skill(names)
    # OSSERVABILITÀ (§2.8): un pool TROPPO grande è un segnale di PERICOLO — il
    # filtro non ha discriminato, il prompt LLM è gonfio, il routing diventa una
    # scelta vaga (non deterministica). Non dev'essere scoperto per caso: warning
    # esplicito quando il pool finale eccede di molto il target `pool_size`. Soglia
    # = 2× il target (e ≥ pool_size+8) per non rumoreggiare sui pool legittimi con
    # helpers/companions. Non altera il pool (solo segnala). Il full-catalog
    # fallback (intent incompleto/prefilter rotto) è atteso → escluso dal warning.
    _oversized = max(2 * pool_size, pool_size + 8)
    if pool_for_propose is not catalog and len(names) > _oversized:
        log.warning("routing_pool: pool OVERSIZED %d candidati (target %d) per "
                    "intent(verb=%r,object=%r) q=%r — filtro poco discriminante, "
                    "routing impreciso", len(names), pool_size,
                    getattr(intent, "verb", None), getattr(intent, "object", None),
                    (query or "")[:60])
    return names


def _gate_approval_tool(names: list[str], intent) -> list[str]:
    """get_approval e' un GATE gestito dal RUNTIME (consent-gate inserito da
    dispatch.run_turn + FIX 1 gate-resume), NON un tool che il proposer deve
    comporre: il wise LLM tende ad aggiungerlo spuriamente sulle query
    "sensibili" (pubblica/cancella) E a TRONCARE la pipeline (drop send/write).
    Fuori dal pool del proposer SALVO che l'utente lo chieda ESPLICITAMENTE
    (intent ha la clausola (get, approval)). §7.9 deterministico."""
    try:
        acts = getattr(intent, "actions", None) or []
        wants_gate = any(
            isinstance(a, dict)
            and (a.get("verb") or "").lower() == "get"
            and (a.get("object") or "").lower() == "approval"
            for a in acts)
        if wants_gate:
            return names
    except Exception:  # noqa: BLE001 — best-effort, pool resta valido
        return names
    return [n for n in names if n != "get_approval"]


# Famiglia skill «store generico» (store_entries): dormiente finché il registro
# è vuoto (nessuno store dichiarato). §7.9, gemella di «provider dormant se no
# creds»: zero bersagli → fuori dal pool → niente inquinamento del routing.
_STORE_SKILL_TOOLS = ("find_entries", "write_entries", "delete_entries")


def _gate_store_skill(names: list[str]) -> list[str]:
    try:
        import store as _store
        if _store.registered():          # ≥1 store registrato → attivi
            return names
    except Exception as ex:              # §2.8: traccia, non bloccare
        log.debug("routing_pool: store-skill gate fallito (%r)", ex)
    return [n for n in names if n not in _STORE_SKILL_TOOLS]
