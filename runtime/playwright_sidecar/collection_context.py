# SPDX-License-Identifier: MIT
"""Select an observed account/category before traversing its records.

No site vocabulary lives here. DOM state identifies selectors; the local
model compares their current values with the user's request. Clicks remain
the caller's responsibility and use the ordinary action gate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata

from playwright_sidecar import login_navigation


def selectors(candidates):
    return [c for c in candidates if (
        c.get("role") == "combobox"
        or c.get("aria_haspopup") in {"listbox", "menu", "true"}
        or (c.get("aria_expanded") in {"true", "false"}
            and c.get("text") and c.get("name")
            and c["text"] != c["name"]))]


def describe(candidate):
    return {k: candidate[k] for k in (
        "id", "role", "name", "text", "context_name", "aria_expanded",
        "aria_selected", "aria_checked", "aria_pressed", "aria_current") if candidate.get(k)}


def fingerprint(controls):
    # Enumeration IDs and geometry can change after a scroll or redraw.
    data = [{k: v for k, v in describe(c).items() if k != "id"} for c in controls]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def options(candidates, controls, opened):
    """Use observed popup membership, or the controls revealed by our click."""
    expanded = [c for c in controls if c.get("aria_expanded") == "true"]
    if not expanded:
        return []
    targets = {t for c in expanded for t in c.get("control_targets", [])}
    before = set(opened.get("before", [])) if opened else None
    selector_ids = {c["id"] for c in controls}
    observed = [c for c in candidates if c.get("id") not in selector_ids and (
        (len(expanded) == 1 and c.get("role") in {"option", "menuitem", "menuitemradio"})
        or targets.intersection([c.get("dom_id"), *c.get("ancestor_ids", [])])
        or (before is not None and login_navigation.candidate_key(c) not in before))]
    return collapse_nested_options(observed)


def collapse_nested_options(candidates):
    """Keep one surface for a menu row and its inert, pointer-styled children.

    Explicit child controls remain separate: they may perform another action.
    Distinct rows are never merged, even when their text is identical.
    """
    by_id = {c.get("id"): c for c in candidates}
    semantic = {"a", "button", "input", "select", "option", "summary"}
    roles = {"option", "menuitem", "menuitemradio", "button", "link",
             "radio", "checkbox"}
    def label(candidate):
        return " ".join(str(candidate.get("name") or candidate.get("text") or "")
                        .casefold().split())
    def redundant(child):
        if child.get("tag") in semantic or child.get("role") in roles:
            return False
        child_label = label(child)
        if not child_label:
            return False
        for ancestor_id in child.get("ancestor_action_ids") or ():
            parent = by_id.get(ancestor_id)
            if parent is None:
                continue
            parent_label = label(parent)
            if parent_label and (child_label in parent_label or parent_label in child_label):
                return True
        return False
    return [c for c in candidates if not redundant(c)]


def navigation_candidates(candidates, *, controls=None):
    excluded = {c["id"] for c in (selectors(candidates) if controls is None else controls)}
    return [c for c in candidates if c["id"] not in excluded and
            c.get("role") not in {"option", "menuitemradio"}]


def rebind_controls(controls, candidates):
    """Rebind scene-local semantic controls by observed identity, never IDs."""
    rebound = []
    for control in controls:
        identity = login_navigation.candidate_key(control)
        matches = [c for c in candidates if login_navigation.candidate_key(c) == identity]
        if len(matches) != 1:
            return None
        rebound.append(matches[0])
    # A newly visible DOM context is mandatory for the next semantic scene.
    # Read-only replay cannot silently omit it or classify it from stale data.
    if any(c not in rebound for c in selectors(candidates)):
        return None
    return rebound


def value_key(controls):
    values = sorted((c.get("name", ""), c.get("text", ""),
                     c.get("aria_selected", ""), c.get("aria_checked", ""),
                     c.get("aria_pressed", ""), c.get("aria_current", "")) for c in controls)
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def explicit_alternatives(request, label):
    """Find alternatives literally present in both label and request.

    A slash separates alternatives in many accessible labels. The first part
    may also contain an instruction (for example, "Select A/B"), so compare
    its final words using the shortest alternative's length. No vocabulary
    or language-specific mapping is needed; synonyms remain for the model.
    """
    parts = label.split("/")
    if len(parts) < 2:
        return set()

    def words(value):
        plain = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(c for c in plain if not unicodedata.combining(c))
        return re.findall(r"\w+", plain)

    variants = [words(part) for part in parts]
    if any(not variant for variant in variants):
        return set()
    width = min(map(len, variants))
    variants = [variant[-width:] for variant in variants]
    request_words = words(request)
    found = {" ".join(re.findall(r"\w+", part)[-width:])
             for part, variant in zip(parts, variants)
             if any(request_words[i:i + width] == variant
                    for i in range(len(request_words) - width + 1))}
    return found


def adopt_verified(state, candidates, controls):
    """Record a context verified against a saved page as ensure() would.

    The caller has rebound the saved controls with their same values; no
    decision or effect happens here. A pending context change is never
    adopted: the full path must settle it.
    """
    if state.get("pending"):
        return None
    if not controls:
        state.pop("ready", None)
        state.pop("value_key", None)
        return {"candidates": navigation_candidates(candidates, controls=controls)}
    choices = options(candidates, controls, state.get("opened"))
    state.update(ready=fingerprint(controls), value_key=value_key(controls))
    if not any(c.get("aria_expanded") == "true" for c in controls):
        state.pop("opened", None)
    state.pop("attempted", None)
    excluded = {c["id"] for c in choices}
    return {"candidates": [c for c in navigation_candidates(candidates, controls=controls)
                           if c["id"] not in excluded]}


async def ensure(state, target, candidates, *, read, execute, settle, budget, enabled,
                 timeout_s=20, decide=None, restore=None):
    """Establish context before navigation frames; every effect spends their budget.

    State survives an approval pause. A ready value is reusable only while the
    observed selector state is identical. A closed menu alone proves nothing.

    A requested context whose effect cannot be verified (the selector is
    gone, unchanged, or the same action is asked again) stops the search,
    unless every record carries that constraint itself (a date): then only
    the attempt ends. Nothing is adopted, the control and what its click
    revealed never become routes, the last verified value keeps naming the
    page, and the caller declares the context unverified.

    A date attempt that ends so first closes what its click opened
    (`restore`: Esc, then a fresh read). When the context controls show again
    what they showed before the click, the page is the one before it and no
    selection of the site changed (real run bq4, ENI 24/10: a calendar left
    open was saved as the list, and a second tab was refused for nothing).
    """
    async def abandoned(phase, controls, choices=(), role=None, key=None, pending=None):
        nonlocal candidates
        if role == "temporal" and pending is not None and restore is not None:
            # Enumeration numbers change with the fresh read: only its
            # controls are used from here on.
            candidates = await restore()
            controls = selectors(candidates)
            choices = options(candidates, controls, None)
            if fingerprint(controls) == pending["fingerprint"]:
                state.pop("opened", None)
                if not pending.get("changed_before"):
                    state.pop("changed", None)
        return unverified(phase, controls, choices, role=role, key=key)

    def unverified(phase, controls, choices=(), role=None, key=None):
        if role != "temporal":
            # Records do not carry an account, category or entity: a later
            # filter on the records cannot enforce what the page did not apply.
            return {"terminal": {"ok": False, "error_class": "target_changed"},
                    "rejection": {"phase": phase}}
        blocked = state.setdefault("unverifiable", set())
        if key is not None:
            blocked.add(key)
        if state.get("opened"):
            before = set(state["opened"].get("before", ()))
            blocked = blocked | {login_navigation.candidate_key(c) for c in candidates
                                 if login_navigation.candidate_key(c) not in before}
        for name in ("pending", "opened", "ready"):
            state.pop(name, None)
        excluded = {c["id"] for c in choices}
        return {"candidates": [c for c in navigation_candidates(candidates, controls=controls)
                               if c["id"] not in excluded
                               and login_navigation.candidate_key(c) not in blocked],
                "unverified": phase}

    while True:
        controls = selectors(candidates)
        choices = options(candidates, controls, state.get("opened"))
        # The shared observer has already verified the whole fresh scene.
        # Consume its classification even with no DOM hints or an unchanged
        # ready fingerprint; semantic kind/request can change independently.
        decision = None
        if decide is not None:
            decision = await decide(controls, choices, candidates)
            controls = decision.pop("controls", controls)
            choices = options(candidates, controls, state.get("opened"))
            if decision.get("error_class"):
                return {"terminal": {"ok": False, **decision},
                        "rejection": {"phase": decision.get("phase", "context_decision"),
                                      "reason": decision.get("reason", "invalid_response"),
                                      "decision_actor": decision.get("_decision_actor", {"actor_type": "unknown"})}}
        if not controls:
            if state.get("pending"):
                return await abandoned("context_selector_missing", controls,
                                       role=state["pending"].get("role"),
                                       key=state["pending"].get("key"), pending=state["pending"])
            state.pop("ready", None)
            state.pop("value_key", None)
            return {"candidates": navigation_candidates(candidates, controls=controls)}
        current = fingerprint(controls)
        pending = state.pop("pending", None)
        if pending:
            if current == pending["fingerprint"] or (
                    pending["option"] and value_key(controls) == pending["value_key"]):
                return await abandoned("context_action_unchanged", controls, choices,
                                       role=pending.get("role"), key=pending.get("key"),
                                       pending=pending)
        if decide is None and state.get("ready") == current:
            if not any(c.get("aria_expanded") == "true" for c in controls):
                state.pop("opened", None)
            state.pop("attempted", None)
            excluded = {c["id"] for c in choices}
            return {"candidates": [c for c in navigation_candidates(candidates)
                                    if c["id"] not in excluded]}
        if not enabled:
            return {"terminal": {"ok": False, "error_class": "selector_missing"}}
        # Collection callers inject their shared content/context observer.
        # The legacy local helper remains for compatibility outside that path.
        if decision is None:
            decision = await choose(target, controls, choices, timeout_s=timeout_s,
                                    requested=state.get("requested", {}))
        state.setdefault("requested", {}).update(decision.pop("requested", {}))
        if decision.get("ready"):
            state.update(ready=current, value_key=value_key(controls))
            if not any(c.get("aria_expanded") == "true" for c in controls):
                state.pop("opened", None)
            state.pop("attempted", None)
            excluded = {c["id"] for c in choices}
            return {"candidates": [c for c in navigation_candidates(candidates, controls=controls)
                                    if c["id"] not in excluded]}
        if decision.get("error_class"):
            return {"terminal": {"ok": False, **decision},
                    "rejection": {"phase": decision.get("phase", "context_decision"),
                                  "reason": decision.get("reason", "invalid_response"),
                                  "decision_actor": decision.get("_decision_actor", {"actor_type": "unknown"})}}
        candidate = decision["candidate"]
        attempt = (current, login_navigation.candidate_key(candidate))
        attempted = state.setdefault("attempted", set())
        key = login_navigation.candidate_key(candidate)
        if attempt in attempted or key in state.get("unverifiable", ()):
            return unverified("context_attempt_repeated", controls, choices,
                              role=decision.get("role"), key=key)
        if budget.get("actions", 0) >= login_navigation.COLLECTION_MAX_ACTIONS:
            return {"terminal": {"ok": False, "error_class": "goal_step_limit"}}
        attempted.add(attempt)
        budget["actions"] = budget.get("actions", 0) + 1
        if candidate in controls:
            state["opened"] = {"before": [login_navigation.candidate_key(c)
                                          for c in candidates]}
        state["pending"] = {"fingerprint": current, "value_key": value_key(controls),
                            "option": candidate in choices, "role": decision.get("role"),
                            "key": key, "changed_before": bool(state.get("changed"))}
        # Durable: the search changed a selection of the site (never cleared
        # with the attempt bookkeeping once the new value is verified).
        state["changed"] = True
        result = await execute(decision)
        if result.get("approval_required") or not result.get("ok"):
            return {"terminal": result}
        if not await settle():
            return {"terminal": {"ok": False, "error_class": "target_unstable"}}
        candidates = await read()


async def choose(target, controls, choices, *, timeout_s, requested=None):
    """Compare values locally; deterministic code selects the next action."""
    import i18n
    import prompt_loader
    from llm_router import LLMRouter
    from llm_workloads import tier_for

    if len(controls) > 8 or len(choices) > 64:
        return {"error_class": "selector_missing"}
    requested = dict(requested or {})
    def literal(value):
        return json.dumps(json.dumps(value))
    string_rules = ('string ::= "\\\"" ([^"\\\\] | "\\\\" .){1,64} "\\\""\n'
                    'ws ::= [ \\t\\n\\r]?\n')
    async def local(observation, grammar):
        prompt = json.dumps(observation, ensure_ascii=False)
        if len(prompt) > 16000:
            raise ValueError("context_observation_limit")
        def call():
            provider = LLMRouter().provider(tier_for("sites.action_reduce"))
            if getattr(provider, "mode", "") != "local":
                raise ValueError("local_model_required")
            return provider.chat(prompt_loader.get("agentic_sites_context_system",
                                 i18n.current_lang()), prompt, grammar=grammar,
                                 max_tokens=768, request_timeout_s=timeout_s).text
        return json.loads(await asyncio.wait_for(asyncio.to_thread(call), timeout=timeout_s))
    missing = [c for c in controls if login_navigation.candidate_key(c) not in requested]
    # An explicit, literal alternative is stronger evidence than a model's
    # occasional false "not specified" answer. Semantic synonyms still need
    # local interpretation.
    for c in missing[:]:
        explicit = explicit_alternatives(target, c.get("name", ""))
        if len(explicit) > 1:
            return {"error_class": "selector_ambiguous"}
        if explicit:
            requested[login_navigation.candidate_key(c)] = next(iter(explicit))
            missing.remove(c)
    if missing:
        observation = {"task": "requested_values", "request": target,
                       "selectors": [{"id": c["id"], "label": c.get("name", "")}
                                     for c in missing]}
        grammar = ('root ::= "{" ws ' + literal("reason") + ' ws ":" ws string ws "," ws ' +
                   literal("values") + ' ws ":" ws "[" ws value (ws "," ws value)* ws "]" ws "}"\n'
                   'value ::= "null" | string\n' + string_rules)
        try:
            answer = await local(observation, grammar)
            values = answer.get("values") if isinstance(answer, dict) else None
            if not isinstance(values, list) or len(values) != len(missing):
                raise ValueError("incomplete_requested_values")
            for c, value in zip(missing, values):
                if value is not None and (not isinstance(value, str) or not value.strip()
                                          or len(value) > 64):
                    raise ValueError("invalid_requested_value")
                requested[login_navigation.candidate_key(c)] = value
        except Exception:
            return {"error_class": "selector_missing"}
    if all(requested[login_navigation.candidate_key(c)] is None for c in controls):
        return {"ready": True, "requested": requested}
    by_id = {c["id"]: c for c in choices}
    observation = {"task": "compare_values",
        "selectors": [{"id": c["id"], "label": c.get("name", ""),
                       "current_value": c.get("text", ""),
                       "open": c.get("aria_expanded") == "true"} for c in controls],
        "options": [describe(c) for c in choices]}
    prompt = json.dumps(observation, ensure_ascii=False)
    if len(controls) > 8 or len(choices) > 64 or len(prompt) > 16000 or len(by_id) != len(choices):
        return {"error_class": "selector_missing"}
    checks = []
    for c, observed in zip(controls, observation["selectors"]):
        key = login_navigation.candidate_key(c)
        value_rule = "value"
        if key in requested:
            observed["requested_value"] = requested[key]
            value_rule = '"null"' if requested[key] is None else literal(requested[key])
        checks.append('"{" ws ' + literal("selector_id") + ' ws ":" ws ' + literal(c["id"]) +
            ' ws "," ws ' + literal("requested_value") + ' ws ":" ws ' + value_rule + ' ws "," ws ' +
            literal("current_matches") + ' ws ":" ws boolean ws "," ws ' +
            literal("matching_options") + ' ws ":" ws options ws "}"')
    grammar = ('root ::= "[" ws ' + ' ws "," ws '.join(checks) + ' ws "]"\n' +
        'value ::= "null" | string\nboolean ::= "true" | "false"\n' +
        ('options ::= "[" ws (choice (ws "," ws choice)*)? ws "]"\nchoice ::= ' +
         ' | '.join(literal(cid) for cid in by_id) + '\n' if by_id else
         'options ::= "[" ws "]"\n') +
        'string ::= "\\\"" ([^"\\\\] | "\\\\" .){1,64} "\\\""\n' +
        'ws ::= [ \\t\\n\\r]?\n')

    try:
        checks = await local(observation, grammar)
        if not isinstance(checks, list) or len(checks) != len(controls):
            raise ValueError("incomplete_context_comparison")
        pending, interpreted = [], {}
        for c, check in zip(controls, checks):
            if (not isinstance(check, dict) or check.get("selector_id") != c["id"]
                or "requested_value" not in check
                or type(check.get("current_matches")) is not bool
                or not isinstance(check.get("matching_options"), list)):
                raise ValueError("invalid_context_comparison")
            wanted, matches = check["requested_value"], check["matching_options"]
            if wanted is not None and (not isinstance(wanted, str) or not wanted.strip()):
                raise ValueError("invalid_requested_value")
            if any(not isinstance(cid, str) or cid not in by_id for cid in matches):
                raise ValueError("unknown_context_option")
            key = login_navigation.candidate_key(c)
            if key in requested and wanted != requested[key]:
                raise ValueError("changed_requested_value")
            interpreted[key] = wanted
            if wanted is None or check["current_matches"]:
                continue
            if c.get("aria_expanded") != "true":
                pending.append(c)
            elif len(matches) != 1:
                return {"error_class": "selector_ambiguous" if matches else "selector_missing"}
            else:
                pending.append(by_id[matches[0]])
    except Exception:
        return {"error_class": "selector_missing"}
    if not pending:
        return {"ready": True, "requested": interpreted}
    selected = pending[0]
    identity = login_navigation.candidate_key(selected)
    if sum(login_navigation.candidate_key(c) == identity for c in controls + choices) != 1:
        return {"error_class": "selector_ambiguous"}
    return {"candidate": selected, "model_selected": True, "confidence": 0.5,
            "requested": interpreted}
