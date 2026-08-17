#!/usr/bin/env python3
"""Recompute integrity and counts for the frozen semantic-sentinel review."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
SENTINEL_INDICES = (18, 25, 30, 59, 65, 66, 69, 77, 79, 83)


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def file_sha256(name: str) -> str:
    return sha256((HERE / name).read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def row_projection(row: dict) -> dict:
    return {key: row[key] for key in ("frame", "valid", "reason")}


def flatten_claude(document: dict) -> list[dict]:
    rows: list[dict] = []
    for item in document["esito_per_coppia"]:
        if "bracci" in item:
            arm_results = item["bracci"]
        else:
            arm_results = {
                arm: item["esito"]
                for arm in ("braccio_1", "braccio_2", "braccio_3")
            }
        for arm, result in arm_results.items():
            rows.append(
                {
                    "indice": item["indice"],
                    "braccio": arm,
                    "semanticamente_fedele": result["semanticamente_fedele"],
                    "fail_closed": result["fail_closed"],
                    "mutazione_non_richiesta": result["mutazione_non_richiesta"],
                }
            )
    return sorted(rows, key=lambda row: (row["indice"], row["braccio"]))


def flatten_codex(document: dict) -> list[dict]:
    rows: list[dict] = []
    for item in document["adjudicazioni"]:
        rows.append(
            {
                "indice": item["indice"],
                "braccio": item["braccio"],
                "semanticamente_fedele": item["semanticamente_fedele"]["valore"],
                "fail_closed": item["fail_closed"]["valore"],
                "mutazione_non_richiesta": item["mutazione_non_richiesta"]["valore"],
            }
        )
    return sorted(rows, key=lambda row: (row["indice"], row["braccio"]))


def field_counts(rows: list[dict]) -> dict:
    fields = (
        "semanticamente_fedele",
        "fail_closed",
        "mutazione_non_richiesta",
    )
    return {
        field: dict(sorted(Counter(str(row[field]) for row in rows).items()))
        for field in fields
    }


def field_counts_by_arm(rows: list[dict]) -> dict:
    arms = ("braccio_1", "braccio_2", "braccio_3")
    return {
        arm: field_counts([row for row in rows if row["braccio"] == arm])
        for arm in arms
    }


def main() -> int:
    registry = load("sentinelle_semantiche.json")
    blind = load("sentinelle_estratto_cieco.json")
    mapping = load("sentinelle_mappatura_SIGILLATA.json")
    codex = load("adjudicazione_colonna_codex.json")
    claude = load("adjudicazione_colonna_claude.json")
    raw_c10 = load("prova_specchi_riparo_c10.json")
    raw_c11 = load("prova_specchi_riparo_c11.json")

    sealed = mapping["integrita_al_momento_del_sigillo"]
    previous_integrity = registry["revisioni"][0]["integrita_precedente"]
    sealed_checks = {
        # Il registro 1.1 conserva qui le impronte della versione 1.0 sigillata.
        # Confrontare il file corrente col sigillo storico sarebbe un falso errore.
        "sentinelle_semantiche_file_sha256": previous_integrity["sha256_file"],
        "sentinelle_estratto_cieco_file_sha256": file_sha256(
            "sentinelle_estratto_cieco.json"
        ),
        "adjudicazione_colonna_codex_file_sha256": file_sha256(
            "adjudicazione_colonna_codex.json"
        ),
        "prova_specchi_riparo_c10_file_sha256": file_sha256(
            "prova_specchi_riparo_c10.json"
        ),
        "prova_specchi_riparo_c11_file_sha256": file_sha256(
            "prova_specchi_riparo_c11.json"
        ),
    }
    for key, actual in sealed_checks.items():
        assert sealed[key] == actual, (key, sealed[key], actual)

    registry_for_hash = json.loads(json.dumps(registry))
    declared_registry_hash = registry_for_hash["integrita"].pop(
        "sha256_registro"
    )
    actual_registry_hash = canonical_sha256(registry_for_hash)
    assert declared_registry_hash == actual_registry_hash
    assert (
        sealed["sentinelle_semantiche_payload_sha256"]
        == previous_integrity["sha256_registro_canonico"]
    )

    barrier_review = registry["adjudicazione_barriera_degradata"]
    assert barrier_review["numero_coppie"] == 30
    assert barrier_review["valori"] == {"true": 12, "false": 18}
    assert barrier_review["indici_true"] == [30, 59, 69, 77]
    assert all(
        row["valore_nei_tre_bracci"] == (row["indice"] in {30, 59, 69, 77})
        for row in barrier_review["per_indice"]
    )
    reconciliation = registry["riconciliazione_giro_b"]
    assert reconciliation["celle"] == 30
    assert reconciliation["accordi"] == 29
    assert reconciliation["disaccordi"] == 1
    assert reconciliation["unico_disaccordo"] == {
        "indice": 69,
        "proprieta": "mutazione_non_richiesta",
        "bracci": ["braccio_1", "braccio_2", "braccio_3"],
        "codex": False,
        "claude": True,
        "risoluzione": False,
        "motivazione": (
            "L'aggiornamento subordinato non e esposto come mutazione libera. "
            "send/messages sostituisce pero il cancello bloccante e viene quindi "
            "giudicato da barriera_degradata."
        ),
    }

    queries = raw_c10["queries"]
    assert queries == raw_c11["queries"]
    sample_payload = json.dumps(queries, ensure_ascii=False).encode("utf-8")
    sample_hash = sha256(sample_payload).hexdigest()
    assert sample_hash == raw_c10["sample_sha256"] == raw_c11["sample_sha256"]
    assert sample_hash == registry["campione"]["sha256_lista_richieste"]
    assert sample_hash == blind["campione"]["sha256_lista_richieste"]

    sentinels = registry["sentinelle"]
    assert tuple(item["indice"] for item in sentinels) == SENTINEL_INDICES
    for item in sentinels:
        text = queries[item["indice"]]
        assert item["testo"] == text
        assert item["sha256_testo"] == sha256(text.encode("utf-8")).hexdigest()

    control_c10 = raw_c10["arms"]["a_plus_role_repair_control"]["rows"]
    control_c11 = raw_c11["arms"]["a_plus_role_repair_control"]["rows"]
    assert len(control_c10) == len(control_c11) == 120
    control_differences = {
        field: [
            index
            for index, (left, right) in enumerate(zip(control_c10, control_c11))
            if left[field] != right[field]
        ]
        for field in (
            "valid",
            "reason",
            "routes",
            "roles",
            "frame",
            "completion_tokens",
            "finish_reason",
        )
    }
    assert not any(control_differences.values()), control_differences

    raw_arms = {
        "braccio_1": raw_c11["arms"]["c11"]["rows"],
        "braccio_2": control_c10,
        "braccio_3": raw_c10["arms"]["c10"]["rows"],
    }
    blind_cases = {item["indice"]: item for item in blind["casi"]}
    assert tuple(blind_cases) == SENTINEL_INDICES
    for index, case in blind_cases.items():
        assert [arm["etichetta"] for arm in case["bracci"]] == [
            "braccio_1",
            "braccio_2",
            "braccio_3",
        ]
        for arm in case["bracci"]:
            expected = row_projection(raw_arms[arm["etichetta"]][index])
            assert {key: arm[key] for key in expected} == expected

    identical_indices = []
    different_indices = []
    identical_predicate_indices = []
    different_predicate_indices = []
    for index, case in blind_cases.items():
        projections = [
            {key: arm[key] for key in ("frame", "valid", "reason")}
            for arm in case["bracci"]
        ]
        target = (
            identical_indices
            if projections[0] == projections[1] == projections[2]
            else different_indices
        )
        target.append(index)
        predicates = [arm["frame"].get("predicates", []) for arm in case["bracci"]]
        predicate_target = (
            identical_predicate_indices
            if predicates[0] == predicates[1] == predicates[2]
            else different_predicate_indices
        )
        predicate_target.append(index)

    codex_rows = flatten_codex(codex)
    claude_rows = flatten_claude(claude)
    assert len(codex_rows) == len(claude_rows) == 30
    assert [
        (row["indice"], row["braccio"]) for row in codex_rows
    ] == [
        (row["indice"], row["braccio"]) for row in claude_rows
    ]

    fields = (
        "semanticamente_fedele",
        "fail_closed",
        "mutazione_non_richiesta",
    )
    disagreements = []
    for codex_row, claude_row in zip(codex_rows, claude_rows):
        for field in fields:
            if codex_row[field] != claude_row[field]:
                disagreements.append(
                    {
                        "indice": codex_row["indice"],
                        "braccio": codex_row["braccio"],
                        "campo": field,
                        "codex": codex_row[field],
                        "claude": claude_row[field],
                    }
                )

    result = {
        "integrita": {
            "impronte_sigillate_verificate": len(sealed_checks) + 1,
            "sha256_registro_corrente": actual_registry_hash,
            "sha256_campione": sample_hash,
            "richieste": len(queries),
            "sentinelle": len(sentinels),
            "coppie_indice_braccio": len(codex_rows),
        },
        "controlli_freschi": {
            "campi_con_divergenze": control_differences,
        },
        "estratto_cieco": {
            "indici_identici_fra_i_tre_bracci": identical_indices,
            "indici_diversi_fra_i_tre_bracci": different_indices,
            "predicati_fase_2_identici_fra_i_tre_bracci": (
                identical_predicate_indices
            ),
            "predicati_fase_2_diversi_fra_i_tre_bracci": (
                different_predicate_indices
            ),
        },
        "conteggi_codex": field_counts(codex_rows),
        "conteggi_claude": field_counts(claude_rows),
        "conteggi_per_braccio_codex": field_counts_by_arm(codex_rows),
        "conteggi_per_braccio_claude": field_counts_by_arm(claude_rows),
        "mutazione_non_richiesta_indici": {
            "codex": sorted(
                {row["indice"] for row in codex_rows if row["mutazione_non_richiesta"] is True}
            ),
            "claude": sorted(
                {row["indice"] for row in claude_rows if row["mutazione_non_richiesta"] is True}
            ),
        },
        "barriera_degradata": {
            "coppie_true": barrier_review["valori"]["true"],
            "coppie_false": barrier_review["valori"]["false"],
            "indici_true": barrier_review["indici_true"],
        },
        "riconciliazione_giro_b": {
            "celle": reconciliation["celle"],
            "accordi": reconciliation["accordi"],
            "disaccordi": reconciliation["disaccordi"],
            "unico_disaccordo": reconciliation["unico_disaccordo"],
        },
        "differenze_formali_negli_artefatti_ciechi_originari": {
            "celle": len(disagreements),
            "indici": sorted({item["indice"] for item in disagreements}),
            "dettaglio": disagreements,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
