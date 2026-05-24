"""runtime.builtins — verb-unique builtin executors (ADR 0069, 0070).

This package houses the privileged primitives whose verbs are *outside*
the closed vocabulary (ADR 0045) and *invisible* to the PLANNER LLM.
They are invoked only by the system dispatcher.

Members at 2 May 2026:
  - admin:  deliberative orchestrator for shell-like intents.
  - sudoer: privileged executor with one-time secret slot for sudo password.

New members must satisfy the five invariants in ADR 0069 and be registered
explicitly via `runtime/loader.py::register_verb_unique_builtin`.
"""
