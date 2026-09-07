from __future__ import annotations

from dataclasses import replace

import pytest

from executor_birth_identity import (
    ADMISSION_CONTEXT_DOMAIN_V1,
    CANDIDATE_DOMAIN_V1,
    SEMANTIC_CORE_DOMAIN_V1,
    AdmissionContextV1,
    CandidateIdentityInput,
    ContextComponent,
    ExecutorOrigin,
    IdentityError,
    RevisionAuthor,
    admission_context_id,
    candidate_id,
    encode_framed_v1,
    semantic_core_id,
)
from manifest_inventory import ContractId, ManifestOrigin


OBJECTIVE = "sha256:" + "1" * 64
MANIFEST = b'''manifest_format="1.0"\nexecutor_standard="metnos.executor/1.0"\nname="demo"\nversion="1.0.0"\naffinity=["x"]\nrevertible=false\nlifecycle="synthesized"\n[description]\nit="ciao"\nen="hello"\n[code]\nfiles=["demo.py","lib/helper.py"]\ndigest="sha256:''' + b"2" * 64 + b'''"\n[args]\ntype="object"\n[args.properties.value]\ntype="string"\n[args.properties.value.description]\nit="valore"\nen="value"\n[[tests]]\nname="ok"\ninput={value="x"}\nexpect={ok=true}\n'''


def sample(**changes):
    value = CandidateIdentityInput(
        contract_id=ContractId(ManifestOrigin.USER, "demo/manifest.toml"),
        manifest_bytes=MANIFEST,
        language_state_bytes=b'{"schema_version":1,"selectors":{}}',
        code_files={"demo.py": b"print('x')\n", "lib/helper.py": b"X=1\n"},
        executor_origin=ExecutorOrigin.SYNTHESIZED,
        revision_authorship=RevisionAuthor.MODEL,
        objective_hash=OBJECTIVE,
    )
    return replace(value, **changes)


def context():
    values = {name: ContextComponent("v1", "sha256:" + format(i, "064x")) for i, name in enumerate(
        ("standard", "linter", "vocabulary", "authority_registry", "sandbox_registry",
         "property_catalog", "runner", "review_policy", "template_allowlist",
         "primitive_allowlist", "dependency_allowlist"), 1)}
    return AdmissionContextV1(**values)


def test_codec_golden_and_type_separation():
    assert encode_framed_v1({"b": [None, True, 1], "a": "é"}).hex() == (
        "6d0000000000000055000000000000000273000000000000000161"
        "730000000000000002c3a973000000000000000162610000000000000025"
        "00000000000000036e000000000000000062000000000000000101"
        "69000000000000000101"
    )
    assert encode_framed_v1(True) != encode_framed_v1(1)
    assert encode_framed_v1(-129).endswith(b"\xff\x7f")
    assert encode_framed_v1(128).endswith(b"\x00\x80")
    assert encode_framed_v1(1.5).hex() == "6600000000000000083ff8000000000000"
    assert encode_framed_v1(1.0) != encode_framed_v1(1)
    assert encode_framed_v1(-0.0) != encode_framed_v1(0.0)
    assert encode_framed_v1({"a": 1, "b": 2}) == encode_framed_v1({"b": 2, "a": 1})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_codec_rejects_non_finite_floats(value):
    with pytest.raises(IdentityError, match="semantic_core_type_unsupported"):
        encode_framed_v1(value)


def test_domains_are_distinct_and_versioned():
    assert len({CANDIDATE_DOMAIN_V1, SEMANTIC_CORE_DOMAIN_V1, ADMISSION_CONTEXT_DOMAIN_V1}) == 3
    assert all(domain.endswith(b"/v1\0") for domain in (
        CANDIDATE_DOMAIN_V1, SEMANTIC_CORE_DOMAIN_V1, ADMISSION_CONTEXT_DOMAIN_V1))


def test_candidate_and_semantic_core_golden_vectors():
    assert candidate_id(sample()) == (
        "sha256:40a8cc77bcf070c3317756043c7f2326ca8ddad0045f1037af775bd23958053b"
    )
    assert semantic_core_id(sample()) == (
        "sha256:356b5af05e4cfd4d691ae843a1cbc3166f786e6d564dbe240c87c5d482e34ea8"
    )


def test_candidate_is_toml_canonical_but_language_value_sensitive():
    base = candidate_id(sample())
    reformatted = MANIFEST.replace(b'name="demo"', b'name = "demo" # comment')
    assert candidate_id(sample(manifest_bytes=reformatted)) == base
    assert candidate_id(sample(language_state_bytes=b'{ "selectors": {}, "schema_version": 1 }')) == base
    assert candidate_id(sample(language_state_bytes=b'{"schema_version":2,"selectors":{}}')) != base


@pytest.mark.parametrize("field,value", [
    ("executor_origin", ExecutorOrigin.IMPORTED),
    ("revision_authorship", RevisionAuthor.HUMAN),
    ("objective_hash", "sha256:" + "3" * 64),
])
def test_candidate_binds_provenance(field, value):
    assert candidate_id(replace(sample(), **{field: value})) != candidate_id(sample())


def test_candidate_binds_paths_and_bytes_not_mapping_order():
    base = candidate_id(sample())
    reversed_files = dict(reversed(list(sample().code_files.items())))
    assert candidate_id(sample(code_files=reversed_files)) == base
    changed = dict(sample().code_files)
    changed["demo.py"] += b"#"
    assert candidate_id(sample(code_files=changed)) != base


@pytest.mark.parametrize("manifest,files,code", [
    (MANIFEST.replace(b'"demo.py","lib/helper.py"', b'"../demo.py"'), {"../demo.py": b"x"}, "candidate_path_invalid"),
    (MANIFEST, {"demo.py": b"x"}, "candidate_file_missing"),
    (MANIFEST, {**sample().code_files, "extra.py": b"x"}, "candidate_file_extra"),
    (MANIFEST.replace(b'"demo.py","lib/helper.py"', b'"Demo.py","demo.py"'), {"Demo.py": b"x", "demo.py": b"y"}, "candidate_path_invalid"),
    (MANIFEST.replace(b'"demo.py","lib/helper.py"', b'"CON.py"'), {"CON.py": b"x"}, "candidate_path_invalid"),
])
def test_candidate_rejects_bad_envelope(manifest, files, code):
    with pytest.raises(IdentityError, match=code):
        candidate_id(sample(manifest_bytes=manifest, code_files=files))


def test_producer_cannot_supply_birth_block():
    with pytest.raises(IdentityError, match="producer_birth_block"):
        candidate_id(sample(manifest_bytes=MANIFEST + b"\n[birth]\nreceipt_id='x'\n"))


def test_semantic_core_ignores_only_linguistic_surfaces():
    base = semantic_core_id(sample())
    translated = MANIFEST.replace(b'it="ciao"', b'it="salve"').replace(b'it="valore"', b'it="dato"')
    assert semantic_core_id(sample(manifest_bytes=translated)) == base
    assert semantic_core_id(sample(manifest_bytes=MANIFEST.replace(b'name="demo"', b'name="other"'))) != base
    assert semantic_core_id(sample(manifest_bytes=MANIFEST.replace(b'type="string"', b'type="integer"'))) != base


def test_semantic_core_accepts_and_binds_nested_schema_string_description():
    nested = MANIFEST.replace(
        b"[[tests]]",
        b'''[args.properties.value.properties.nested]
type="string"
description="technical nested description"
[[tests]]''',
    )
    changed = nested.replace(b"technical nested description", b"changed nested description")
    assert semantic_core_id(sample(manifest_bytes=nested)) != semantic_core_id(
        sample(manifest_bytes=changed)
    )


@pytest.mark.parametrize("description", [b"description=7\n", b"description=[\"x\"]\n"])
def test_semantic_core_rejects_invalid_nested_schema_description(description):
    invalid = MANIFEST.replace(
        b'''[args.properties.value.description]\nit="valore"\nen="value"\n''',
        description,
    )
    with pytest.raises(IdentityError, match="semantic_core_unknown_field"):
        semantic_core_id(sample(manifest_bytes=invalid))


def test_semantic_core_rejects_non_string_localized_schema_description():
    invalid = MANIFEST.replace(b'it="valore"', b"it=7")
    with pytest.raises(IdentityError, match="semantic_core_unknown_field"):
        semantic_core_id(sample(manifest_bytes=invalid))


def test_semantic_core_accepts_and_binds_paired_device_identity_contract():
    declared = MANIFEST.replace(
        b'type="string"\n[args.properties.value.description]',
        b'type="string"\npaired_device_identity="id"\n'
        b'paired_device_identity_mode="exact"\n'
        b'[args.properties.value.description]',
    )
    assert semantic_core_id(sample(manifest_bytes=declared)) != semantic_core_id(sample())


def test_semantic_core_is_fail_closed_for_unknown_and_unsupported_types():
    with pytest.raises(IdentityError, match="semantic_core_unknown_field"):
        semantic_core_id(sample(manifest_bytes=MANIFEST + b"\nunknown_technical='x'\n"))
    dated = MANIFEST.replace(b'version="1.0.0"', b'version=1979-05-27T07:32:00Z')
    with pytest.raises(IdentityError, match="semantic_core_type_unsupported"):
        semantic_core_id(sample(manifest_bytes=dated))


def test_candidate_and_semantic_core_accept_and_bind_finite_manifest_floats():
    integral = MANIFEST.replace(b"input={value=\"x\"}", b"input={value=1}")
    fractional = MANIFEST.replace(b"input={value=\"x\"}", b"input={value=1.0}")
    assert candidate_id(sample(manifest_bytes=integral)) != candidate_id(
        sample(manifest_bytes=fractional)
    )
    assert semantic_core_id(sample(manifest_bytes=integral)) != semantic_core_id(
        sample(manifest_bytes=fractional)
    )


@pytest.mark.parametrize("literal", [b"nan", b"+inf", b"-inf"])
def test_manifest_identity_rejects_non_finite_floats(literal):
    manifest = MANIFEST.replace(b"input={value=\"x\"}", b"input={value=" + literal + b"}")
    with pytest.raises(IdentityError, match="semantic_core_type_unsupported"):
        candidate_id(sample(manifest_bytes=manifest))


def test_semantic_core_preserves_absent_empty_array_order_and_unicode():
    base = semantic_core_id(sample())
    assert semantic_core_id(sample(manifest_bytes=MANIFEST.replace(b'affinity=["x"]', b'affinity=[]'))) != base
    assert semantic_core_id(sample(manifest_bytes=MANIFEST.replace(b'affinity=["x"]', b'affinity=["x","y"]'))) != base
    assert semantic_core_id(sample(manifest_bytes=MANIFEST.replace(b'name="demo"', 'name="démo"'.encode()))) != base


def test_admission_context_golden_and_every_component_matters():
    baseline = admission_context_id(context())
    assert baseline == "sha256:0263d3f9ec24a2eb5dcb3e7e9f761fd96659cedec218577e732f596b96eb3105"
    for name in context().__dataclass_fields__:
        changed = replace(context(), **{name: ContextComponent("v2", getattr(context(), name).digest)})
        assert admission_context_id(changed) != baseline


@pytest.mark.parametrize("version,digest", [("", "sha256:" + "0" * 64), ("v1", "0" * 64), ("v1", "sha256:" + "A" * 64)])
def test_context_component_is_closed(version, digest):
    with pytest.raises(IdentityError):
        ContextComponent(version, digest)
