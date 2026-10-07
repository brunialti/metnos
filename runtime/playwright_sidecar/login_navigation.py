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

MAX_DEPTH = 4
MAX_ACTIONS = 32
COLLECTION_MAX_ACTIONS = 256
COLLECTION_TIMEOUT_S = 300
_IDENTITY_FIELDS = (
    "tag", "role", "type", "name", "href", "form_action", "form_method",
    "download", "secret_input", "context_name")


def candidate_key(candidate: dict) -> str:
    # Broker IDs and geometry change across a reload; destinations and browser
    # semantics identify the control. Values of inputs are never observations.
    stable = {key: candidate.get(key) for key in _IDENTITY_FIELDS}
    return hashlib.sha256(json.dumps(
        stable, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


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


def _failure(code: str) -> dict:
    return {"ok": False, "error_class": code}


async def discover(state: dict, *, observe, choose, execute, restore,
                   max_depth: int = MAX_DEPTH, max_actions: int = MAX_ACTIONS,
                   exhaustive: bool = False) -> dict:
    """Depth-first exploration, bounded independently of approval round trips.

    observe -> {key, candidates, ...} or a terminal result;
    choose(observation, tried) -> broker-verified choice or None;
    execute(choice) / restore(root observation) -> ordinary broker result.
    A replay must match each saved observation before the next click. A
    collection may supply a location_key to refresh changing page content;
    replay still requires the exact saved control at that location.
    """
    frames = state.setdefault("frames", [])
    tried = state.setdefault("tried", {})
    seen = state.setdefault("seen", set())

    def spend() -> bool:
        used = int(state.get("actions", 0))
        if used >= max_actions:
            return False
        state["actions"] = used + 1
        return True

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
        observation = await observe()
        if observation.get("terminal") is not None:
            return observation["terminal"]
        key = observation["key"]
        if state.pop("refreshed", False) and frames:
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
                if not spend():
                    return _failure("login_step_limit")
                # Advance before awaiting: a pause must not repeat an effect.
                replay["index"] = 0
                result = await restore(frames[0]["observation"])
            else:
                if not refresh(frames[index], observation,
                               preserve=index < len(frames) - 1):
                    state["rejection"] = {"phase": "replay_page", "index": index}
                    return _failure("target_changed")
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
                    if not spend():
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
                depth = (frames[-1]["depth"] +
                         (0 if pending.get("continuation") else 1)
                         if frames and pending else 0)
                frames.append({"observation": observation, "via": pending,
                               "depth": depth,
                               "cycle": key in seen})
                seen.add(key)
        elif not refresh(frames[-1], observation):
            # An external change is not a new authorisation or a fresh budget.
            state["rejection"] = {"phase": "current_page"}
            return _failure("target_changed")

        at_limit = frames[-1]["depth"] >= max_depth
        if at_limit and not exhaustive:
            state["depth_limited"] = True
        attempted = tried.setdefault(key, set())
        choice = (None if (at_limit and not exhaustive) or frames[-1]["cycle"]
                  else await choose(observation, attempted))
        if at_limit and choice is not None and not choice.get("continuation"):
            # Pagination stays at the same depth. A deeper relevant branch
            # is left unexplored, so exhaustion cannot certify completeness.
            state["depth_limited"] = True
            identity = candidate_key(choice["candidate"])
            if identity in attempted:
                return _failure("login_entry_stalled")
            attempted.add(identity)
            continue
        if choice is not None:
            identity = candidate_key(choice["candidate"])
            if identity in attempted:
                return _failure("login_entry_stalled")
            if not spend():
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

        if len(frames) == 1:
            if exhaustive:
                return {"ok": True, "exhausted": True,
                        "depth_limited": bool(state.get("depth_limited"))}
            return _failure("login_step_limit" if state.get("depth_limited")
                            else "login_entry_stalled" if state.get("actions")
                            else "selector_missing")
        frames.pop()
        # Only revisit an actual fork. Replaying an exhausted single-child
        # chain spends time without offering an alternative.
        while len(frames) > 1:
            parent = frames[-1]["observation"]
            if any(candidate_key(candidate) not in tried.get(parent["key"], set())
                   for candidate in parent["candidates"]):
                break
            frames.pop()
        root = frames[0]["observation"]
        if exhaustive and len(frames) == 1 and not any(
                candidate_key(candidate) not in tried.get(root["key"], set())
                for candidate in root["candidates"]):
            return {"ok": True, "exhausted": True,
                    "depth_limited": bool(state.get("depth_limited"))}
        state["replay"] = {"index": -1}
