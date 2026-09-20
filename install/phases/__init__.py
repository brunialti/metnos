# SPDX-License-Identifier: AGPL-3.0-only
"""Six install phases per ADR 0145.

Each phase exposes a single ``run(args)`` function returning a dict of
notes that get persisted in the state sentinel. Phases are decoupled —
the orchestrator wires them via the state module.
"""
