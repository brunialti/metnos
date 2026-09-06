"""Projectable pure wire decoder for the canonical legacy-state journal."""
from __future__ import annotations


LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1 = (
    ("sequence", "int"), ("state", "str"), ("intent", "str | None"),
    ("previous_record_sha256", "str | None"), ("request_id", "str"),
    ("policy_sha256", "str"), ("inventory_sha256", "str | None"),
    ("inventory_disposition", "str | None"),
    ("adoption_target_sha256", "str | None"),
    ("authoring_sha256", "str | None"), ("ready_sha256", "str | None"),
    ("record_sha256", "str"),
)
LEGACY_STATE_WIRE_HELPER_CATALOG_V1 = (
    "legacy_state_wire_is_digest_v1",
    "legacy_state_wire_require_schema_v1",
    "legacy_state_wire_require_presence_v1",
    "legacy_state_wire_require_relations_v1",
    "legacy_state_wire_record_v1",
    "legacy_state_wire_records_linked_v1",
    "decode_legacy_state_wire_chain_v1",
)


def legacy_state_wire_is_digest_v1(value, digest_pattern):
    return type(value) is str and digest_pattern.fullmatch(value) is not None


def legacy_state_wire_require_schema_v1(value, profile, invalid):
    if (
        type(value) is not dict or set(value) != profile["record_keys"]
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("protocol") != profile["protocol"]
        or type(value.get("sequence")) is not int
        or not 0 <= value["sequence"] < len(profile["fsm"])
    ):
        raise invalid("record_schema")
    return value["sequence"]


def legacy_state_wire_require_presence_v1(
    value, sequence, profile, digest_pattern, invalid,
):
    fields = (
        "inventory_sha256", "adoption_target_sha256",
        "authoring_sha256", "ready_sha256",
    )
    expected = (sequence >= 1, sequence >= 1, sequence >= 2, sequence >= 3)
    observed = tuple(
        legacy_state_wire_is_digest_v1(value[field], digest_pattern)
        for field in fields
    )
    disposition = value["inventory_disposition"]
    valid_disposition = (
        type(disposition) is str and disposition in profile["dispositions"]
    )
    if (
        observed != expected or (disposition is None) != (sequence == 0)
        or (disposition is not None and not valid_disposition)
    ):
        raise invalid("record_grammar")


def legacy_state_wire_require_relations_v1(
    value, sequence, profile, digest_pattern, invalid,
):
    state, intent = profile["fsm"][sequence]
    previous = value["previous_record_sha256"]
    if (
        value["state"] != state or value["intent"] != intent
        or not legacy_state_wire_is_digest_v1(value["request_id"], digest_pattern)
        or value["policy_sha256"] != profile["policy_sha256"]
        or (previous is None) != (sequence == 0)
        or (
            previous is not None
            and not legacy_state_wire_is_digest_v1(previous, digest_pattern)
        )
        or (
            sequence >= 2
            and value["authoring_sha256"] != value["adoption_target_sha256"]
        )
    ):
        raise invalid("record_grammar")


def legacy_state_wire_record_v1(
    encoded, profile, digest_pattern, decode_canonical, encode_canonical,
    framed_sha256, invalid,
):
    value = decode_canonical(encoded, profile["maximum_record_bytes"])
    sequence = legacy_state_wire_require_schema_v1(value, profile, invalid)
    legacy_state_wire_require_presence_v1(
        value, sequence, profile, digest_pattern, invalid,
    )
    legacy_state_wire_require_relations_v1(
        value, sequence, profile, digest_pattern, invalid,
    )
    unsigned = dict(value)
    unsigned.pop("record_sha256")
    expected_hash = framed_sha256(
        profile["record_domain"], encode_canonical(unsigned),
    )
    if value["record_sha256"] != expected_hash:
        raise invalid("record_hash")
    return value


def legacy_state_wire_records_linked_v1(previous, current):
    return (
        current["sequence"] == previous["sequence"] + 1
        and current["previous_record_sha256"] == previous["record_sha256"]
        and current["request_id"] == previous["request_id"]
        and current["policy_sha256"] == previous["policy_sha256"]
        and (
            previous["sequence"] < 1
            or (current["inventory_sha256"], current["inventory_disposition"])
            == (previous["inventory_sha256"], previous["inventory_disposition"])
        )
        and (
            previous["sequence"] < 2
            or current["authoring_sha256"] == previous["authoring_sha256"]
        )
        and (
            previous["sequence"] < 1
            or current["adoption_target_sha256"]
            == previous["adoption_target_sha256"]
        )
    )


def decode_legacy_state_wire_chain_v1(
    raw, profile, digest_pattern, decode_canonical, encode_canonical,
    framed_sha256, invalid,
):
    if (
        type(raw) is not tuple or not 1 <= len(raw) <= len(profile["fsm"])
        or any(type(item) is not bytes for item in raw)
    ):
        raise invalid("chain_size")
    records = tuple(legacy_state_wire_record_v1(
        item, profile, digest_pattern, decode_canonical, encode_canonical,
        framed_sha256, invalid,
    ) for item in raw)
    if records[0]["sequence"] != 0 or any(
        not legacy_state_wire_records_linked_v1(previous, current)
        for previous, current in zip(records, records[1:])
    ):
        raise invalid("record_chain")
    return records


__all__ = [
    "LEGACY_STATE_WIRE_HELPER_CATALOG_V1",
    "LEGACY_STATE_WIRE_OUTPUT_FIELDS_V1",
    "decode_legacy_state_wire_chain_v1",
    "legacy_state_wire_is_digest_v1",
    "legacy_state_wire_record_v1",
    "legacy_state_wire_records_linked_v1",
    "legacy_state_wire_require_presence_v1",
    "legacy_state_wire_require_relations_v1",
    "legacy_state_wire_require_schema_v1",
]
