# SPDX-License-Identifier: MIT
"""Six install phases per ADR 0145.

Each phase exposes a single ``run(args)`` function returning a dict of
notes that get persisted in the state sentinel. Phases are decoupled —
the orchestrator wires them via the state module.
"""
