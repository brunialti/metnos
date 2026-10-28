# SPDX-License-Identifier: MIT
"""Bounded semantic cookie rejection; browser observations never grant authority."""
from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
import dataclasses
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CookieOutcome:
    status: Literal["clear", "resolved", "blocked"]
    kind: Literal["cookie", "other", "unknown"] = "unknown"
    reason: str = ""
    # Counts only, never observed text, and the largest seen during the flow:
    # a caller must be able to tell "no panel exists" from "panels exist and
    # none was actable", and from "a panel existed and was dismissed".
    frames: int = 0
    panels: int = 0
    # Controls of the observed panels: all, safe, safe with a native rejection
    # label, and a native rejection label on a control that is not safe (a
    # link, a submit). Counts only (real run Telepass 21/10/2026).
    controls: int = 0
    safe_controls: int = 0
    rejections: int = 0
    unsafe_rejections: int = 0


# A consent panel is frequently rendered inside a nested browsing context. A
# main-document observation cannot see it at all, so it reports no panel
# instead of an obstruction and the caller proceeds against a covered page.
MAX_OBSERVED_FRAMES = 8
MAX_OBSERVED_PANELS = 3
_MAX_PURPOSE_CHARS = 32
# The same blindness a second time, one level down: a consent platform that
# renders its banner into an open shadow root is invisible to a light-DOM
# query, and the observation reports a clear page while the page is covered.
# Observed on a real site: zero panels seen, no obstruction recorded, and the
# login discovery that followed found no form because a modal was over it.
MAX_OBSERVED_SHADOW_ROOTS = 24
# The per-origin budget above is two clicks. Consent state belongs to an
# origin and is reset when one is crossed - correctly, because a new origin's
# banner is a new panel - but the ceiling lived inside that same state, so a
# site bouncing between two origins reset it every time and never reached it.
# This one is carried across the resets: it is the whole session's ceiling.
MAX_SESSION_DISMISSALS = 6


# References remain inside a JSHandle, so identical replacement nodes cannot
# inherit a model decision. Text is bounded and excludes editable/secret areas.
_OBSERVE_JS = r"""() => {
  const SHADOW_ROOT_BUDGET = %d;
  // Upwards through the composed tree: a shadow root has no parent element,
  // its host does. Plain `parentElement` stops at the boundary and would make
  // every control inside a shadow root look like a top-level one.
  const up = el => {
    const parent = el.parentElement;
    if (parent) return parent;
    const root = el.getRootNode();
    return (root && root.host) ? root.host : null;
  };
  // These caches belong only to this synchronous observation. A transparent
  // ancestor can leave a child's own rectangle and opacity unchanged.
  const styles = new Map();
  const styleOf = el => {
    if (!styles.has(el)) styles.set(el, getComputedStyle(el));
    return styles.get(el);
  };
  const painted = el => {
    if (styleOf(el).visibility !== 'visible') return false;
    let opacity = 1;
    for (let node = el; node; node = up(node)) {
      const style = styleOf(node);
      opacity *= Number(style.opacity);
      if (style.display === 'none' || style.contentVisibility === 'hidden' ||
          opacity < .05) return false;
    }
    return true;
  };
  const visibleBox = r => r.width >= 2 && r.height >= 2 &&
    r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  const visible = el => painted(el) && visibleBox(el.getBoundingClientRect());
  const within = (outer, inner) => {
    for (let n = inner; n; n = up(n)) if (n === outer) return true;
    return false;
  };
  // `elementFromPoint` answers per root, so on a shadow host it returns the
  // host and never the control the user would actually hit. Descend until it
  // stops changing.
  const deepFromPoint = (x, y) => {
    let node = document.elementFromPoint(x, y);
    for (let depth = 0; node && node.shadowRoot && depth < 14; depth++) {
      const inner = node.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === node) break;
      node = inner;
    }
    return node;
  };
  const topmost = el => {
    const r = el.getBoundingClientRect();
    const top = deepFromPoint(
      Math.max(0, Math.min(innerWidth - 1, r.x + r.width / 2)),
      Math.max(0, Math.min(innerHeight - 1, r.y + r.height / 2)));
    return top === el || !!(top && within(el, top));
  };
  const excluded = 'input:not([type=button]):not([type=submit]),textarea,select,' +
    '[contenteditable]:not([contenteditable=false]),[data-metnos-redact],script,style';
  const publicArea = el => {
    for (let node = el; node; node = up(node))
      if (node.matches(excluded) || node.isContentEditable) return false;
    return true;
  };
  const text = (root, panelEvidence = false) => {
    const parts = [], visited = new Set();
    let seen = 0, characters = 0;
    const add = value => {
      if (seen >= 400 || characters >= 4000) return;
      const cleaned = String(value || '').replace(/\s+/g, ' ').trim();
      if (!cleaned) return;
      const part = cleaned.slice(0, 4000 - characters - (parts.length ? 1 : 0));
      if (!part) return;
      characters += part.length + (parts.length ? 1 : 0);
      parts.push(part);
      seen++;
    };
    const walk = node => {
      if (seen >= 400 || characters >= 4000 || !node || visited.has(node)) return;
      visited.add(node);
      if (node.nodeType === 3) {
        const parent = node.parentElement;
        if (parent && publicArea(parent) && painted(parent)) {
          let box = parent.getBoundingClientRect();
          // display:contents has visible text but no element box. Other
          // parents retain their box, so clipped/zero-size text stays out.
          if (styleOf(parent).display === 'contents') {
            const range = document.createRange();
            range.selectNodeContents(node);
            box = range.getBoundingClientRect();
          }
          if (visibleBox(box)) add(node.textContent);
        }
        return;
      }
      if (node.nodeType !== 1 && node.nodeType !== 11) return;
      if (node.nodeType === 1) {
        if (!publicArea(node)) return;
        // Only visible images in this panel supply alternatives. Arbitrary
        // descendant ARIA labels and input values are not panel prose.
        if (panelEvidence && node.tagName === 'IMG' && visible(node))
          add(node.getAttribute('alt'));
      }
      if (node.nodeType === 1 && node.shadowRoot) walk(node.shadowRoot);
      for (const child of node.childNodes) walk(child);
    };
    if (panelEvidence && publicArea(root) && visible(root)) {
      const ownName = (root.getAttribute('aria-label') || '').trim();
      if (ownName) add(ownName);
      else {
        const scope = root.getRootNode();
        const ids = (root.getAttribute('aria-labelledby') || '').split(/\s+/).slice(0, 400);
        for (const id of ids) {
          const label = scope && scope.getElementById ? scope.getElementById(id) : null;
          if (label && within(root, label)) walk(label);
        }
      }
    }
    walk(root);
    return parts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 4000);
  };
  const editableInside = root => {
    const selector =
      'input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]),' +
      'textarea,[contenteditable=true]';
    const stack = [root];
    for (let guard = 0; stack.length && guard < 4000; guard++) {
      const node = stack.pop();
      if (!node.querySelectorAll) continue;
      for (const el of node.querySelectorAll(selector)) if (visible(el)) return true;
      for (const el of node.querySelectorAll('*')) if (el.shadowRoot) stack.push(el.shadowRoot);
    }
    return false;
  };
  const searchRoots = [document];
  for (let index = 0; index < searchRoots.length; index++) {
    const node = searchRoots[index];
    if (!node.querySelectorAll) continue;
    for (const el of node.querySelectorAll('*')) {
      if (!el.shadowRoot) continue;
      if (searchRoots.length >= SHADOW_ROOT_BUDGET + 1) break;
      searchRoots.push(el.shadowRoot);
    }
  }
  const candidates = [];
  const selector = 'button,input[type=button],input[type=submit],a,[role=button]';
  for (const node of searchRoots) {
    if (candidates.length >= 640) break;
    if (node.querySelectorAll) candidates.push(...node.querySelectorAll(selector));
  }
  const roots = new Map();
  for (const el of candidates.slice(0, 640)) {
    if (!visible(el) || !topmost(el)) continue;
    let root = null;
    for (let p = up(el), depth = 0;
         p && p !== document.body && p !== document.documentElement && depth < 14;
         p = up(p), depth++) {
      const r = p.getBoundingClientRect(), s = getComputedStyle(p);
      const ratio = r.width * r.height / (innerWidth * innerHeight);
      if (p.matches('dialog,[role=dialog],[role=alertdialog],[aria-modal=true]') && ratio >= .03) {
        root = p; break;
      }
      // A persistent side rail can occupy a large page area without
      // covering the content. Keep smaller consent popups eligible.
      const sideRail = r.width < innerWidth * .25 &&
        r.height >= innerHeight * .8 &&
        (r.left <= 2 || r.right >= innerWidth - 2);
      if ((s.position === 'fixed' || s.position === 'sticky') &&
          ratio >= .12 && !sideRail) root = p;
    }
    if (!root || editableInside(root)) continue;
    if (!roots.has(root)) {
      if (roots.size >= 3) continue;
      roots.set(root, []);
    }
    if (roots.get(root).length < 16) roots.get(root).push(el);
  }
  const nodes = [], panels = [];
  for (const [root, controls] of roots) {
    const panel_id = 'p' + (panels.length + 1);
    const items = controls.map((el, i) => {
      const scope = el.getRootNode();
      const byId = id => (scope && scope.getElementById)
        ? scope.getElementById(id) : document.getElementById(id);
      const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(byId).filter(x => x && within(root, x))
        .map(text).join(' ');
      const name = (el.getAttribute('aria-label') || labelled ||
        el.getAttribute('title') || text(el) ||
        (el.matches('input[type=button],input[type=submit]') ? el.value : '')).trim();
      const form = el.form || el.closest('form');
      const submit = !!form && (el.matches('input[type=submit],input[type=image]') ||
        (el.tagName === 'BUTTON' && el.type === 'submit'));
      const safe = !submit && !el.closest('a[href],[formaction],[download]') &&
        !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
        !el.isContentEditable && /[\p{L}\p{N}]/u.test(name) && !/^x$/i.test(name);
      return {id: panel_id + 'c' + (i + 1), name: name.slice(0, 300), safe: !!safe};
    });
    nodes.push({root, controls, signatures: controls.map(el =>
      [el.outerHTML, String(el.onclick || '')])});
    panels.push({id: panel_id, text: text(root, true), controls: items});
  }
  return {nodes, public: panels, url: location.href};
}""" % (MAX_OBSERVED_SHADOW_ROOTS,)

_COMMIT_JS = r"""(saved, choice) => {
  const fresh = (""" + _OBSERVE_JS + r""")();
  if (fresh.url !== saved.url || JSON.stringify(fresh.public) !== JSON.stringify(saved.public) ||
      fresh.nodes.some((p, i) => p.root !== saved.nodes[i].root ||
        JSON.stringify(p.signatures) !== JSON.stringify(saved.nodes[i].signatures) ||
        p.controls.some((el, j) => el !== saved.nodes[i].controls[j]))) return 'stale_dom';
  if (choice.check_only) return 'unchanged';
  const pi = saved.public.findIndex(p => p.id === choice.panel_id);
  const ci = pi < 0 ? -1 : saved.public[pi].controls.findIndex(c => c.id === choice.control_id);
  if (ci < 0 || !fresh.public[pi].controls[ci].safe) return 'unsafe_control';
  saved.nodes[pi].controls[ci].click();
  return 'clicked';
}"""


def _normalize_privacy_label(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    # Preserve every writing system; invisible non-whitespace cannot supply
    # a word boundary or silently join two different labels.
    if any(unicodedata.category(char).startswith("C") and not char.isspace()
           for char in value):
        return ""
    return " ".join(value.split())


def _privacy_rejection_forms() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        import detection_lexicon

        forms = [tuple(normalized for value in detection_lexicon.native_ready_forms(
                     concept, include_reviewed_baselines=True)
                     if (normalized := _normalize_privacy_label(value)))
                 for concept in ("sites.privacy_reject_target",
                                 "sites.privacy_reject_noun_target")]
        return forms[0], forms[1]
    except Exception:
        # Missing or unreadable native language resources grant no click.
        return (), ()


def _has_explicit_rejection(
    control: dict,
    forms: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> bool:
    """Require a native rejection on this safe control's own visible label."""
    if control.get("safe") is not True:
        return False
    label = _normalize_privacy_label(control.get("name", ""))
    if not label:
        return False
    targets, nouns = forms if forms is not None else _privacy_rejection_forms()
    if label in nouns:
        return True
    for target in targets:
        if not label.startswith(target):
            continue
        suffix = label[len(target):]
        if (not suffix or not (
                suffix[0].isalnum() or suffix[0] == "_"
                or unicodedata.category(suffix[0]).startswith("M"))):
            return True
    return False


def _decision_options(panels: list[dict]) -> list[dict]:
    """Finite outcomes; clicks require one observed safe, positive rejection."""
    forms = _privacy_rejection_forms()
    # Uninformative observed panels cannot become clear through the cache,
    # grammar, or a provider response that bypasses constrained generation.
    if (panels and not any(_normalize_privacy_label(panel.get("text", ""))
                           for panel in panels)
            and not any(_has_explicit_rejection(control, forms)
                        for panel in panels for control in panel.get("controls", []))):
        return [{"kind": "unknown", "panel_id": "", "control_id": "", "effect": "none"}]
    options = [{"kind": kind, "panel_id": "", "control_id": "", "effect": "none"}
               for kind in ("cookie", "other", "unknown")]
    for panel in panels:
        panel_id = panel.get("id", "")
        if not isinstance(panel_id, str) or not panel_id:
            continue
        options.append({"kind": "cookie", "panel_id": panel_id,
                        "control_id": "", "effect": "none"})
        for control in panel.get("controls", []):
            control_id = control.get("id", "")
            if (_has_explicit_rejection(control, forms) and isinstance(control_id, str)
                    and control_id):
                options.append({"kind": "cookie", "panel_id": panel_id,
                                "control_id": control_id, "effect": "reject_optional"})
    return options


def _purpose_grammar(panel_count: int) -> str:
    """One ordered, bounded assessment per panel; no action fields exist."""
    if not 1 <= panel_count <= MAX_OBSERVED_PANELS:
        raise ValueError("invalid_decision")
    entries = ' ws "," ws '.join(["assessment"] * panel_count)
    # A nested optional chain bounds text without ambiguous placements of
    # independent optionals or requiring an additional GBNF dialect.
    characters = "purpose-char"
    for _ in range(_MAX_PURPOSE_CHARS - 1):
        characters = "purpose-char (" + characters + ")?"
    return (
        "root ::= ws decision ws\n"
        'decision ::= "{" ws "\\\"assessments\\\"" ws ":" ws "[" ws '
        + entries + ' ws "]" ws "}"\n'
        'assessment ::= "{" ws "\\\"purpose\\\"" ws ":" ws '
        '(purpose-string ws "," ws "\\\"type\\\"" ws ":" ws purpose-type '
        '| "null" ws "," ws "\\\"type\\\"" ws ":" ws "\\\"unresolved\\\"") ws "}"\n'
        'purpose-type ::= "\\\"consent\\\"" | "\\\"alternative\\\"" | "\\\"unresolved\\\""\n'
        'purpose-string ::= "\\\"" ' + characters + ' "\\\""\n'
        r'purpose-char ::= [^"\\\x00-\x1f\x7f-\x9f]' + "\n"
        r'ws ::= [ \t\n\r]?' + "\n")


def _unique_purpose_fields(pairs) -> dict:
    """Do not let JSON duplicate keys overwrite earlier model output."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_decision")
        result[key] = value
    return result


def _purpose_decision(panels: list[dict], response) -> dict:
    """Validate coverage and aggregate types; a paraphrase is not proof."""
    if (not 1 <= len(panels) <= MAX_OBSERVED_PANELS
            or not isinstance(response, dict) or set(response) != {"assessments"}
            or not isinstance(response["assessments"], list)
            or len(response["assessments"]) != len(panels)):
        # Preserve the existing diagnostic for attempted unsafe actions;
        # valid previous wire formats still fail the new internal schema.
        raise ValueError(_decision_error(panels, response) or "invalid_decision")
    types = []
    for assessment in response["assessments"]:
        if (not isinstance(assessment, dict)
                or list(assessment) != ["purpose", "type"]):
            raise ValueError("invalid_decision")
        purpose, category = assessment["purpose"], assessment["type"]
        if (not isinstance(category, str)
                or category not in {"consent", "alternative", "unresolved"}):
            raise ValueError("invalid_decision")
        if purpose is None:
            if category != "unresolved":
                raise ValueError("invalid_decision")
        elif (not isinstance(purpose, str) or not purpose.strip()
                or len(purpose) > _MAX_PURPOSE_CHARS
                or any(char in {'"', "\\"} or ord(char) < 0x20
                       or 0x7F <= ord(char) <= 0x9F
                       or 0xD800 <= ord(char) <= 0xDFFF for char in purpose)):
            raise ValueError("invalid_decision")
        types.append(category)
    kind = ("cookie" if "consent" in types else
            "unknown" if "unresolved" in types else "other")
    return {"kind": kind, "panel_id": "", "control_id": "", "effect": "none"}


def _decision_grammar(panels: list[dict], *, purpose_only: bool = False) -> str:
    if purpose_only:
        return _purpose_grammar(len(panels))
    # Each terminal is a complete JSON decision: independent ID enums would
    # still permit a control from a different panel or browsing context.
    options = _decision_options(panels)
    alternatives = [json.dumps(json.dumps(option, ensure_ascii=False,
                                         separators=(",", ":")), ensure_ascii=False)
                    for option in options]
    return ("root ::= ws decision ws\n"
            "decision ::= " + " | ".join(alternatives) + "\n"
            r"ws ::= [ \t\n\r]*" + "\n")


def _decision_error(panels: list[dict], decision) -> str:
    if (not isinstance(decision, dict) or set(decision) != {
            "kind", "panel_id", "control_id", "effect"}
            or not all(isinstance(value, str) for value in decision.values())
            or decision["kind"] not in {"cookie", "other", "unknown"}
            or decision["effect"] not in {"reject_optional", "none"}):
        return "invalid_decision"
    if decision in _decision_options(panels):
        return ""
    if decision["kind"] == "cookie" and decision["effect"] == "reject_optional":
        panel = next((panel for panel in panels
                      if panel.get("id") == decision["panel_id"]), None)
        if panel is None:
            return "unknown_panel"
        control = next((control for control in panel.get("controls", [])
                        if control.get("id") == decision["control_id"]), None)
        if control is None:
            return "unknown_control"
        if control.get("safe") is not True:
            return "unsafe_control"
        if not _has_explicit_rejection(control):
            return "no_safe_rejection"
    return "invalid_decision"


def _classify(panels: list[dict], timeout_s: float) -> dict:
    # The broker does not classify an empty observation. Preserve that API's
    # provider validation; share the sole unknown outcome with the validator.
    purpose_only = False
    if panels:
        options = _decision_options(panels)
        if len(options) == 1:
            return options[0]
        # Informative panels without positive rejection need a purpose, not
        # a control selection. Cookie and uncertain purposes remain blocking.
        purpose_only = not any(option["effect"] == "reject_optional"
                               for option in options)

    import i18n
    import prompt_loader
    from llm_router import LLMRouter
    from llm_workloads import tier_for

    provider = LLMRouter().provider(tier_for("sites.cookie_resolution"))
    if getattr(provider, "mode", "") != "local":
        raise RuntimeError("local_model_required")
    result = provider.chat(
        prompt_loader.get("sites_cookie_purpose" if purpose_only else
                          "sites_cookie_resolution", i18n.current_lang()),
        json.dumps(panels, ensure_ascii=False), max_tokens=160,
        request_timeout_s=timeout_s,
        grammar=_decision_grammar(panels, purpose_only=purpose_only))
    if purpose_only:
        raw = str(getattr(result, "text", "") or "")
        # The bounded grammar has no escape branch, including in fixed keys.
        if "\\" in raw:
            raise ValueError("invalid_decision")
        decision = _purpose_decision(panels, json.loads(
            raw, object_pairs_hook=_unique_purpose_fields))
    else:
        decision = json.loads(str(getattr(result, "text", "") or ""))
    error = _decision_error(panels, decision)
    if error:
        raise ValueError(error)
    return decision


async def _observe_frames(page) -> list[tuple[int, object, list[dict]]]:
    """Observe the main document and its nested contexts, bounded and in order.

    Each context keeps its own handle, because the live node references that
    the commit step re-verifies belong to that context alone. The panel budget
    stays global, so widening the observation never widens what the model sees.
    """
    observed: list[tuple[int, object, list[dict]]] = []
    total = 0
    for index, frame in enumerate(page.frames[:MAX_OBSERVED_FRAMES]):
        if total >= MAX_OBSERVED_PANELS:
            break
        handle = None
        try:
            handle = await frame.evaluate_handle(_OBSERVE_JS)
            panels = await handle.evaluate('(saved) => saved.public')
        except Exception:
            # A nested context can detach or navigate while it is read; that is
            # not an observation failure of the page as a whole.
            panels = None
        if not panels:
            await _dispose_one(handle)
            continue
        allowed = panels[:MAX_OBSERVED_PANELS - total]
        total += len(allowed)
        observed.append((index, handle, allowed))
    return observed


async def _dispose_one(handle) -> None:
    if handle is None:
        return
    try:
        await handle.dispose()
    except Exception:
        pass  # Navigation may already have destroyed the handle.


async def _dispose_all(observed) -> None:
    for _index, handle, _panels in observed:
        await _dispose_one(handle)


def _global_id(index: int, identifier: str) -> str:
    return f"f{index}{identifier}"


def _local_id(index: int, identifier: str) -> str:
    prefix = f"f{index}"
    if not isinstance(identifier, str) or not identifier.startswith(prefix):
        raise ValueError("invalid_context_id")
    local = identifier[len(prefix):]
    if not local:
        raise ValueError("invalid_context_id")
    return local


async def reject_cookies(page, state: dict, **options) -> CookieOutcome:
    """The rejection flow below, with the largest control counts it observed."""
    counts = {}
    outcome = await _reject_cookies(page, state, counts=counts, **options)
    return dataclasses.replace(outcome, **counts)


def _count_controls(panels: list[dict], counts: dict) -> None:
    forms = _privacy_rejection_forms()
    controls = [c for panel in panels for c in panel.get("controls", ())]
    observed = {"controls": len(controls),
                "safe_controls": sum(c.get("safe") is True for c in controls),
                "rejections": sum(_has_explicit_rejection(c, forms) for c in controls),
                "unsafe_rejections": sum(c.get("safe") is not True
                                         and _has_explicit_rejection({**c, "safe": True}, forms)
                                         for c in controls)}
    for key, value in observed.items():
        counts[key] = max(counts.get(key, 0), value)


async def _reject_cookies(page, state: dict, *, counts: dict, redact=None,
                          timeout_s: float = 6.0, enabled: bool = True,
                          classification_budget: dict | None = None) -> CookieOutcome:
    """Resolve at most two cookie panels, with four local calls per attempt.

    Cache stores only hashes and typed decisions; every click rechecks live
    node identity, text, visibility and HTML navigation/submit constraints.
    ``redact`` is an additional local defence supplied by credential injection.
    Share ``classification_budget`` across retries of the same attempt;
    ``state["calls"]`` records cumulative calls independently of that budget.
    """
    budget = classification_budget if classification_budget is not None else {"calls": 0}
    resolved = False
    previous_click = ""
    frames = panels_seen = 0
    for _ in range(3):
        observed = await _observe_frames(page)
        # The most this flow ever saw, not what is left at the end: a panel
        # that was found and dismissed did exist, and a run that reported zero
        # after resolving one would hide the only case worth recording.
        frames = max(frames, len(observed))
        panels_seen = max(
            panels_seen, sum(len(items) for _i, _h, items in observed))
        try:
            if not observed:
                return CookieOutcome(
                    "resolved" if resolved else "clear",
                    "cookie" if resolved else "unknown", "",
                    frames, panels_seen)
            # Redact only observed text, never IDs or JSON structure: a short
            # credential can coincide with an ID or the literal ``true``.
            clean = redact if redact is not None else lambda text: text
            safe_panels = []
            owner = {}
            for index, _handle, items in observed:
                for panel in items:
                    owner[_global_id(index, panel["id"])] = index
                    safe_panels.append({
                        **panel,
                        "id": _global_id(index, panel["id"]),
                        "text": clean(panel["text"]),
                        "controls": [
                            {**control,
                             "id": _global_id(index, control["id"]),
                             "name": clean(control["name"])}
                            for control in panel["controls"]],
                    })
            _count_controls(safe_panels, counts)
            serialized = json.dumps(safe_panels, ensure_ascii=False)
            key = hashlib.sha256(serialized.encode()).hexdigest()
            if key == previous_click:
                return CookieOutcome("blocked", "cookie", "obstruction_remains",
                                     frames, panels_seen)
            cache = state.setdefault("decisions", {})
            decision = cache.get(key)
            if decision is None:
                if not enabled:
                    return CookieOutcome("blocked", "unknown", "model_disabled",
                                         frames, panels_seen)
                if budget.get("calls", 0) >= 4:
                    return CookieOutcome("blocked", "unknown", "decision_limit",
                                         frames, panels_seen)
                budget["calls"] = budget.get("calls", 0) + 1
                state["calls"] = state.get("calls", 0) + 1
                try:
                    decision = await asyncio.wait_for(
                        asyncio.to_thread(_classify, safe_panels, timeout_s), timeout_s)
                except (asyncio.TimeoutError, TimeoutError):
                    return CookieOutcome("blocked", "unknown", "model_timeout",
                                         frames, panels_seen)
                except Exception:
                    return CookieOutcome("blocked", "unknown", "model_failed",
                                         frames, panels_seen)
            # Revalidate cached and fresh decisions against this observation.
            # No inferred prefix or ID repair can acquire click authority.
            error = _decision_error(safe_panels, decision)
            if error:
                cache.pop(key, None)
                return CookieOutcome(
                    "blocked", "unknown" if error == "invalid_decision" else "cookie",
                    error, frames, panels_seen)
            cache[key] = decision
            if decision["kind"] == "other" and decision["effect"] == "none":
                for _index, handle, _items in observed:
                    if await handle.evaluate(
                            _COMMIT_JS, {"check_only": True}) != "unchanged":
                        return CookieOutcome("blocked", "unknown", "stale_dom",
                                             frames, panels_seen)
                return CookieOutcome("resolved" if resolved else "clear", "other",
                                     "", frames, panels_seen)
            if decision["kind"] != "cookie" or decision["effect"] != "reject_optional":
                return CookieOutcome("blocked", decision["kind"], "no_safe_rejection",
                                     frames, panels_seen)
            index = owner.get(decision["panel_id"])
            if index is None:
                return CookieOutcome("blocked", "cookie", "unknown_panel",
                                     frames, panels_seen)
            handle = next(item[1] for item in observed if item[0] == index)
            if state.get("clicks", 0) >= 2:
                return CookieOutcome("blocked", "cookie", "dismissal_limit",
                                     frames, panels_seen)
            if (state.get("clicks", 0) + state.get("carried", 0)
                    >= MAX_SESSION_DISMISSALS):
                return CookieOutcome("blocked", "cookie", "session_limit",
                                     frames, panels_seen)
            outcome = await handle.evaluate(_COMMIT_JS, {
                **decision,
                "panel_id": _local_id(index, decision["panel_id"]),
                "control_id": _local_id(index, decision["control_id"]),
            })
            if outcome != "clicked":
                return CookieOutcome("blocked", "cookie", outcome,
                                     frames, panels_seen)
            state["clicks"] = state.get("clicks", 0) + 1
            previous_click = key
            # Consent UIs can dismiss asynchronously. Observe the exact saved
            # panel for at most two seconds; never reclassify or click again
            # merely because its first render after dispatch is unchanged.
            for _ in range(20):
                await page.wait_for_timeout(100)
                if await handle.evaluate(_COMMIT_JS, {"check_only": True}) != "unchanged":
                    break
            if await handle.evaluate('(saved) => saved.url !== location.href'):
                return CookieOutcome("blocked", "cookie", "page_changed",
                                     frames, panels_seen)
            resolved = True
        except Exception:
            return CookieOutcome("blocked", "unknown", "observation_failed",
                                 frames, panels_seen)
        finally:
            await _dispose_all(observed)
    return CookieOutcome("blocked", "cookie", "dismissal_limit",
                         frames, panels_seen)
