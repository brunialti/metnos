# SPDX-License-Identifier: MIT
"""Bounded navigation search. No browser, model or authority of its own.

The broker supplies observations and executes every selected control through
its ordinary gates. Backtracking restores the observed entry page and replays
only the verified path to the previous fork. Search and replay share a budget;
approval pauses retain both the cursor and the already attempted alternatives.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from urllib.parse import urlsplit

MAX_DEPTH = 4
MAX_ACTIONS = 32
COLLECTION_MAX_ACTIONS = 256
# Roberto, 12 October 2026: eight levels for collections (was three).
COLLECTION_MAX_DEPTH = 8
COLLECTION_TIMEOUT_S = 600
_IDENTITY_FIELDS = (
    "tag", "role", "type", "name", "href", "form_action", "form_method",
    "download", "secret_input", "context_name", "_record_context_key")


def candidate_key(candidate: dict) -> str:
    # Broker IDs and geometry change across a reload; destinations and browser
    # semantics identify the control. Values of inputs are never observations.
    stable = {key: candidate.get(key) for key in _IDENTITY_FIELDS
              if key != "_record_context_key" or candidate.get(key)}
    return hashlib.sha256(json.dumps(
        stable, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def untried_candidates(candidates: list[dict], tried: Collection[str], *,
                       pending_key: str | None = None) -> list[dict]:
    """Preserve observed controls and order, excluding attempted identities."""
    return [candidate for candidate in candidates
            if (identity := candidate_key(candidate)) not in tried
            and identity != pending_key]


def _replay_mismatch(expected: dict, candidates: list[dict]) -> dict:
    """Count identity differences without recording page text or destinations."""
    single_field_changes = {field: 0 for field in _IDENTITY_FIELDS}
    for candidate in candidates:
        changed = [field for field in _IDENTITY_FIELDS
                   if candidate.get(field) != expected.get(field)]
        if len(changed) == 1:
            single_field_changes[changed[0]] += 1
    return {"candidate_count": len(candidates),
            "same_name_count": sum(c.get("name") == expected.get("name")
                                   for c in candidates),
            "same_href_count": sum(c.get("href") == expected.get("href")
                                   for c in candidates),
            "single_field_changes": single_field_changes}


def state_key(url: str, candidates: list[dict]) -> str:
    controls = sorted((candidate_key(candidate), *(
        str(candidate.get(key)) for key in (
            "visible", "in_viewport", "topmost", "disabled", "aria_expanded",
            "aria_selected", "aria_pressed", "aria_checked", "checked")))
        for candidate in candidates)
    # Full URL distinguishes hash/query SPA routes; only its digest is kept in
    # search identities. Raw bookmarks stay private to the broker's memory.
    return hashlib.sha256(json.dumps([url, controls]).encode()).hexdigest()


def replay_page_diagnostics(previous: dict, current: dict, *, producer: str) -> dict:
    """Explain the existing refusal using equality only; keep values private."""
    result = {"producer": producer if producer in ("broker_page", "nav_refresh") else "unknown"}
    for field, label in (("key", "observation"), ("location_key", "location_key"),
                         ("context_key", "context_key")):
        saved, observed = bool(previous.get(field)), bool(current.get(field))
        result[f"saved_{label}_present"] = saved
        result[f"current_{label}_present"] = observed
        result[f"same_{label}"] = previous[field] == current[field] if saved and observed else None
    result["same_page"] = (previous["page"] is current["page"]
                           if previous.get("page") is not None
                           and current.get("page") is not None else None)
    before_url, after_url = previous.get("url"), current.get("url")
    try:
        before = urlsplit(before_url if isinstance(before_url, str) else "")
        after = urlsplit(after_url if isinstance(after_url, str) else "")
        available = bool(before.scheme and before.netloc and after.scheme and after.netloc)
    except ValueError:
        available = False
    result["urls_present"] = available
    for component in ("origin", "path", "query", "fragment"):
        equal = None
        if available:
            equal = ((before.scheme, before.netloc) == (after.scheme, after.netloc)
                     if component == "origin" else
                     getattr(before, component) == getattr(after, component))
        result[f"same_{component}"] = equal
    return result


# A decision taken on a scene that changed before it could act is read again,
# this many times in a row at most.
MAX_REOBSERVE = 2


# The navigation cursor: where a search is and what it tried there. A new
# record partition starts a new cursor; the budget (`actions`) and the
# evidence (`rejection`, `depth_limited`, `no_effect` keyed by place) stay.
CURSOR_FIELDS = ("frames", "tried", "seen", "limited", "deferred", "pending", "replay")


def reset_cursor(state: dict) -> None:
    for field in CURSOR_FIELDS:
        state.pop(field, None)


def spend(state: dict, max_actions: int) -> bool:
    """One action of the shared budget; False once it is used up."""
    used = int(state.get("actions", 0))
    if used >= max_actions:
        return False
    state["actions"] = used + 1
    return True


class Reobserve(Exception):
    """choose(): the scene changed after the decision; observe it again."""


def _failure(code: str) -> dict:
    return {"ok": False, "error_class": code}


async def discover(state: dict, *, observe, choose, execute, restore,
                   max_depth: int = MAX_DEPTH, max_actions: int = MAX_ACTIONS,
                   exhaustive: bool = False,
                   restart_on_replay_change: bool = False,
                   guard=None, aside_return=None) -> dict:
    """Depth-first exploration, bounded independently of approval round trips.

    observe -> {key, candidates, ...} or a terminal result;
    choose(observation, tried) -> broker-verified choice or None;
    execute(choice) / restore(root observation) -> ordinary broker result.
    A replay must match each saved observation before the next click. A
    collection may supply a location_key to refresh changing page content;
    replay still requires the exact saved control at that location.
    An opted-in search may discard a changed replay branch and restore the
    entry page for a fresh decision, retaining attempts and all budgets.
    choose may raise Reobserve: the page changed after its decision, with no
    effect; it is observed and decided again, MAX_REOBSERVE times in a row.
    guard() -> terminal result or None: the current-page checks of observe,
    without reading or deciding. When supplied, a return already decided
    runs only these checks before restoring; the restored page is observed
    fully. Without it every return is preceded by a full observation.
    aside_return(popped, frame, ending) -> bool: closes a page opened aside
    for a popped leaf, or goes back to the frame by other means; True when
    the current page is again the one of the frame returned to, which is then
    observed instead of restored. If it no longer matches, the return is
    restored as usual and state["aside_disabled"] set. `ending`: the search
    ends here, nothing is returned to.
    """
    frames = state.setdefault("frames", [])
    tried = state.setdefault("tried", {})
    seen = state.setdefault("seen", set())
    # Controls set aside at the depth limit, or left to the parent of a state
    # changed in place, were never followed there.
    limited = state.setdefault("limited", {})
    deferred = state.setdefault("deferred", {})

    def in_place(child: dict, parent: dict) -> bool:
        location = child["observation"].get("location_key")
        return bool(location) and location == parent["observation"].get("location_key")

    def share_attempts(source: dict, target: dict, via: dict | None) -> None:
        # What was tried in one state of a page changed in place is tried in
        # the other. A persistent non-continuation was already tried; next/more
        # stays available. Legacy choosers without the flag keep their behavior.
        # Controls set aside at the depth limit were never followed.
        key = source["observation"]["key"]
        repeatable = ({(via or {}).get("key")}
                      if (via or {}).get("continuation") is not False else set())
        moved = (tried.get(key, set()) - limited.get(key, set())
                 - deferred.get(key, set()) - repeatable)
        tried.setdefault(target["observation"]["key"], set()).update(moved)

    def defer_to_parent(child: dict, parent: dict) -> None:
        # A state changed in place explores what the change revealed; the
        # controls it shares with its parent are the parent's alternatives,
        # explored from there instead of from every menu opened on the way.
        via = (child.get("via") or {}).get("key")
        key = child["observation"]["key"]
        left = {candidate_key(c) for c in parent["observation"]["candidates"]} - {via}
        left -= tried.get(key, set())
        deferred.setdefault(key, set()).update(left)
        tried.setdefault(key, set()).update(left)

    def refresh(frame: dict, observation: dict, *, preserve: bool = False) -> bool:
        previous = frame["observation"]
        if previous["key"] == observation["key"]:
            return True
        if (not previous.get("location_key") or
                previous["location_key"] != observation.get("location_key")):
            return False
        if preserve:
            # A passing replay can omit controls loaded by earlier scrolling.
            # Keep that fork until it is observed fully for a new decision.
            return True
        # Changing banners are not new branches or a fresh budget. Preserve
        # attempted semantic controls, then let the broker read the new page.
        tried.setdefault(observation["key"], set()).update(
            tried.get(previous["key"], set()))
        frame["observation"] = observation
        seen.add(observation["key"])
        return True

    while True:
        if guard is not None and (state.get("replay") or {}).get("index") == -1:
            # The decision to return was taken on an already observed page;
            # reading and classifying it again cannot change the restore.
            terminal = await guard()
            if terminal is not None:
                return terminal
            if state.pop("refreshed", False) and frames:
                return _failure("target_changed")
            observation, key = None, None
        else:
            observation = await observe()
            if observation.get("terminal") is not None:
                return observation["terminal"]
            key = observation["key"]
        if observation is not None and state.pop("refreshed", False) and frames:
            # An explicitly authorised resource reload may finish rendering
            # this same node. It does not reset depth, time or attempts.
            if state.get("pending") or state.get("replay") is not None:
                return _failure("target_changed")
            frames[-1]["observation"] = observation
            seen.add(key)
        replay = state.get("replay")
        if replay is not None:
            index = replay["index"]
            if index == -1:
                if not spend(state, max_actions):
                    return _failure("login_step_limit")
                # Advance before awaiting: a pause must not repeat an effect.
                replay["index"] = 0
                restart = replay.pop("restart", False)
                restore_index = replay.get("restore_index", 0)
                replay["index"] = restore_index
                root = frames[restore_index]
                result = await restore(root.get("restore_observation", root["observation"]))
                if restart:
                    if result.get("approval_required") or (
                            result.get("ok") and result.get("executed")):
                        replay["fresh_root"] = True
                    elif result.get("ok"):
                        return _failure("target_changed")
            else:
                if replay.pop("verify_get", False):
                    saved = frames[index]["observation"]
                    # An observer that separates the page's controls from its
                    # text lets the text change (a banner, a date): the same
                    # controls in the same state are the same view (real run
                    # R37). Without that separation the whole key must match.
                    identity = ("controls_key" if saved.get("controls_key")
                                and observation.get("controls_key") else "key")
                    if (observation.get("page") is not saved.get("page")
                            or any(observation.get(field) != saved.get(field)
                                   for field in ("url", identity, "context_key", "location_key"))
                            or not observation.get("get_restore_context")):
                        frames[index]["get_disabled"] = True
                        state["rejection"] = {"phase": "restore_page", "index": index,
                            **replay_page_diagnostics(saved, observation, producer="nav_refresh")}
                        state["replay"] = {"index": -1}
                        continue
                if replay.pop("fresh_root", False):
                    # This is a new root observation after an authorised GET,
                    # not proof that the discarded observations are equal.
                    previous = frames[0]["observation"]
                    tried.setdefault(key, set()).update(tried.get(previous["key"], ()))
                    frames[0]["observation"] = observation
                    seen.add(key)
                if not refresh(frames[index], observation,
                               preserve=index < len(frames) - 1):
                    state["rejection"] = {"phase": "replay_page", "index": index,
                        **replay_page_diagnostics(frames[index]["observation"], observation,
                                                  producer="nav_refresh")}
                    if not restart_on_replay_change:
                        return _failure("target_changed")
                    root = frames[0]
                    # Keep the original entry bookmark even when its freshly
                    # restored page has a different URL or observation key.
                    root.setdefault("restore_observation", root["observation"])
                    del frames[1:]
                    state["replay"] = {"index": -1, "restart": True}
                    continue
                if index == len(frames) - 1:
                    state.pop("replay", None)
                else:
                    choice = {**frames[index + 1]["via"], "replay": True}
                    matches = [candidate for candidate in observation["candidates"]
                               if candidate_key(candidate) == choice["key"]]
                    if len(matches) != 1:
                        state["rejection"] = {"phase": "replay_control",
                            "index": index, "match_count": len(matches),
                            **_replay_mismatch(choice["candidate"], observation["candidates"])}
                        return _failure("target_changed")
                    if not spend(state, max_actions):
                        return _failure("login_step_limit")
                    choice["candidate"] = matches[0]
                    replay["index"] = index + 1
                    result = await execute(choice)
            if state.get("replay") is not None:
                if result.get("approval_required") or not result.get("ok"):
                    return result
                continue

        pending = state.pop("pending", None)
        if not frames or pending is not None:
            if not frames or key != frames[-1]["observation"]["key"]:
                # Pagination and a button that changes the page in place (a
                # menu, all items of the same list) are not a deeper level.
                revealed = bool(frames and pending
                                and not (pending.get("candidate") or {}).get("href")
                                and observation.get("location_key")
                                and observation.get("location_key")
                                == frames[-1]["observation"].get("location_key"))
                depth = (frames[-1]["depth"] +
                         (0 if pending.get("continuation") or revealed else 1)
                         if frames and pending else 0)
                frames.append({"observation": observation, "via": pending,
                               "depth": depth,
                               "cycle": key in seen})
                seen.add(key)
                if len(frames) > 1 and in_place(frames[-1], frames[-2]):
                    share_attempts(frames[-2], frames[-1], pending)
                    defer_to_parent(frames[-1], frames[-2])
            elif pending is not None and pending.get("key"):
                # The click left this exact observation unchanged. At this
                # location the same control is not tried again on a later visit.
                place = observation.get("location_key") or key
                state.setdefault("no_effect", {}).setdefault(place, set()).add(pending["key"])
        elif not refresh(frames[-1], observation):
            if state.pop("aside_returned", False):
                # The page changed while its leaf was open aside: return as
                # usual, and follow later leaves in place.
                state["aside_disabled"] = True
                state["replay"] = {"index": -1}
                continue
            # An external change is not a new authorisation or a fresh budget.
            state["rejection"] = {"phase": "current_page"}
            return _failure("target_changed")
        state.pop("aside_returned", None)

        at_limit = frames[-1]["depth"] >= max_depth
        if at_limit and not exhaustive:
            state["depth_limited"] = True
        attempted = tried.setdefault(key, set())
        attempted.update((state.get("no_effect") or {}).get(
            observation.get("location_key") or key, ()))
        try:
            choice = (None if (at_limit and not exhaustive) or frames[-1]["cycle"]
                      else await choose(observation, attempted))
        except Reobserve:
            state["reobserved"] = int(state.get("reobserved", 0)) + 1
            if state["reobserved"] > MAX_REOBSERVE:
                state["rejection"] = {"phase": "collection_snapshot_fence",
                                      "reason": "scene_changed_repeatedly"}
                return _failure("target_changed")
            continue
        state.pop("reobserved", None)
        if at_limit and choice is not None and not choice.get("continuation"):
            # Pagination stays at the same depth. A deeper relevant branch
            # is left unexplored, so exhaustion cannot certify completeness.
            state["depth_limited"] = True
            identity = candidate_key(choice["candidate"])
            if identity in attempted:
                return _failure("login_entry_stalled")
            attempted.add(identity)
            limited.setdefault(key, set()).add(identity)
            continue
        if choice is not None:
            identity = candidate_key(choice["candidate"])
            if identity in attempted:
                return _failure("login_entry_stalled")
            if not spend(state, max_actions):
                return _failure("login_step_limit")
            attempted.add(identity)
            choice = {**choice, "key": identity}
            state["pending"] = choice
            result = await execute(choice)
            if (exhaustive and result.get("ok") is False
                    and result.get("collection_bind_refused") is True
                    and isinstance(result.get("error_class"), str)
                    and not result.get("approval_required")):
                # The broker refused this fresh choice before any click, with
                # page and context unchanged. Keep the control attempted and
                # the first cause; the next loop observes the page again.
                state.pop("pending", None)
                state.setdefault("unresolved_control", result["error_class"])
                continue
            if result.get("approval_required") or not result.get("ok"):
                return result
            continue

        if exhaustive and state.get("search_complete"):
            # The collection proved the requested set complete on this page:
            # no return to other branches.
            return {"ok": True, "exhausted": True,
                    "depth_limited": bool(state.get("depth_limited"))}
        if len(frames) == 1:
            if exhaustive:
                return {"ok": True, "exhausted": True,
                        "depth_limited": bool(state.get("depth_limited"))}
            return _failure("login_step_limit" if state.get("depth_limited")
                            else "login_entry_stalled" if state.get("actions")
                            else "selector_missing")
        popped = []

        def pop() -> None:
            child = frames.pop()
            popped.append(child)
            if frames and in_place(child, frames[-1]):
                share_attempts(child, frames[-1], child.get("via"))

        pop()
        # Only revisit an actual fork. Replaying an exhausted single-child
        # chain spends time without offering an alternative.
        while len(frames) > 1:
            parent = frames[-1]["observation"]
            if untried_candidates(parent["candidates"], tried.get(parent["key"], ())):
                break
            pop()
        root = frames[0]["observation"]
        ending = exhaustive and len(frames) == 1 and not untried_candidates(
            root["candidates"], tried.get(root["key"], ()))
        returned = aside_return is not None and await aside_return(popped, frames[-1], ending)
        if ending:
            return {"ok": True, "exhausted": True,
                    "depth_limited": bool(state.get("depth_limited"))}
        if returned:
            state["aside_returned"] = True
            continue
        state["replay"] = {"index": -1}
        for index in range(len(frames) - 1, 0, -1):
            frame = frames[index]
            saved = frame["observation"]
            witness = (frame.get("via") or {}).get("observed_get")
            if (not frame.get("get_disabled") and witness
                    and witness[0] is saved.get("page") and witness[0] is root.get("page")
                    and witness[1] == saved.get("url")
                    and saved.get("get_restore_context")
                    and saved.get("url") != frames[index - 1]["observation"].get("url")
                    and not witness[0].is_closed()):
                state["replay"].update(restore_index=index, verify_get=True)
                break
