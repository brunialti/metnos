"""Query-free synthetic 20-case fixture used only to exercise the complete gate."""
from __future__ import annotations

from . import build
from . import engine as e
from .model import (
    ApprovalOwns, ConditionalBranches, Consumes, Control, Independent,
    LifecycleRecord, Negated, Outside, Positive, Proposal, ReviewerIdentity,
)


def _proposal(slot: dict, index: int, freeze_sha: str, routes: list[str]) -> Proposal:
    first, second = routes[index * 2], routes[index * 2 + 1]
    cell = slot["cell"]; subtype = slot["subtype"]
    obligations = (Positive(first),); relations = (); surface = ()
    if cell == "G2_COMPOUND_INDEPENDENT":
        obligations = (Positive(first), Positive(second)); relations = (Independent((0, 1)),)
    elif cell == "G3_COMPOUND_DEPENDENT":
        obligations = (Positive(first), Positive(second)); relations = (Consumes(0, 1, "result", "primary"),)
    elif cell == "G4_COVERAGE_BOUNDARY":
        outside = Outside(build.OUTSIDE.keys().__iter__().__next__(), None)
        obligations = (Positive(first), outside) if subtype == "mixed" else (outside,)
    elif cell == "G5_LINGUISTIC_VARIATION":
        surface = (subtype,)
    elif cell == "S1_APPROVAL":
        obligations = (Positive(first), Positive(second))
        relations = (ApprovalOwns((0, 1) if subtype == "all_approved" else (1,)),)
    elif cell == "S2_NEGATION":
        obligations = (Positive(first), Negated(second))
    elif cell == "S3_CONDITIONAL_BRANCH":
        obligations = (Positive(first), Positive(second)); relations = (ConditionalBranches((0,), (1,), False),)
    elif cell == "S4_UNDO":
        obligations = (Control("undo_last_turn"),); surface = ("undo_only",)
    elif cell == "S5_MIXED_CONTROL":
        obligations = (Control("undo_last_turn"), Positive(first))
    elif cell == "S6_FALSE_ACTION_TRAP":
        obligations = (Outside("financial_transaction", first),)
    return Proposal(slot["proposal_id"], slot["language_tag"], cell, subtype,
                    obligations, relations, surface, freeze_sha)


def panel_fixture() -> dict:
    process_freeze_bytes = (build.HERE / "process.freeze.json").read_bytes()
    freeze_sha = e.digest_bytes(process_freeze_bytes)
    freeze = e.load_json(build.HERE / "process.freeze.json")
    frozen_files = {}
    for name in freeze["local_files"]:
        frozen_files[f"local:{name}"] = (build.HERE / name).read_bytes()
    for name in freeze["generated"]:
        frozen_files[f"generated:{name}"] = (build.HERE / name).read_bytes()
    for name in freeze["source_files"]:
        frozen_files[f"source:{name}"] = (build.REPO / name).read_bytes()

    registry = e.load_json(build.REGISTRY); schema = e.load_json(build.CANONICAL_SCHEMA)
    glossary = e.load_json(build.HERE / "glossary.json"); authority = e.load_json(build.HERE / "authority.json")
    matrix = e.load_json(build.HERE / "pilot_matrix.json"); rubrics = e.load_json(build.HERE / "rubrics.json")
    routes = [name for name, item in glossary["operations"].items()
              if item["authorable"] and "result" in registry["operations"][name]["output_ports"]
              and "primary" in registry["operations"][name]["input_ports"]]
    proposals = [_proposal(slot, index, freeze_sha, routes) for index, slot in enumerate(matrix["slots"])]
    query_panel = {proposal.proposal_id: f"Synthetic isolated utterance {proposal.proposal_id}.".encode()
                   for proposal in proposals}
    expected_cases = {proposal.proposal_id: proposal.language_tag for proposal in proposals}
    rubric_bytes = e.canonical_bytes(rubrics)
    pool_a_people = (ReviewerIdentity("pool-a-1", "pool-a-context-1"),
                     ReviewerIdentity("pool-a-2", "pool-a-context-2"))
    pool_b_people = (ReviewerIdentity("pool-b-1", "pool-b-context-1"),
                     ReviewerIdentity("pool-b-2", "pool-b-context-2"))
    cross = ReviewerIdentity("cross-1", "cross-context-1")
    native = {language: ReviewerIdentity(f"native-{index}", f"native-context-{index}")
              for index, language in enumerate(build.LANGUAGES, 1)}
    records = []; artifacts = {}; cases = []
    for index, proposal in enumerate(proposals, 1):
        case_id = proposal.proposal_id; query = query_panel[case_id]
        projection_value = e.projection_value(proposal, glossary); projection = e.canonical_bytes(projection_value)
        constitution = frozen_files["local:FORMAL_SPEC.md"]
        author_bundle = e.canonical_bytes(e.author_bundle_value(projection_value, constitution))
        proposal_bytes = e.canonical_bytes(e.proposal_value(proposal))
        pool_a_values = tuple(e.pool_a_judgment(identity, proposal) for identity in pool_a_people)
        pool_a = tuple(e.canonical_bytes(value) for value in pool_a_values)
        gold = e.canonical_bytes(e.semantic_gold(proposal, freeze_sha))
        pool_b_values = tuple(e.pool_b_judgment(identity, proposal, query) for identity in pool_b_people)
        pool_b = tuple(e.canonical_bytes(value) for value in pool_b_values)
        role_rows = [
            ("author", ReviewerIdentity(f"author-{index}", f"author-context-{index}"), author_bundle, query, 1),
            *[("pool_a", identity,
               e.canonical_bytes(e.pool_a_bundle_value(proposal, registry, schema, glossary, authority, rubrics)),
               output, 0) for identity, output in zip(pool_a_people, pool_a)],
            *[("pool_b", identity,
               e.canonical_bytes(e.pool_b_bundle_value(proposal, query, registry, schema, glossary, authority, rubrics)),
               output, 0) for identity, output in zip(pool_b_people, pool_b)],
        ]
        for role, identity in (("native_reviewer", native[proposal.language_tag]),
                               ("cross_language_reviewer", cross)):
            input_bytes = e.canonical_bytes(e.review_input_value(role, case_id, query_panel, expected_cases, rubrics))
            output = e.canonical_bytes(e.review_value(role, identity, case_id, proposal.language_tag, query, rubric_bytes))
            role_rows.append((role, identity, input_bytes, output, 0))
        for role, identity, input_bytes, output_bytes, query_count in role_rows:
            records.append(LifecycleRecord(role, identity, case_id, proposal.language_tag,
                e.digest_bytes(input_bytes), e.digest_bytes(output_bytes), query_count, 0, 0, 0, 0))
            artifacts[(role, identity.context_id, case_id)] = (input_bytes, output_bytes)
        cases.append({"case_id": case_id, "proposal_bytes": proposal_bytes, "projection_bytes": projection,
            "author_bundle_bytes": author_bundle, "gold_bytes": gold, "pool_a_bytes": pool_a,
            "pool_b_bytes": pool_b, "envelope": None})
    lifecycle_bytes = e.canonical_bytes(e.lifecycle_value(tuple(records)))
    for item in cases:
        item["envelope"] = e.envelope_value(
            proposal_bytes=item["proposal_bytes"], process_freeze_sha256=freeze_sha,
            projection_bytes=item["projection_bytes"], author_bundle_bytes=item["author_bundle_bytes"],
            gold_bytes=item["gold_bytes"], query_bytes=query_panel[item["case_id"]],
            pool_a_bytes=item["pool_a_bytes"], pool_b_bytes=item["pool_b_bytes"],
            rubric_bytes=rubric_bytes, lifecycle_bytes=lifecycle_bytes)
    return {"cases": tuple(cases), "lifecycle_bytes": lifecycle_bytes, "lifecycle_artifacts": artifacts,
            "query_panel": query_panel, "process_freeze_bytes": process_freeze_bytes,
            "expected_process_freeze_sha256": freeze_sha, "frozen_files": frozen_files}
