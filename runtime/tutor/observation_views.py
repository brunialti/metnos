"""Closed semantic authority for Tutor live observations.

A mode decision says only that a person requested real data.  It grants no
access.  This registry is the second, independent gate: one semantically
selected view binds a bounded user-visible purpose to exactly one registered
probe and an exact projection of its recursively typed payload.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np

from logging_setup import get_logger

from .models import TutorPrincipal
from .probes import ObservationCapsule, probe_contract


log = get_logger(__name__)
_AUDIENCE_RANK = {"user": 0, "instance_admin": 1}


@dataclass(frozen=True, slots=True)
class ObservationViewSpec:
    view_id: str
    probe_id: str
    audience: str
    title: dict[str, str]
    coverage: dict[str, str]
    excluded: dict[str, str]
    fact_paths: tuple[str, ...]

    def languages(self) -> tuple[str, ...]:
        """Locales fully authored for this view, derived from registry data."""

        fields = (self.title, self.coverage, self.excluded)
        return tuple(sorted(set.intersection(*(
            {str(lang).lower() for lang in values if str(lang).strip()}
            for values in fields
        ))))

    def localized(self, field: str, lang: str) -> str:
        values = getattr(self, field)
        base = str(lang or "en").lower().split("-", 1)[0]
        return str(values.get(base) or values.get("en") or "")

    def visible_to(self, audience: str) -> bool:
        return _AUDIENCE_RANK.get(audience, -1) >= _AUDIENCE_RANK.get(
            self.audience, 99)


_VIEWS: tuple[ObservationViewSpec, ...] = (
    ObservationViewSpec(
        view_id="SERVICES_STATUS",
        probe_id="service_health",
        audience="instance_admin",
        title={
            "it": "Stato corrente dei servizi Metnos",
            "en": "Current Metnos service status",
        },
        coverage={
            "it": (
                "Inventario osservato dei servizi: nome, descrizione, ambito, "
                "installazione, stato di esecuzione, salute e conteggi "
                "correnti di servizi attivi o degradati."
            ),
            "en": (
                "Observed service inventory: name, description, scope, "
                "installation, running state, health, and current counts of "
                "running or degraded services."
            ),
        },
        excluded={
            "it": (
                "PID, endpoint, log, configurazione interna, comandi di "
                "avvio/arresto e qualunque modifica."
            ),
            "en": (
                "PIDs, endpoints, logs, internal configuration, start/stop "
                "commands, and every modification."
            ),
        },
        fact_paths=("total", "running", "degraded", "services"),
    ),
    ObservationViewSpec(
        view_id="EXECUTOR_CATALOG_STATE",
        probe_id="admitted_executor_state",
        audience="instance_admin",
        title={
            "it": "Stato corrente del catalogo executor ammesso",
            "en": "Current admitted executor catalog state",
        },
        coverage={
            "it": (
                "Executor attualmente ammessi con nome, versione, ciclo di "
                "vita, appartenenza e stato rispetto allo standard; conteggi "
                "per categoria e solo il numero totale dei rifiutati."
            ),
            "en": (
                "Currently admitted executors with name, version, lifecycle, "
                "membership, and standards state; category counts and only "
                "the total number rejected."
            ),
        },
        excluded={
            "it": (
                "Identità, percorsi e motivazioni dei rifiutati, codice "
                "sorgente, argomenti completi ed esecuzione di executor."
            ),
            "en": (
                "Rejected identities, paths, and reasons; source code, full "
                "arguments, and executor execution."
            ),
        },
        fact_paths=(
            "total", "by_lifecycle", "by_membership", "executors",
            "rejected_total",
        ),
    ),
    ObservationViewSpec(
        view_id="OWNED_DEVICE_STATE",
        probe_id="owned_device_state",
        audience="user",
        title={
            "it": "Stato corrente dei dispositivi associati all'utente",
            "en": "Current state of the user's paired devices",
        },
        coverage={
            "it": (
                "Dispositivi associati alla persona autenticata: identità, "
                "nome, famiglia e architettura del sistema operativo, versione "
                "del client, ultimo heartbeat e disponibilità corrente."
            ),
            "en": (
                "Devices paired to the authenticated person: identity, name, "
                "operating-system family and architecture, client version, "
                "last heartbeat, and current availability."
            ),
        },
        excluded={
            "it": (
                "Telemetria hardware o termica, processi, file, chiavi, "
                "profilo completo, dati di altri utenti e operazioni remote."
            ),
            "en": (
                "Hardware or thermal telemetry, processes, files, keys, full "
                "profiles, other users' data, and remote operations."
            ),
        },
        fact_paths=("total", "available", "devices"),
    ),
    ObservationViewSpec(
        view_id="ACTOR_TASK_STATE",
        probe_id="actor_task_state",
        audience="user",
        title={
            "it": "Stato corrente delle attività programmate dell'utente",
            "en": "Current state of the user's scheduled tasks",
        },
        coverage={
            "it": (
                "Attività programmate appartenenti alla persona autenticata: "
                "nome, etichetta, regola temporale, abilitazione, prossima e "
                "ultima esecuzione, esito e durata delle esecuzioni recenti."
            ),
            "en": (
                "Scheduled tasks owned by the authenticated person: name, "
                "label, schedule, enabled state, next and last run, outcome, "
                "and duration of recent runs."
            ),
        },
        excluded={
            "it": (
                "Testo operativo del task, output delle esecuzioni, attività "
                "di altri utenti, creazione, modifica ed esecuzione."
            ),
            "en": (
                "Task operation text, run outputs, other users' tasks, and "
                "task creation, modification, or execution."
            ),
        },
        fact_paths=("total", "enabled", "tasks"),
    ),
    ObservationViewSpec(
        view_id="SCHEDULER_HEALTH",
        probe_id="scheduler_health",
        audience="instance_admin",
        title={
            "it": "Salute corrente dello scheduler Metnos",
            "en": "Current Metnos scheduler health",
        },
        coverage={
            "it": (
                "Stato e salute del loop scheduler, causa diagnostica, avvio, "
                "heartbeat, conteggi dei job e riepilogo dell'ultima esecuzione "
                "o dell'errore corrente."
            ),
            "en": (
                "Scheduler-loop state and health, diagnostic cause, start, "
                "heartbeat, job counts, and a summary of the latest run or "
                "current error."
            ),
        },
        excluded={
            "it": (
                "Payload e output dei singoli job, dati di altri utenti, "
                "modifica della pianificazione e comandi sul servizio."
            ),
            "en": (
                "Individual job payloads and outputs, other users' data, "
                "schedule changes, and service commands."
            ),
        },
        fact_paths=(
            "component", "cohost", "state", "healthy", "reason_code",
            "started_at", "heartbeat_at", "heartbeat_age_s", "jobs_total",
            "jobs_enabled", "jobs_running", "last_run_at",
            "last_run_status", "error_class", "error_summary",
        ),
    ),
)


def catalog() -> tuple[ObservationViewSpec, ...]:
    return _VIEWS


def registered_view_ids() -> frozenset[str]:
    return frozenset(view.view_id for view in _VIEWS)


def validate_views() -> tuple[str, ...]:
    """Cross-check view identity, audience, localization, and probe schema."""

    findings: list[str] = []
    ids = [view.view_id for view in _VIEWS]
    if len(ids) != len(set(ids)):
        findings.append("duplicate_view_id")
    for view in _VIEWS:
        if not view.view_id or view.view_id != view.view_id.upper():
            findings.append(f"invalid_view_id:{view.view_id}")
        contract = probe_contract(view.probe_id)
        if contract is None:
            findings.append(f"unknown_probe:{view.view_id}:{view.probe_id}")
            continue
        probe_audience, fact_schema = contract
        if (_AUDIENCE_RANK.get(view.audience, -1)
                < _AUDIENCE_RANK.get(probe_audience, 99)):
            findings.append(f"audience_widening:{view.view_id}")
        if not view.fact_paths or len(view.fact_paths) != len(set(view.fact_paths)):
            findings.append(f"invalid_fact_paths:{view.view_id}")
        unknown = set(view.fact_paths) - set(fact_schema)
        if unknown:
            findings.append(
                f"unknown_fact_paths:{view.view_id}:{','.join(sorted(unknown))}")
        language_sets = {
            field: {
                str(lang).lower() for lang, value in getattr(view, field).items()
                if str(lang).strip() and str(value).strip()
            }
            for field in ("title", "coverage", "excluded")
        }
        declared = set().union(*language_sets.values())
        if "en" not in declared:
            findings.append(f"missing_fallback_locale:{view.view_id}:en")
        for lang in sorted(declared):
            missing = [
                field for field, languages in language_sets.items()
                if lang not in languages
            ]
            if missing:
                findings.append(
                    f"incomplete_locale:{view.view_id}:{lang}:"
                    f"{','.join(missing)}")
    return tuple(findings)


@dataclass(frozen=True, slots=True)
class ViewSelection:
    view: ObservationViewSpec | None
    available: bool
    reason: str = ""


_SELECTOR = SimpleNamespace(
    name="tutor_observation_select",
    execution_policy={
        "effect": "read_only",
        "parallelism_class": 0,
        "resource_class": "llm",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    },
)


def select_view(*, query: str, lang: str, principal: TutorPrincipal,
                deadline_at: float) -> ViewSelection:
    """Semantically select one complete visible view; never infer authority."""

    visible = tuple(
        view for view in _VIEWS if view.visible_to(principal.audience))
    if not visible:
        return ViewSelection(None, True, "no_visible_view")
    inventory = [{
        "view_id": view.view_id,
        "title": view.localized("title", lang),
        "coverage": view.localized("coverage", lang),
        "excluded": view.localized("excluded", lang),
        "fact_paths": list(view.fact_paths),
    } for view in visible]
    from .mode import _invoke_closed_classifier
    selected, reason = _invoke_closed_classifier(
        prompt_name="tutor_observation_select",
        executor=_SELECTOR,
        payload={
            "language": lang,
            "user_query": query,
            "views": inventory,
        },
        lang=lang,
        allowed=frozenset({"NONE", *(view.view_id for view in visible)}),
        deadline_at=deadline_at,
    )
    if selected is None:
        return ViewSelection(None, False, reason)
    if selected == "NONE":
        return ViewSelection(None, True, "no_complete_view")
    view = next((item for item in visible if item.view_id == selected), None)
    if view is None:
        return ViewSelection(None, False, "unregistered_selection")

    # A second context-free pass sees only the proposed contract.  Selection
    # and verification are deliberately separate decisions: topical
    # proximity may propose a view, but cannot by itself grant live authority.
    verified, verify_reason = _invoke_closed_classifier(
        prompt_name="tutor_observation_select",
        executor=_SELECTOR,
        payload={
            "language": lang,
            "user_query": query,
            "verification_pass": True,
            "views": [{
                "view_id": view.view_id,
                "title": view.localized("title", lang),
                "coverage": view.localized("coverage", lang),
                "excluded": view.localized("excluded", lang),
                "fact_paths": list(view.fact_paths),
            }],
        },
        lang=lang,
        allowed=frozenset({"NONE", view.view_id}),
        deadline_at=deadline_at,
    )
    if verified is None:
        return ViewSelection(None, False, verify_reason)
    if verified != view.view_id:
        return ViewSelection(None, True, "verification_rejected")
    return ViewSelection(view, True, "semantic_view")


def verify_semantic_coverage(*, query: str, lang: str,
                             principal: TutorPrincipal,
                             selected: ObservationViewSpec,
                             snapshot, deadline_at: float) -> tuple[bool, str]:
    """Require a strong, contrastive coverage match for live authority.

    The first two gates are closed LLM classifications.  This independent
    dense gate uses only the signed live-view units already present in the
    admitted Tutor generation.  A selected view must be the unique semantic
    primary among all visible views, sit one relevance band above the general
    documentation floor, and lead the runner-up by a fraction of that same
    corpus-wide band.  No query wording, topic, view id, or user affinity is
    encoded in the decision.
    """

    from .deadline import remaining
    from .semantic import (
        _language_order,
        _query_vector,
        knowledge_band,
        knowledge_minimum,
    )

    remaining(deadline_at)
    visible = tuple(
        view for view in _VIEWS if view.visible_to(principal.audience))
    if selected not in visible:
        return False, "view_not_visible"

    units = tuple(
        unit for unit in getattr(snapshot, "units", ())
        if unit.source_kind == "live_observation"
        and unit.observation_ref in {view.view_id for view in visible}
        and unit.visible_to(principal.audience)
    )
    language_order = _language_order(lang)
    chosen_units = []
    for view in visible:
        candidates = tuple(
            unit for unit in units if unit.observation_ref == view.view_id)
        unit = next(
            (item for candidate_lang in language_order for item in candidates
             if item.lang == candidate_lang),
            None,
        )
        if unit is None:
            return False, "coverage_unit_missing"
        chosen_units.append((view, unit))

    index = getattr(snapshot, "knowledge_index", None)
    refs = getattr(index, "refs", ())
    row_by_ref = {ref: row for row, ref in enumerate(refs)}
    rows = []
    for view, unit in chosen_units:
        row = row_by_ref.get((unit.unit_id, unit.lang))
        if row is None:
            return False, "coverage_vector_missing"
        rows.append((view, row))

    vector = _query_vector(
        query, int(index.dimension), deadline_at=deadline_at)
    ranked = sorted(
        ((float(index.matrix[row] @ vector), view) for view, row in rows),
        key=lambda item: (-item[0], item[1].view_id),
    )
    if not ranked or ranked[0][1] != selected:
        return False, "coverage_not_primary"

    band = knowledge_band()
    authority_floor = min(1.0, knowledge_minimum() + band)
    if not np.isfinite(ranked[0][0]) or ranked[0][0] < authority_floor:
        return False, "coverage_below_authority_floor"
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < band / 3.0:
        return False, "coverage_ambiguous"
    return True, "coverage_verified"


def project_capsule(view: ObservationViewSpec,
                    capsule: ObservationCapsule) -> ObservationCapsule:
    """Apply the exact view projection to an already validated raw capsule."""

    if capsule.probe_id != view.probe_id:
        raise ValueError("observation view/probe mismatch")
    if not capsule.facts:
        facts = {}
    else:
        missing = set(view.fact_paths) - set(capsule.facts)
        if missing:
            raise ValueError("observation capsule misses view facts")
        facts = {
            path: deepcopy(capsule.facts[path]) for path in view.fact_paths
        }
    return replace(capsule, facts=facts, view_id=view.view_id)


_validation = validate_views()
if _validation:
    raise RuntimeError("invalid Tutor observation views: " + ", ".join(
        _validation))
