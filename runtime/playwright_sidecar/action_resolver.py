# SPDX-License-Identifier: AGPL-3.0-only
"""Risoluzione deterministica delle azioni web del dominio ``sites``.

Il planner fornisce linguaggio naturale; questo modulo lo riduce a un
vocabolario chiuso e seleziona solo elementi enumerati dal broker. Nessun
selettore proveniente dall'LLM attraversa il confine di sicurezza.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse

try:
    import detection_lexicon as _detlex
except ImportError:  # pragma: no cover - sidecar install incompleto
    _detlex = None

_VERBS_FALLBACK = {
    "goto": ("vai", "naviga", "apri", "visita", "go", "navigate", "open", "visit"),
    "click": ("clicca", "premi", "seleziona", "scegli", "click", "press", "select", "choose"),
    "fill": ("compila", "scrivi", "inserisci", "digita", "fill", "write", "enter", "type"),
    "submit": ("invia", "conferma", "salva", "pubblica", "submit", "confirm", "save", "publish"),
    "wait": ("attendi", "aspetta", "wait", "pause"),
}

_OVERLAY_DISMISS_FALLBACK = (
    "close", "close dialog", "close modal", "dismiss", "dismiss dialog",
    "not now", "maybe later", "later", "got it", "understood", "okay",
    "ok", "cancel", "chiudi", "chiudi dialogo", "chiudi finestra", "ignora",
    "non ora", "non adesso", "forse dopo", "piu tardi", "ho capito",
    "capito", "va bene", "annulla",
)


def _verbs() -> dict[str, tuple[str, ...]]:
    if _detlex is not None:
        try:
            mapped = _detlex.mapping("sites.action_verb")
            if all(mapped.get(k) for k in _VERBS_FALLBACK):
                return {k: tuple(mapped[k]) for k in _VERBS_FALLBACK}
        except Exception:
            pass
    return _VERBS_FALLBACK


def normalize(text: str) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(c for c in raw if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", raw))


def _target_noise() -> tuple[str, ...]:
    if _detlex is not None:
        try:
            forms = tuple(normalize(x) for x in _detlex.forms(
                "sites.action_target_noise") if normalize(x))
            if forms:
                return forms
        except Exception:
            pass
    return ("il", "lo", "la", "un", "una", "sul", "sulla", "pulsante",
            "bottone", "the", "a", "an", "on", "button", "link")


def _concept_forms(concept: str) -> tuple[str, ...]:
    if _detlex is None:
        return ()
    try:
        return tuple(normalize(x) for x in _detlex.forms(concept)
                     if normalize(x))
    except Exception:
        return ()


def overlay_dismiss_forms() -> tuple[str, ...]:
    """Safe, non-committing exits for obstructing transient overlays."""
    forms = (_concept_forms("sites.overlay_dismiss_target")
             + _concept_forms("sites.overlay_acknowledge_target"))
    return forms or _OVERLAY_DISMISS_FALLBACK


def privacy_reject_forms() -> tuple[str, ...]:
    """Translated controls that decline optional privacy processing."""
    return (_concept_forms("sites.privacy_reject_target")
            + _concept_forms("sites.privacy_reject_noun_target"))


def privacy_overlay_marker_forms() -> tuple[str, ...]:
    """Translated evidence that a fixed panel is a privacy overlay."""
    return _concept_forms("sites.privacy_overlay_marker")


def loading_marker_forms() -> tuple[str, ...]:
    """Testi brevi che indicano contenuto asincrono non ancora stabile."""
    return _concept_forms("sites.loading_marker")


def is_collection_search_request(text: str) -> bool:
    """Riconosce una richiesta enumerativa tramite il lessico traducibile."""
    if _detlex is None:
        return False
    try:
        return bool(_detlex.match("sites.collection_search_request", text or ""))
    except Exception:
        return False


def normalize_target(text: str) -> str:
    target = normalize(text)
    for phrase in sorted(_target_noise(), key=len, reverse=True):
        target = re.sub(rf"\b{re.escape(phrase)}\b", " ", target)
    return " ".join(target.split())


def _primary_action_clause(action: str) -> str:
    """Select one bounded goal clause without turning policy text into a target."""
    raw = str(action or "").strip()
    clauses = [part.strip() for part in re.split(
        r"(?:[\r\n]+|(?<=[.!?;])\s+)", raw) if part.strip()]
    if len(clauses) <= 1:
        return raw

    search_forms = _concept_forms("sites.search_action_verb")
    for clause in clauses:
        normalized = normalize(clause)
        if any(re.search(rf"\b{re.escape(form)}\b", normalized)
               for form in search_forms):
            return clause

    action_forms = tuple(
        normalize(form) for forms in _verbs().values() for form in forms)
    for clause in clauses:
        normalized = normalize(clause)
        if (re.search(r"https?://", clause, re.IGNORECASE)
                or any(re.search(rf"\b{re.escape(form)}\b", normalized)
                       for form in action_forms)):
            return clause
    return clauses[0]


def is_goal_navigation_request(action: str) -> bool:
    """Return whether natural language asks to *reach* page content.

    Search is intrinsically goal-oriented.  A navigation verb without an
    explicit URL is also a bounded semantic goal (for example ``vai alle mie
    prenotazioni`` or ``apri il menu account``), whereas an explicit click,
    fill, submit or wait remains an atomic command.  Forms come from the
    detection lexicon through the same closed verb map used by ``parse_action``.
    """
    clause = _primary_action_clause(action)
    if re.search(r"https?://[^\s'\"<>]+", clause or "", re.I):
        return False
    normalized = normalize(clause)
    if not normalized:
        return False

    search_forms = _concept_forms("sites.search_action_verb")
    if any(re.search(rf"\b{re.escape(form)}\b", normalized)
           for form in search_forms):
        return True

    verbs = _verbs()
    # Match parse_action's precedence: an explicit atomic verb wins even if a
    # later word also happens to be a navigation verb.
    for kind in ("submit", "wait", "fill", "click"):
        if any(re.search(rf"\b{re.escape(normalize(form))}\b", normalized)
               for form in verbs[kind]):
            return False
    return any(re.search(rf"\b{re.escape(normalize(form))}\b", normalized)
               for form in verbs["goto"])


def parse_action(action: str) -> dict:
    """Riduce una frase a ``{primitive,target,seconds}``.

    ``submit`` ha precedenza: "compila e invia" e' un'unica azione batch e il
    riempimento eventuale avviene dopo il gate, immediatamente prima del submit.
    """
    action = _primary_action_clause(action)
    norm = normalize(action)
    if not norm:
        return {"ok": False, "error_class": "invalid_args"}
    verbs = _verbs()
    url_match = re.search(r"https?://[^\s'\"<>]+", action or "", re.I)
    primitive = None
    search_verbs = _concept_forms("sites.search_action_verb")
    if any(re.search(rf"\b{re.escape(form)}\b", norm)
           for form in search_verbs):
        primitive = "search"
    order = ("submit", "wait", "fill", "click", "goto")
    for kind in order if primitive is None else ():
        if any(re.search(rf"\b{re.escape(normalize(v))}\b", norm)
               for v in verbs[kind]):
            primitive = kind
            break
    # Un URL esplicito rende la destinazione non ambigua. Vale anche per
    # "apri/open URL", che senza URL indica invece un elemento della pagina.
    if url_match and primitive not in ("submit", "wait", "fill", "search"):
        primitive = "goto"
    elif primitive == "goto" and not url_match:
        primitive = "click"
    if primitive is None:
        return {"ok": False, "error_class": "unsupported_action"}
    target = norm
    if primitive == "goto":
        if url_match:
            target = url_match.group(0).rstrip(".,;)")
    if primitive != "goto" or not target.startswith(("http://", "https://")):
        removable = list(verbs.values())
        if search_verbs:
            removable.append(search_verbs)
        for words in removable:
            for word in words:
                target = re.sub(rf"\b{re.escape(word)}\b", " ", target)
        target = " ".join(target.split())
        target = normalize_target(target)
    seconds = 0
    if primitive == "wait":
        m = re.search(r"\b(\d{1,3})\b", norm)
        seconds = min(20, max(1, int(m.group(1)))) if m else 2
    return {"ok": True, "primitive": primitive, "target": target,
            "seconds": seconds, "normalized": norm}


def _candidate_text(candidate: dict) -> str:
    return normalize(" ".join(str(candidate.get(k) or "") for k in (
        "name", "label", "role", "tag", "type", "placeholder")))


def active_goal_control_label(candidate: dict) -> str:
    """Return a label only for a control whose active state is DOM-attested."""
    state = any((
        str(candidate.get("aria_selected") or "").lower() == "true",
        str(candidate.get("aria_pressed") or "").lower() == "true",
        str(candidate.get("aria_checked") or "").lower() == "true",
        str(candidate.get("aria_expanded") or "").lower() == "true",
        bool(candidate.get("checked")),
        str(candidate.get("aria_current") or "").lower() in {
            "true", "page", "step", "location", "date", "time"},
    ))
    if not state or candidate.get("disabled"):
        return ""
    return str(candidate.get("name") or candidate.get("label") or "").strip()


def _is_semantic_control(candidate: dict) -> bool:
    tag = str(candidate.get("tag") or "").lower()
    role = str(candidate.get("role") or "").lower()
    return tag in ("a", "button", "summary") or role in (
        "button", "link", "tab", "menuitem")


def _target_variants(target: str) -> tuple[str, ...]:
    """Espande un target naturale solo quando coincide con un concetto noto.

    L'espansione serve agli executor intelligenti drop-in: il broker puo'
    cercare il concetto ``login`` e riconoscere ``Accedi``/``Sign in`` senza
    imporre quelle forme al planner o codificarle nel motore browser.
    """
    target_n = normalize_target(target)
    variants = [target_n] if target_n else []
    semantic_targets = {
        "login": "sites.login_entry_target",
        "privacy reject": "sites.privacy_reject_target",
        "login continue": "sites.login_continue_target",
        "site search": "sites.search_entry_target",
    }
    for canonical, concept in semantic_targets.items():
        source_forms = (privacy_reject_forms()
                        if canonical == "privacy reject"
                        else _concept_forms(concept))
        forms = [normalize_target(form) for form in source_forms]
        if target_n and (target_n == canonical or target_n in forms):
            variants.extend(form for form in forms if form)
    return tuple(dict.fromkeys(variants))


def _contains_phrase(haystack: str, needle: str) -> bool:
    return bool(needle and re.search(
        rf"(?:^|\s){re.escape(needle)}(?:\s|$)", haystack))


def _candidate_score_single(target: str, candidate: dict,
                            primitive: str) -> float:
    if (candidate.get("disabled") or candidate.get("visible") is False
            or candidate.get("in_viewport") is False
            or candidate.get("topmost") is False):
        return 0.0
    tag = str(candidate.get("tag") or "").lower()
    typ = str(candidate.get("type") or "").lower()
    editable = tag in ("input", "textarea", "select") or candidate.get("editable")
    if primitive == "fill" and not editable:
        return 0.0
    if primitive == "submit" and not (typ == "submit" or candidate.get("form_action")):
        return 0.0
    hay = _candidate_text(candidate)
    target_n = normalize(target)
    if not hay:
        return 0.0
    score = 0.0
    name = normalize(str(candidate.get("name") or candidate.get("label") or ""))
    if target_n and target_n == name:
        score = 1.0
    elif target_n and (_contains_phrase(hay, target_n)
                       or _contains_phrase(target_n, name)):
        score = 0.88
    else:
        wanted = set(target_n.split())
        present = set(hay.split())
        if wanted:
            score = 0.75 * len(wanted & present) / len(wanted)
    if primitive == "submit" and typ == "submit":
        score = max(score, 0.72)
    if score > 0 and primitive == "click":
        # Un framework puo' enumerare sia il vero link/button sia un wrapper
        # grafico con cursor:pointer e lo stesso testo. Il controllo HTML/ARIA
        # ha semantica browser-owned; il wrapper resta soltanto un fallback.
        if _is_semantic_control(candidate):
            if tag in ("button", "a"):
                score += 0.05
        else:
            score *= 0.78
    return min(score, 1.0)


def candidate_score(target: str, candidate: dict, primitive: str) -> float:
    variants = _target_variants(target) or (normalize_target(target),)
    return max((_candidate_score_single(variant, candidate, primitive)
                for variant in variants), default=0.0)


def _candidate_has_unique_exact_name(target: str, candidate: dict) -> bool:
    name = normalize(str(
        candidate.get("name") or candidate.get("label") or ""))
    return bool(name and any(
        name == normalize(variant) for variant in _target_variants(target)))


def _is_login_target(target: str) -> bool:
    target_n = normalize_target(target)
    forms = {normalize_target(form) for form in
             _concept_forms("sites.login_entry_target")}
    return bool(target_n and (target_n == "login" or target_n in forms))


def _candidate_matches_concept(candidate: dict, concept: str) -> bool:
    text = _candidate_text(candidate)
    return bool(text and any(
        _contains_phrase(text, form) for form in _concept_forms(concept)))


def _safe_navigation_identity(candidate: dict) -> tuple[str, str, str] | None:
    href = str(candidate.get("href") or "")
    try:
        parsed = urllib.parse.urlsplit(href)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return (parsed.scheme.lower(), parsed.hostname.lower(), parsed.path or "/")


def _model_eligible(candidate: dict, primitive: str) -> bool:
    if (candidate.get("disabled") or candidate.get("visible") is False
            or candidate.get("in_viewport") is False
            or candidate.get("topmost") is False):
        return False
    tag = str(candidate.get("tag") or "").lower()
    typ = str(candidate.get("type") or "").lower()
    editable = tag in ("input", "textarea", "select") or candidate.get(
        "editable")
    if primitive == "fill":
        return bool(editable)
    if primitive == "submit":
        return typ == "submit" or bool(candidate.get("form_action"))
    if primitive == "click" and editable and typ not in (
            "button", "submit", "image", "checkbox", "radio"):
        return False
    return True


def choose_candidate(target: str, candidates: list[dict], primitive: str) -> dict:
    all_ranked = sorted(
        ((candidate_score(target, c, primitive), c) for c in candidates),
        key=lambda x: x[0], reverse=True)
    ranked = [(s, c) for s, c in all_ranked if s > 0]
    # Se il testo accessibile non esprime il concetto (icone, UI custom,
    # etichette inattese), il VLM puo' ancora scegliere fra elementi gia'
    # enumerati. Lo score zero non viene mai eseguito deterministicamente.
    model_ranked = ranked or [
        (s, c) for s, c in all_ranked if _model_eligible(c, primitive)]
    if not ranked or ranked[0][0] < 0.55:
        return {"ok": False, "error_class": "selector_missing",
                "ranked": model_ranked[:24]}
    if primitive == "click" and _is_login_target(target):
        # Un controllo diretto di autenticazione (es. "Accedi"/"Sign in")
        # e' semanticamente piu' specifico di un reveal generico come
        # "Account". Le forme arrivano dal detection_lexicon: nessun label o
        # sito e' codificato nel resolver. Se non esiste un diretto, conserva
        # integralmente il fallback precedente sui reveal.
        direct = [
            (score, candidate) for score, candidate in ranked
            if _candidate_matches_concept(
                candidate, "sites.login_direct_target")]
        if direct and direct[0][0] >= 0.55:
            ranked = direct
        # Un href HTTP(S) e' verificabile dal broker prima del click; un
        # controllo JavaScript opaco no. Se esistono link login validi, limita
        # l'ambiguita' a questi senza inventare destinazioni o selettori.
        navigable = [(score, candidate) for score, candidate in ranked
                     if _safe_navigation_identity(candidate) is not None]
        if navigable and navigable[0][0] >= 0.55:
            ranked = navigable
    top_score, top = ranked[0]
    margin = top_score - (ranked[1][0] if len(ranked) > 1 else 0.0)
    ambiguous = margin < 0.12
    if ambiguous and _candidate_has_unique_exact_name(target, top):
        close_exact = [
            candidate for score, candidate in ranked[1:]
            if top_score - score < 0.12
            and _candidate_has_unique_exact_name(target, candidate)
        ]
        if not close_exact:
            ambiguous = False
    if ambiguous and primitive == "click" and _is_login_target(target):
        close = [candidate for score, candidate in ranked
                 if top_score - score < 0.12]
        destinations = {_safe_navigation_identity(candidate)
                        for candidate in close}
        if len(destinations) == 1 and None not in destinations:
            ambiguous = False
    return {"ok": True, "candidate": top, "confidence": top_score,
            "ambiguous": ambiguous, "ranked": ranked[:5]}


def _goal_noise() -> set[str]:
    return (set(_concept_forms("sites.goal_noise"))
            | set(_concept_forms("sites.goal_noise_articulated_preposition"))
            | set(_concept_forms("sites.goal_scope_quantifier"))
            | set(_concept_forms("sites.personal_goal_marker")))


def _canonical_goal_text(text: str) -> str:
    normalized = normalize_target(text)
    if _detlex is None:
        return normalized
    for concept in ("sites.goal_term_alias", "sites.goal_state_alias"):
        try:
            aliases = _detlex.mapping(concept)
        except Exception:
            continue
        for canonical, forms in aliases.items():
            for form in sorted(
                    (normalize(x) for x in forms), key=len, reverse=True):
                if form:
                    normalized = re.sub(
                        rf"\b{re.escape(form)}\b",
                        normalize(canonical), normalized)
    return " ".join(normalized.split())


def _is_personal_goal(target: str) -> bool:
    normalized = normalize(target)
    return any(re.search(rf"\b{re.escape(form)}\b", normalized)
               for form in _concept_forms("sites.personal_goal_marker"))


def goal_is_exhaustive(target: str) -> bool:
    normalized = normalize_target(target)
    return any(_contains_phrase(normalized, form) for form in
               _concept_forms("sites.goal_scope_quantifier"))


def preserve_goal_qualifiers(query: str, goal: str, *,
                             max_words: int = 6) -> str:
    """Restore navigation semantics that a goal reducer may discard.

    Ownership and exhaustive-scope markers do not contribute content tokens,
    but they alter how the resolver reaches that content (for example through
    a personal-area reveal or through continuation controls).  Every restored
    phrase is copied from a translated detection concept that is actually
    present in the original query; the model cannot invent it.
    """
    query_n = normalize(query)
    goal_n = normalize(goal)
    if not query_n or not goal_n:
        return ""

    matches: list[tuple[int, str]] = []
    for concept in ("sites.goal_scope_quantifier",
                    "sites.personal_goal_marker"):
        forms = tuple(dict.fromkeys(_concept_forms(concept)))
        if any(_contains_phrase(goal_n, form) for form in forms):
            continue
        found: list[tuple[int, int, str]] = []
        for form in forms:
            match = re.search(
                rf"(?:^|\s)({re.escape(form)})(?=\s|$)", query_n)
            if match:
                found.append((match.start(1), -len(form.split()), form))
        if found:
            start, _neg_words, form = min(found)
            matches.append((start, form))

    qualifiers = [form for _start, form in sorted(matches)]
    restored = " ".join((*qualifiers, goal_n)).strip()
    if not restored or len(restored.split()) > max(1, int(max_words)):
        return ""
    return restored


def goal_tokens(target: str, *, navigation: bool = False) -> tuple[str, ...]:
    noise = _goal_noise()
    out = []
    for token in _canonical_goal_text(target).split():
        if token in noise or len(token) < 2:
            continue
        if navigation and token.isdigit():
            continue  # date/importi sono filtri differiti, non nomi di menu
        out.append(token)
    return tuple(dict.fromkeys(out))


def goal_candidate_key(candidate: dict) -> str:
    stable = "\0".join(str(candidate.get(k) or "") for k in (
        "tag", "role", "name", "label", "href", "form_action"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _is_root_navigation(candidate: dict) -> bool:
    href = str(candidate.get("href") or "").strip()
    if not href:
        return False
    try:
        parsed = urllib.parse.urlsplit(href)
    except ValueError:
        return False
    return (parsed.scheme.lower() in {"http", "https"}
            and (parsed.path or "/") == "/")


def goal_candidate_is_admissible(target: str, candidate: dict) -> bool:
    """Apply goal-level safety constraints to deterministic and model paths."""
    return not (_is_personal_goal(target) and _is_root_navigation(candidate))


def goal_navigation_candidates(candidates: list[dict], *,
                               excluded: set[str] | None = None) -> list[dict]:
    excluded = excluded or set()
    out = []
    for candidate in candidates:
        tag = str(candidate.get("tag") or "").lower()
        typ = str(candidate.get("type") or "").lower()
        role = str(candidate.get("role") or "").lower()
        if (candidate.get("disabled") or candidate.get("visible") is False
                or candidate.get("in_viewport") is False
                or candidate.get("topmost") is False
                or candidate.get("secret_input") or candidate.get("download")
                or typ == "password"
                or (typ == "submit" and candidate.get("form_action"))
                or tag in ("input", "textarea", "select")
                or role in ("textbox", "searchbox")):
            continue
        if goal_candidate_key(candidate) in excluded:
            continue
        if not normalize(str(candidate.get("name") or candidate.get("label") or "")):
            continue
        out.append(candidate)
    return out


def prefer_verifiable_goal_candidates(candidates: list[dict]) -> list[dict]:
    """Collapse same-name wrappers onto a unique broker-verifiable link."""
    groups: dict[str, list[dict]] = {}
    for candidate in candidates:
        name = normalize(str(
            candidate.get("name") or candidate.get("label") or ""))
        groups.setdefault(name, []).append(candidate)
    out = []
    for group in groups.values():
        navigable = [candidate for candidate in group
                     if _safe_navigation_identity(candidate) is not None]
        destinations = {
            _safe_navigation_identity(candidate) for candidate in navigable}
        if navigable and len(destinations) == 1:
            out.append(min(navigable, key=lambda candidate: (
                str(candidate.get("tag") or "").lower() != "a",
                str(candidate.get("id") or ""))))
        else:
            out.extend(group)
    return out


def choose_authenticated_reveal_candidate(
        candidates: list[dict], *, excluded: set[str] | None = None) -> dict:
    """Choose one closed disclosure before failing an authenticated goal.

    Account areas often expose only the user's name/avatar while the desired
    links live in a collapsed menu.  The decision uses browser-owned ARIA/DOM
    state, never a vendor label: a unique closed semantic disclosure is safe
    to reveal and then rescan.  Multiple plausible disclosures remain an
    explicit ambiguity.
    """
    eligible = []
    for candidate in goal_navigation_candidates(
            candidates, excluded=excluded):
        if not _is_semantic_control(candidate):
            continue
        closed = str(candidate.get("aria_expanded") or "").lower() == "false"
        controlled = bool(candidate.get("control_targets"))
        if not (closed or controlled):
            continue
        eligible.append(candidate)
    if not eligible:
        return {"ok": False, "error_class": "selector_missing", "ranked": []}

    account = [candidate for candidate in eligible
               if _candidate_matches_concept(
                   candidate, "sites.account_reveal_control")]
    pool = account or eligible
    if len(pool) != 1:
        return {"ok": False, "error_class": "selector_ambiguous",
                "ranked": [(0.62, candidate) for candidate in pool[:24]]}
    return {"ok": True, "candidate": pool[0], "confidence": 0.62,
            "ranked": [(0.62, pool[0])]}


def choose_goal_candidate(target: str, candidates: list[dict], *,
                          excluded: set[str] | None = None) -> dict:
    """Sceglie un passo che copre una parte semantica del fine.

    I numeri restano vincoli terminali (anno/importo) e non penalizzano il nome
    di un menu. Il margine rende ambiguita' e collisioni un fallimento chiuso.
    """
    wanted = set(goal_tokens(target, navigation=True))
    personal_goal = _is_personal_goal(target)
    account_forms = _concept_forms("sites.account_reveal_control")
    ranked = []
    eligible = prefer_verifiable_goal_candidates(
        goal_navigation_candidates(candidates, excluded=excluded))
    for candidate in eligible:
        if not goal_candidate_is_admissible(target, candidate):
            continue
        present = set(goal_tokens(str(
            candidate.get("name") or candidate.get("label") or ""),
            navigation=True))
        overlap = wanted & present
        name = normalize(str(candidate.get("name") or candidate.get("label") or ""))
        account_reveal = (personal_goal and any(
            re.search(rf"\b{re.escape(form)}\b", name)
            for form in account_forms))
        if overlap:
            coverage = len(overlap) / len(wanted)
            focus = len(overlap) / max(1, len(present))
            score = min(1.0, 0.72 * coverage + 0.28 * focus)
        elif account_reveal:
            score = 0.62
        else:
            continue
        if not _is_semantic_control(candidate):
            score *= 0.72
        ranked.append((score, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 0.55:
        return {"ok": False, "error_class": "selector_missing",
                "ranked": ranked[:24]}
    top_score, top = ranked[0]
    margin = top_score - (ranked[1][0] if len(ranked) > 1 else 0.0)
    if margin < 0.12:
        equivalent = [candidate for score, candidate in ranked
                      if top_score - score < 0.12]
        keys = {goal_candidate_key(candidate) for candidate in equivalent}
        destinations = {_safe_navigation_identity(candidate)
                        for candidate in equivalent}
        canonical_names = {tuple(goal_tokens(str(
            candidate.get("name") or candidate.get("label") or ""),
            navigation=True)) for candidate in equivalent}
        # Framework menus commonly expose both an ARIA ``menuitem`` wrapper
        # and the nested HTTP link with the same accessible name.  The wrapper
        # may consume a click without navigating (Booking regression 16/7),
        # while the link has a broker-verifiable destination.  Prefer the
        # unique navigable equivalent; never invent a URL and keep different
        # labels or destinations ambiguous.
        navigable = [candidate for candidate in equivalent
                     if _safe_navigation_identity(candidate) is not None]
        navigable_destinations = {
            _safe_navigation_identity(candidate) for candidate in navigable}
        if (len(canonical_names) == 1 and len(navigable_destinations) == 1
                and navigable):
            top = min(navigable, key=lambda candidate: (
                str(candidate.get("tag") or "").lower() != "a",
                str(candidate.get("id") or "")))
            return {"ok": True, "candidate": top,
                    "confidence": top_score, "ranked": ranked[:24]}
        safe_destination = len(destinations) == 1 and None not in destinations
        if safe_destination and (len(keys) == 1 or len(canonical_names) == 1):
            top = min(equivalent, key=lambda candidate: (
                not _is_semantic_control(candidate),
                str(candidate.get("id") or "")))
        else:
            return {"ok": False, "error_class": "selector_ambiguous",
                    "ranked": ranked[:24]}
    return {"ok": True, "candidate": top, "confidence": top_score,
            "ranked": ranked[:24]}


def goal_candidate_is_exact(target: str, candidate: dict) -> bool:
    wanted = set(goal_tokens(target, navigation=True))
    present = set(goal_tokens(str(
        candidate.get("name") or candidate.get("label") or ""),
        navigation=True))
    return bool(wanted) and present == wanted


def choose_goal_continuation_candidate(
        target: str, candidates: list[dict], *,
        excluded: set[str] | None = None) -> dict:
    """Choose a bounded load-more/next control related to an attained goal."""
    forms = tuple(normalize(form) for form in _concept_forms(
        "sites.continuation_target") if normalize(form))
    wanted = set(goal_tokens(target, navigation=True))
    ranked = []
    for candidate in goal_navigation_candidates(
            candidates, excluded=excluded):
        if str(candidate.get("form_method") or "").upper() == "POST":
            continue
        name = normalize(str(
            candidate.get("name") or candidate.get("label") or ""))
        padded_name = f" {name} "
        if not name or not any(f" {form} " in padded_name for form in forms):
            continue
        name_overlap = wanted & set(goal_tokens(name, navigation=True))
        context = normalize(str(candidate.get("context_name") or ""))
        context_overlap = wanted & set(goal_tokens(context, navigation=True))
        if name_overlap:
            score = 0.95
        elif context_overlap:
            score = 0.80
        else:
            score = 0.62
        if not _is_semantic_control(candidate):
            score *= 0.72
        ranked.append((score, candidate,
                       bool(name_overlap or context_overlap)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return {"ok": False, "error_class": "selector_missing"}
    contextual = [item for item in ranked if item[2]]
    pool = contextual or ranked
    if not contextual and len(pool) != 1:
        return {"ok": False, "error_class": "selector_ambiguous"}
    top_score, top, _ = pool[0]
    if len(pool) > 1 and top_score - pool[1][0] < 0.12:
        return {"ok": False, "error_class": "selector_ambiguous"}
    return {"ok": True, "candidate": top, "confidence": top_score,
            "ranked": [(score, candidate) for score, candidate, _ in pool[:12]]}


def choose_search_field(candidates: list[dict]) -> dict:
    forms = set(_concept_forms("sites.search_entry_target"))
    ranked = []
    for candidate in candidates:
        tag = str(candidate.get("tag") or "").lower()
        typ = str(candidate.get("type") or "").lower()
        role = str(candidate.get("role") or "").lower()
        if (candidate.get("disabled") or candidate.get("visible") is False
                or candidate.get("in_viewport") is False
                or candidate.get("topmost") is False
                or candidate.get("secret_input")
                or (tag not in ("input", "textarea")
                    and not candidate.get("editable"))):
            continue
        semantic = max((_candidate_score_single(form, candidate, "fill")
                        for form in forms), default=0.0)
        structural = 1.0 if typ == "search" or role == "searchbox" else 0.0
        score = max(semantic, structural)
        if score:
            ranked.append((score, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return {"ok": False, "error_class": "selector_missing"}
    top_score, top = ranked[0]
    margin = top_score - (ranked[1][0] if len(ranked) > 1 else 0.0)
    if margin < 0.12:
        return {"ok": False, "error_class": "selector_ambiguous",
                "ranked": ranked[:12]}
    return {"ok": True, "candidate": top, "confidence": top_score}


def _offscreen_probe(candidate: dict) -> dict | None:
    if (candidate.get("rendered") is not True
            or candidate.get("in_viewport") is not False
            or candidate.get("disabled")):
        return None
    probe = dict(candidate)
    probe.update({"visible": True, "in_viewport": True, "topmost": True})
    return probe


def choose_scroll_candidate(target: str, candidates: list[dict],
                            primitive: str) -> dict:
    ranked = []
    for candidate in candidates:
        probe = _offscreen_probe(candidate)
        if probe is None:
            continue
        score = candidate_score(target, probe, primitive)
        if score:
            ranked.append((score, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 0.55:
        return {"ok": False, "error_class": "selector_missing"}
    top_score, top = ranked[0]
    margin = top_score - (ranked[1][0] if len(ranked) > 1 else 0.0)
    if margin < 0.12:
        return {"ok": False, "error_class": "selector_ambiguous"}
    return {"ok": True, "candidate": top, "confidence": top_score}


def choose_goal_scroll_candidate(target: str, candidates: list[dict], *,
                                 excluded: set[str] | None = None) -> dict:
    originals = {}
    probes = []
    for candidate in candidates:
        probe = _offscreen_probe(candidate)
        if probe is None:
            continue
        key = goal_candidate_key(probe)
        originals[key] = candidate
        probes.append(probe)
    chosen = choose_goal_candidate(target, probes, excluded=excluded)
    if chosen.get("ok"):
        probe = chosen["candidate"]
        chosen["candidate"] = originals.get(goal_candidate_key(probe), probe)
    return chosen


def choose_goal_continuation_scroll_candidate(
        target: str, candidates: list[dict], *,
        excluded: set[str] | None = None) -> dict:
    originals = {}
    probes = []
    for candidate in candidates:
        probe = _offscreen_probe(candidate)
        if probe is None:
            continue
        key = goal_candidate_key(probe)
        originals[key] = candidate
        probes.append(probe)
    chosen = choose_goal_continuation_candidate(
        target, probes, excluded=excluded)
    if chosen.get("ok"):
        probe = chosen["candidate"]
        chosen["candidate"] = originals.get(goal_candidate_key(probe), probe)
    return chosen


def choose_search_scroll_field(candidates: list[dict]) -> dict:
    originals = {}
    probes = []
    for candidate in candidates:
        probe = _offscreen_probe(candidate)
        if probe is None:
            continue
        originals[str(candidate.get("id") or id(candidate))] = candidate
        probes.append(probe)
    chosen = choose_search_field(probes)
    if chosen.get("ok"):
        probe = chosen["candidate"]
        chosen["candidate"] = originals.get(
            str(probe.get("id") or id(probe)), probe)
    return chosen


# Home/landing di un sito: root, `/index[.htm[l]]` e le varianti LOCALIZZATE
# `/index.<lang>[-<region>].htm[l]` (es. `/index.it.html`, `/index.en-gb.html`).
# Booking redirige la home su `/index.it.html`: senza le localizzate il guard
# home di page_satisfies_goal falliva a scattare e un goal personale a token
# singolo (es. «prenotazioni»→«booking», onnipresente sul sito) risultava
# "gia' raggiunto" sulla home → observe invece di aprire la sezione dedicata.
_LOCALIZED_INDEX_RE = re.compile(r"/index\.[a-z]{2,3}(-[a-z]{2,4})?\.html?")


def _is_home_path(path: str) -> bool:
    if path in ("", "/", "/index", "/index.htm", "/index.html"):
        return True
    return bool(_LOCALIZED_INDEX_RE.fullmatch(path or ""))


def page_satisfies_goal(target: str, body_text: str | list[str], *,
                        scope_text: str = "") -> bool:
    wanted = set(goal_tokens(target))
    if not wanted:
        return False
    if len(wanted) == 1:
        try:
            split = urllib.parse.urlsplit(scope_text)
            path = split.path.lower()
            host_tokens = set(goal_tokens(split.hostname or ""))
        except ValueError:
            path, host_tokens = "", set()
        # La HOME non "soddisfa" un goal a token singolo quando il goal e'
        # personale (le mie X) OPPURE quando il token E' il brand del sito
        # (es. «prenotazioni»→«booking» su booking.com, onnipresente ovunque):
        # la sezione dedicata va aperta. Il riduttore goal LLM puo' spogliare il
        # marker «mie», quindi il guard NON puo' dipenderne (bug reale Booking:
        # target ridotto a «prenotazioni» → matchava il brand sulla home).
        if _is_home_path(path) and (_is_personal_goal(target)
                                    or wanted <= host_tokens):
            return False
    if isinstance(body_text, list):
        blocks = [normalize(str(block)) for block in body_text[:400]]
    else:
        blocks = [normalize(line) for line in str(body_text or "").splitlines()]
    blocks = [block for block in blocks if block]
    # Il goal deve essere attestato da una regione locale della pagina. Un menu
    # "Fatture" e un anno comparso molto piu' sotto non sono un risultato.
    for index in range(len(blocks)):
        region = " ".join(blocks[index:index + 3])[:4000]
        if wanted.issubset(set(goal_tokens(region))):
            return True
    # Prova composita: route/titolo attestano la sezione, il contenuto visibile
    # non-interattivo attesta gli altri vincoli (anno, stato, nome documento).
    # La sola presenza nel body non basta, perche' includerebbe menu e dati non
    # correlati della dashboard.
    scope_tokens = set(goal_tokens(scope_text))
    content_tokens = set(goal_tokens(" ".join(blocks)[:200_000]))
    if (wanted & scope_tokens
            and wanted.issubset(scope_tokens | content_tokens)):
        return True
    return False


def choose_reveal_candidate(target: str, candidates: list[dict],
                            primitive: str) -> dict:
    """Trova un controllo visibile collegato a un target DOM nascosto.

    Sono accettate solo relazioni esplicite browser-owned: l'id del target o
    di un suo antenato deve comparire in ``aria-controls``, ``popoverTarget``,
    ``commandfor`` o in un fragment locale del controllo. Nessuna euristica su
    classi CSS, domini o parole come "menu".
    """
    hidden_ranked = []
    for candidate in candidates:
        if (candidate.get("visible") is not False
                and candidate.get("in_viewport") is not False
                and candidate.get("topmost") is not False):
            continue
        probe = dict(candidate)
        probe.update({"visible": True, "in_viewport": True, "topmost": True})
        score = candidate_score(target, probe, primitive)
        if score > 0:
            hidden_ranked.append((score, candidate))
    hidden_ranked.sort(key=lambda item: item[0], reverse=True)
    if not hidden_ranked or hidden_ranked[0][0] < 0.55:
        return {"ok": False, "error_class": "selector_missing",
                "hidden_target": False}
    top_score = hidden_ranked[0][0]
    related_ids = set()
    for score, candidate in hidden_ranked:
        if top_score - score >= 0.12:
            break
        if candidate.get("dom_id"):
            related_ids.add(str(candidate["dom_id"]))
        related_ids.update(str(x) for x in (candidate.get("ancestor_ids") or [])
                           if x)
    controls = []
    for candidate in candidates:
        if (candidate.get("visible") is False
                or candidate.get("in_viewport") is False
                or candidate.get("topmost") is False
                or candidate.get("disabled")):
            continue
        targets = {str(x) for x in (candidate.get("control_targets") or []) if x}
        overlap = targets & related_ids
        if overlap:
            controls.append((len(overlap),
                             candidate.get("aria_expanded") == "false",
                             candidate))
    controls.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not controls:
        return {"ok": False, "error_class": "selector_hidden",
                "hidden_target": True, "hidden_target_score": top_score}
    best = controls[0]
    if len(controls) > 1 and best[:2] == controls[1][:2]:
        return {"ok": False, "error_class": "selector_ambiguous",
                "hidden_target": True, "hidden_target_score": top_score}
    return {"ok": True, "candidate": best[2], "confidence": 0.9,
            "hidden_target_score": top_score}


def page_mentions_target(target: str, body_text: str) -> bool:
    body_n = normalize((body_text or "")[:200_000])
    variants = _target_variants(target)
    if not variants or not body_n:
        return False
    return any(re.search(rf"(?:^|\s){re.escape(variant)}(?:\s|$)", body_n)
               for variant in variants)


def is_reveal_control(candidate: dict) -> bool:
    tag = str(candidate.get("tag") or "").lower()
    role = str(candidate.get("role") or "").lower()
    typ = str(candidate.get("type") or "").lower()
    if ((tag != "button" and role != "button") or typ == "submit"
            or candidate.get("disabled") or candidate.get("visible") is False
            or candidate.get("in_viewport") is False
            or candidate.get("topmost") is False):
        return False
    name = normalize(str(candidate.get("name") or candidate.get("label") or ""))
    if not name:
        return False
    forms = []
    if _detlex is not None:
        try:
            forms = _detlex.forms("sites.reveal_control")
        except Exception:
            forms = []
    if not forms:
        forms = ["apri menu", "mostra menu", "open menu", "show menu"]
    return any(name == normalize(form) for form in forms if normalize(form))


def is_sensitive(primitive: str, candidate: dict | None, *,
                 tainted: bool, value_ref: str | None = None) -> tuple[bool, list[str]]:
    c = candidate or {}
    reasons = []
    if primitive in ("goto", "submit", "search"):
        reasons.append("navigation_or_submit")
    if c.get("href") or c.get("form_action"):
        reasons.append("navigation")
    if str(c.get("form_method") or "").upper() == "POST":
        reasons.append("post")
    if c.get("download"):
        reasons.append("download")
    if c.get("secret_input"):
        reasons.append("sensitive_input")
    if value_ref and value_ref.startswith("cred:"):
        reasons.append("credential_use")
    # Un elemento senza href/form puo' comunque avere listener JavaScript che
    # inviano dati. Dopo ingestione di contenuto esterno, ogni interazione e'
    # quindi potenzialmente esfiltrante; solo wait resta non sensibile.
    if tainted and primitive not in ("wait", "observe"):
        reasons.append("tainted_turn")
    return bool(reasons), sorted(set(reasons))


def fingerprint_plan(plan: dict) -> str:
    safe = {k: plan.get(k) for k in (
        "kind", "primitive", "target", "candidate_sig", "page_sig",
        "value_ref", "resource_hosts")}
    return hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()
