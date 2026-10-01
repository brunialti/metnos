# SPDX-License-Identifier: MIT
"""Bounded, pre-credential search. No browser, model or authority of its own.

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


def candidate_key(candidate: dict) -> str:
    # Broker IDs and geometry change across a reload; destinations and browser
    # semantics identify the control. Values of inputs are never observations.
    stable = {key: candidate.get(key) for key in (
        "tag", "role", "type", "name", "href", "form_action", "form_method",
        "download", "secret_input")}
    return hashlib.sha256(json.dumps(
        stable, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


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


async def discover(state: dict, *, observe, choose, execute, restore) -> dict:
    """Depth-first exploration, bounded independently of approval round trips.

    observe -> {key, candidates, ...} or a terminal result;
    choose(observation, tried) -> broker-verified choice or None;
    execute(choice) / restore(root observation) -> ordinary broker result.
    A replay must match each saved observation before the next click.
    """
    frames = state.setdefault("frames", [])
    tried = state.setdefault("tried", {})
    seen = state.setdefault("seen", set())

    def spend() -> bool:
        used = int(state.get("actions", 0))
        if used >= MAX_ACTIONS:
            return False
        state["actions"] = used + 1
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
                if key != frames[index]["observation"]["key"]:
                    return _failure("target_changed")
                if index == len(frames) - 1:
                    state.pop("replay", None)
                    continue
                choice = dict(frames[index + 1]["via"])
                matches = [candidate for candidate in observation["candidates"]
                           if candidate_key(candidate) == choice["key"]]
                if len(matches) != 1:
                    return _failure("target_changed")
                if not spend():
                    return _failure("login_step_limit")
                choice["candidate"] = matches[0]
                replay["index"] = index + 1
                result = await execute(choice)
            if result.get("approval_required") or not result.get("ok"):
                return result
            continue

        pending = state.pop("pending", None)
        if not frames or pending is not None:
            if not frames or key != frames[-1]["observation"]["key"]:
                frames.append({"observation": observation, "via": pending,
                               "cycle": key in seen})
                seen.add(key)
        elif key != frames[-1]["observation"]["key"]:
            # An external change is not a new authorisation or a fresh budget.
            return _failure("target_changed")

        at_limit = len(frames) - 1 >= MAX_DEPTH
        if at_limit:
            state["depth_limited"] = True
        attempted = tried.setdefault(key, set())
        choice = (None if at_limit or frames[-1]["cycle"]
                  else await choose(observation, attempted))
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
            if result.get("approval_required") or not result.get("ok"):
                return result
            continue

        if len(frames) == 1:
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
        state["replay"] = {"index": -1}
