"""Immutable Birth exception classes and per-scope grants."""
from __future__ import annotations

from dataclasses import dataclass

from contract_boundary_policy_types import (
    ContractBoundaryPolicyError,
    canonical_names_v1,
    closed_names_v1,
    require_scope_key_v1,
    require_text_v1,
)


@dataclass(frozen=True, slots=True)
class BirthExceptionGrantV1:
    scope: str
    exception: str
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        require_scope_key_v1(self.scope, field="exception_scope")
        require_text_v1(self.exception, field="exception_class")
        canonical_names_v1(self.capabilities, field="exception_capabilities")

BIRTH_EXCEPTION_CLASSES_V1 = (
    'localization_only',
    'retirement_only',
    'offline_nonproductive_authoring',
)

BIRTH_EXCEPTION_GRANTS_V1 = (
    BirthExceptionGrantV1(
        'runtime/admin/manifest_refactor.py:<module>',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/admin/manifest_refactor.py:main',
        'offline_nonproductive_authoring',
        ('authoring_read', 'authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/admin/manifest_refactor.py:refactor_manifest',
        'offline_nonproductive_authoring',
        ('authoring_read', 'authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/i18n_pipeline.py:live_contract_context',
        'localization_only', ('publish_localization', 'verified_store_read'),
    ),
    BirthExceptionGrantV1(
        'runtime/i18n_translator.py:<module>',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/i18n_translator.py:_align_one_manifest',
        'offline_nonproductive_authoring',
        ('authoring_read', 'authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/i18n_translator.py:align_manifest_descriptions',
        'offline_nonproductive_authoring',
        ('authoring_read', 'authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/manifest_normalize.py:<module>',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/manifest_normalize.py:apply_one',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/manifest_normalize.py:main',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/migrate_manifest_descriptions.py:<module>',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/migrate_manifest_descriptions.py:main',
        'offline_nonproductive_authoring', ('authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/migrate_manifest_descriptions.py:migrate_dirs',
        'offline_nonproductive_authoring',
        ('authoring_read', 'authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/migrate_manifest_descriptions.py:migrate_one',
        'offline_nonproductive_authoring',
        ('authoring_read', 'authoring_write', 'sign'),
    ),
    BirthExceptionGrantV1(
        'runtime/change_rollback.py:_rollback_create_executor',
        'retirement_only', ('retire',),
    ),
    BirthExceptionGrantV1(
        'runtime/cli/skills_cli.py:_cmd_uninstall',
        'retirement_only', ('authoring_read', 'retire'),
    ),
)

def _validate_grants_v1() -> None:
    classes = frozenset(closed_names_v1(
        BIRTH_EXCEPTION_CLASSES_V1, field="exception_classes",
    ))
    scopes = tuple(grant.scope for grant in BIRTH_EXCEPTION_GRANTS_V1)
    closed_names_v1(scopes, field="exception_scopes")
    if any(grant.exception not in classes for grant in BIRTH_EXCEPTION_GRANTS_V1):
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:exception_class_reference"
        )

_validate_grants_v1()

__all__ = [
    "BIRTH_EXCEPTION_CLASSES_V1", "BIRTH_EXCEPTION_GRANTS_V1",
    "BirthExceptionGrantV1",
]
