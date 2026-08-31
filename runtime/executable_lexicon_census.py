"""Executable census for natural-language tables in runtime consumers.

RM-0005 does not prohibit protocol identifiers, file extensions or structural
grammars.  It does prohibit private IT/EN word tables that silently bypass the
versioned detection lexicon.  This AST guard keeps that boundary reviewable:
every surviving literal with a linguistic role is either rejected or listed
below as a typed technical invariant with a concrete reason.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class TechnicalInvariant:
    symbol: str
    kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class CensusIssue:
    path: str
    line: int
    symbol: str
    code: str
    message: str


# Consumer modules explicitly inspected during the RM-0005 reopening.  This
# list remains useful as review documentation; the executable gate discovers
# every runtime module, so a new consumer cannot escape merely by not being
# added here.
CONSUMER_PATHS = (
    "prefilter.py",
    "route_disambiguation.py",
    "compound_decomposer.py",
    "engine/dispatch.py",
    "prefilter_strategies/trie_v2.py",
    "prefilter_strategies/token_flat_v2.py",
    "prefilter_rules.py",
    "adaptive_rerank.py",
    "ordering_clause.py",
    "time_window_resolver.py",
    "time_window_parser.py",
    "recurring_tasks.py",
    "args_extractor.py",
    "fast_path.py",
    "target_device.py",
    "backend_resolver.py",
    "calendar_resolver.py",
    "mail_account_resolver.py",
    "self_recipient_resolver.py",
    "from_contains_resolver.py",
    "read_format_resolver.py",
    "photon_client.py",
    "agent_runtime.py",
    "playwright_sidecar/factor_resolvers.py",
    "credential_intake.py",
    "telos_lenses/_base.py",
    "store_entries.py",
    "system/admin.py",
    "skill_codegen.py",
    "playwright_sidecar/action_resolver.py",
    "skill_admin.py",
)

_DISCOVERY_EXCLUDED_PATHS = frozenset({
    "detection_lexicon.py",
    "executable_lexicon_census.py",
})
_DISCOVERY_EXCLUDED_DIRS = frozenset({"tests", "__pycache__"})

# Seed modules are deliberately excluded by exact relative path, never by a
# filename prefix.  A newly added ``detection_lexicon_seed_*.py`` therefore is
# a consumer until this list is reviewed, while syntax errors in these known
# sources are audited separately by ``scan_runtime``.
KNOWN_DETECTION_SEED_PATHS = frozenset({
    "detection_lexicon_seed.py",
    "detection_lexicon_seed_args.py",
    "detection_lexicon_seed_codegen.py",
    "detection_lexicon_seed_dialog.py",
    "detection_lexicon_seed_geo.py",
    "detection_lexicon_seed_parsers.py",
    "detection_lexicon_seed_reconciliation.py",
    "detection_lexicon_seed_residual_am.py",
    "detection_lexicon_seed_residual_nz.py",
    "detection_lexicon_seed_resolvers.py",
    "detection_lexicon_seed_routing.py",
    "detection_lexicon_seed_runtime_safety.py",
    "detection_lexicon_seed_security.py",
})


# The broadened gate detector also sees thousands of pre-existing wire/schema
# comparisons.  They are admitted only under the exact module AST and exact
# per-operation cardinality observed by the closing review.  Any source edit,
# added gate, copied exception or operation-shape change invalidates authority.
LEGACY_LITERAL_GATE_FILE_AUTHORITIES: Mapping[str, tuple[str, tuple[tuple[str, int], ...]]] = {
    'active_sessions.py': ('db0242202cba9a0e3ec331eba33cf178e185fcd1380ec9bd04a75c2365b7dbf1', (('comparison', 2),)),
    'adaptive_rerank.py': ('04cdfb013c155f2e0105a65f0b29abf3eda3ec1acb7759e5cd0c0e949cfba0ff', (('comparison', 1),)),
    'admin/i18n_cli.py': ('0c2932caac0b2ec2f483f5f6956ff78addaff89d9feafb57e9edcf5474eee2cd', (('comparison', 11), ('helper-argument', 3))),
    'admin/i18n_migrate_manifests.py': ('6dd6324ff6fe3d014e9f007d7ccb2f4492c46b3fbaa4d6e726007f48e3bd7696', (('comparison', 4),)),
    'admin/i18n_migrate_v2.py': ('6814db2316e10e887723b538e46a6f7ea12cc8a88876a335510477ca80c0f861', (('comparison', 5),)),
    'admin/manifest_refactor.py': ('343618185cfa642aa3b68ee2d0048688e060f9478e3751f97abcca0fb0bfea56', (('comparison', 5),)),
    'admin/promotions_cli.py': ('1604091961ff82d3a2c3acaf556e5d631f2949fffba0749bf60b7fdea4747165', (('comparison', 1), ('helper-argument', 2))),
    'admin/promotions_review.py': ('563546bb585ec25e6167687e702045f792e3830524e77ef664b3b6b0ab07e216', (('comparison', 20), ('helper-argument', 2))),
    'admin/prompts_cli.py': ('7d6fa3114bafc8c70d26a9c801e44377b459b1c57a3bc47e369a50d72083001f', (('comparison', 29), ('helper-argument', 4))),
    'admin/proposals_cli.py': ('08513770af795f8a45c5c5b5fc08b750db077aef2728a0311b0093016d7073b9', (('comparison', 11), ('helper-argument', 8))),
    'admin/scheduler_cli.py': ('97fe956403b25bcdae157bb885df716bd95985d7d0c1a8acaded33842c694547', (('comparison', 2), ('helper-argument', 2))),
    'admin_chat_commands.py': ('23fcdf64527453538b5c030e36c2966c665e96abf4d35437f487a426318bbb65', (('comparison', 11),)),
    'admitted_module_v1.py': ('e2544f6735f95ee4d7074b37d0d9cffa9073f555ee296e4db99abf30edb7d2c0', (('comparison', 2), ('helper-argument', 1))),
    'agent_mirror.py': ('870f4556a8cd0bcc06eeda1d834549ecfb560fdd5eb3dee87b0170f9d63fe875', (('helper-argument', 4),)),
    'agent_runtime.py': ('a8fe05852d5baa068429d626c325b779781583d8b9ddc81f1adb3c9bddbc5f9c', (('comparison', 121), ('helper-argument', 1))),
    'agent_server.py': ('8dfbea0437a6c294887ddf0c29efba0319b94852a6fe512be89eab22006c9077', (('comparison', 11), ('helper-argument', 5))),
    'agentic_executor.py': ('8320cd919ebc831aba7deade7477217ce278367974f4d65bb1cc0b7fe8a826f3', (('comparison', 3),)),
    'alignment_engine.py': ('ae8b5551fc8590e1a6bfeda9436b312c8e854d1728921d46fd699175a0484bb7', (('comparison', 6),)),
    'approval_registry.py': ('0df7fa771a236811e290b50f83ef4663e1a1b27a8d804793892206200dcfb6ec', (('comparison', 4),)),
    'arg_provenance.py': ('46e441d5c90575aa292e5659b0db84e9d82a83a45c96d33236160fe9df28026b', (('comparison', 3),)),
    'args_extractor.py': ('e810403aeb7ca79f97f9ea53024e1c6819fbd4bdac9b33290a4f17f8925408a3', (('comparison', 7), ('helper-argument', 1))),
    'audit/audit_verb.py': ('fd9a89c88d1728dae606448975405e9ac7311fad73adfbae6da281e2988e121a', (('comparison', 1),)),
    'audit/queries.py': ('759e6257659456618a22ae35071eafcad13bf8b47cce62f884ad2b67874e2302', (('comparison', 13), ('helper-argument', 1))),
    'audit_jsonl.py': ('3b564cd238d64a07761bbc7244c7613035a39730df6efdd2b9427d8f7c2b40b9', (('comparison', 3),)),
    'auto_remediation.py': ('d939cdcb6407117b37afc261a250f8ff676838241eea8044de73f0575adb0b39', (('comparison', 2),)),
    'backend_resolver.py': ('9507f4576f2cda3ea36cedbc248408b5e20a95c5ea5fc0e7773c0a21c70dd64c', (('comparison', 3),)),
    'backends/_google_api_runner.py': ('e4f52c87c2c03b1940d73ce3f17538ca1d737f2672e4f653015f92288022b6dd', (('comparison', 1),)),
    'backends/_google_auth_common.py': ('dbf1f391a57529c58292109acdc070820ae59a9e54348480e5ef7ebb3f508ab1', (('comparison', 2),)),
    'backends/contacts/google_workspace.py': ('4ecb8eb4b89926ce9f696865e76079e7cbe18764ab8c0a2e789c1f7f359c95e4', (('comparison', 2),)),
    'backends/events/google_workspace.py': ('96d22f3e817496ab5fbce9a0531b3c047213fd215bbf5000112a4d40076d8cd0', (('comparison', 11), ('helper-argument', 1))),
    'backends/events/local_ics.py': ('d2bcbb93c5c96874d824aa21b55441d0603dbb8816c028df32e375efc3c34b00', (('comparison', 2), ('helper-argument', 13))),
    'backends/files/google_workspace.py': ('92f31fe38a5b5372216e4cb87f483c42f1b32d8a7073ca7cabbafa7260a4b8d8', (('comparison', 28),)),
    'backends/files/local.py': ('047dfbeac0aa460515e0706428f4a4f935c228a2e55c9508823087c482fad9b9', (('comparison', 43), ('helper-argument', 1))),
    'backends/images/google_photos.py': ('c90b2123638dc43279e39f087f6286694aea9f37b8b1a6d185f81024400acfc8', (('comparison', 8), ('helper-argument', 2))),
    'backends/images/google_vision.py': ('4fcee6e6eb572f690895f28cc8727759d464bcd90838e6af8bea90edf0c26c60', (('comparison', 6), ('helper-argument', 2))),
    'backends/images/searxng.py': ('457d65f6425d626373e526302acb0309801b4695fc553bf0b35019029c41bd5a', (('comparison', 2), ('helper-argument', 2))),
    'backends/messages/email_metnos.py': ('6711563d061dbf441b5a83301826902e05f584894cc89f275c17c1bebd211d32', (('comparison', 28), ('helper-argument', 3))),
    'backends/messages/gmail_google_workspace.py': ('8a05cf4d57a30c4356608607ee3506a5636eb28789757aec877d65383fb03f8a', (('comparison', 12), ('helper-argument', 1))),
    'backends/urls/httpx_default.py': ('21a589701a39e33585e4d044ce98d354cd1c576219473cf27a33fb540de3862a', (('comparison', 3),)),
    'bge_embedding.py': ('678621dc0c3780c6f2281a46a922bcc1c7783363bb1fda2877642f902e8b4ca0', (('comparison', 2), ('helper-argument', 3))),
    'bounded_subprocess.py': ('b30b7a3201d25ad8091a78edea9043da935370331c4e9459b00566f3bc560b27', (('comparison', 3),)),
    'build_orchestrator.py': ('e5313617f8ea50b0da6dc7e1342f656c2fb832437b8cfbad3a4d86c3d42ccd65', (('comparison', 3),)),
    'build_runner.py': ('dc9158bdcbcb6fb2f9d814755389ac580fb00865613adc0805ad349bf6a32f92', (('comparison', 2),)),
    'build_runner_unified.py': ('15be7355a032efab4b3ffd5e79ec842111a1d5230aae5a71bf773a840b3107e5', (('comparison', 2),)),
    'calendar_resolver.py': ('ca5870a48f1e9241f906237adb80ae7543b3fc6cf8469a8c3d49d479c8d529ba', (('comparison', 4),)),
    'calibration_check.py': ('1372bc7e3adcb29f17155e1cc0a9f36501df90568a38129268444e09462b0afb', (('comparison', 1),)),
    'capabilities.py': ('74669c913fa3e6e29f1f9d545d8a2cd3192442723c0d0145adc84ef88cc26fcc', (('comparison', 11),)),
    'change_applier.py': ('101b1a9388194a9b7a7cb12e5316f35d54ce856afe6f5417b29dd26834c66d96', (('comparison', 2),)),
    'change_applier_extend.py': ('e7d6b4979524f60c2aac6cf71988080c195da012a63fc21261d44fbb95b7108d', (('comparison', 2),)),
    'change_intent_adapters/_base.py': ('f6f87e2831c8a9ee8eeecc3b7589a40920c6a50d90a2ee543bf80a5e1edc7c6d', (('comparison', 1),)),
    'change_intent_adapters/introvertiva.py': ('84cfbe8a3135f24c7a1a0965b06e2a125fcfaca07bbea9326ffe3e3320a08d5b', (('comparison', 3),)),
    'change_intent_adapters/synt.py': ('02c79b3697c08958d183df4b394d9919941078de23930ac9a101174a009e91eb', (('comparison', 3),)),
    'change_intent_adapters/telos.py': ('d7bcce89b9a6641cc306e284e45a8f1bc1cf4c55763a4bd59ca3e10506eeaa0f', (('comparison', 2),)),
    'change_intent_adapters/user_feedback.py': ('431a85bce727545fed6c5db14cb4461b8f3fc473bd7cc7fe4afe462ed3a18276', (('comparison', 2),)),
    'change_intents.py': ('d868d24d1d2ffda5858de56a6dfae61fd6ee39d618665729416f2735aa313f56', (('comparison', 6), ('helper-argument', 1))),
    'change_observer.py': ('e532895256c39468bb2f379537e20639c4109f8e37c7210320779cb72f0b91a2', (('comparison', 12),)),
    'change_rollback.py': ('a0b36a9811066618e6d68001f9d70d0fac51909ea0d71425912ac1323f5fdb66', (('comparison', 2),)),
    'channels/approval.py': ('5f3e8f37e8be1611fc3193b95560574d3fcdd76d64fb5233a571a7043b6aa99b', (('comparison', 2),)),
    'channels/daemon.py': ('bed4ded9502e4bced04081f8d5612cafcb47c63fda51c56aaca74ec31ae240cf', (('comparison', 70), ('helper-argument', 1))),
    'channels/inline_ui.py': ('e8f694fb962d54f6f535a96b87c6716f8f5b64adf49a52bc88c780105f14587a', (('comparison', 8),)),
    'channels/telegram.py': ('60d93064b836f6a4c607b31124d2ad20d1ec10a30ae4b6a1a1d9db91f71c1c3c', (('comparison', 3),)),
    'chat_target_store.py': ('f2ffa6957ac16260a636d62bfb51c1a1d0efeaa9a08b5e46d47645ef3b31ea03', (('helper-argument', 1),)),
    'classify_entries.py': ('12840852c77688d534dfee981ea3114116cc649fbf77ef1b18e49a83e0746f85', (('comparison', 12),)),
    'cli/detection_cli.py': ('cbd92651f4b73a33d227af01cdfb0a76c7f613f5d453a942ab46c2a82174ab53', (('comparison', 5),)),
    'cli/skills_cli.py': ('95eb00cbc38ba9c41005b11833771e8fd1e8f20adf6d6a6ea834491ad1abee0a', (('comparison', 5),)),
    'clip_embedding.py': ('8d9eaa555e58a1737a871da1bef8664367ded5976ea5b6a1efa499ea2a813034', (('comparison', 4), ('helper-argument', 4))),
    'code_file_paths.py': ('dca1a2cf01b942db8c10faa0afb555baf635c035022f332dd150adc465058972', (('comparison', 1), ('helper-argument', 1))),
    'compare_entries.py': ('ffc00edddb2c8ac4f9cc9227a90b824db0aec4a0af6e290eda791a2891414cbb', (('comparison', 1),)),
    'compound_decomposer.py': ('256070a1b292e58eb3ba1d63283a77bb11f9c20c836c4c574aeeefb60a88a178', (('comparison', 4),)),
    'config.py': ('257caa81f45083ad220824046694021f44f75a2f60e718374cbe80ebc9dee8ee', (('comparison', 13), ('helper-argument', 1))),
    'contract_boundary_guard.py': ('1f6381b23b8ffb3184e0a9212adbdd716941b48e3978864c26ad3dffd7b1fcb5', (('comparison', 86),)),
    'contract_cutover_guard.py': ('39849f02e7854d0843edc10d20c528fffffce6d100276de81d069932d72e1759', (('comparison', 2),)),
    'contract_store.py': ('cb092abbc1c9d3f283183185b6a5b1e80199c98956313b9c15e7273f94428d57', (('comparison', 32), ('helper-argument', 3))),
    'credential_mandates.py': ('6f52143c2c23ef03146e16bb988c49b32745aee2e82ace13c7d2ff3f27be02a7', (('comparison', 5),)),
    'credentials.py': ('52f921e330c808c66bfcf39c72b50f0d4c5f795607520cd268d43f56dd760673', (('comparison', 1),)),
    'credentials_migrate.py': ('e0dbf9d6693b9b6559e9eb988607e76f6bbb74621a368915537c3393958238e4', (('comparison', 2), ('helper-argument', 1))),
    'deferred_turns.py': ('a9ff73ed421cf6661e69cc5da3e496b16580bdc16679cd7b5058501b5c087031', (('comparison', 2),)),
    'describe_entries.py': ('03ba293f70ab406a020fd48e31bf63b9a12a43af8401f9b86fa6fc56b18d238a', (('comparison', 23), ('helper-argument', 3), ('iteration', 1), ('literal-lookup', 1), ('membership', 1), ('prefix-suffix', 1))),
    'device_shim/gen_i18n.py': ('ebbc85788a89f4b6dc303c3c468b626778ae5ece17a7f8ce96c8a855fbfd8a48', (('comparison', 1),)),
    'devices.py': ('f6da756628309ed59ceb5987c0757d3398d5fb1c90ba01712cc9b5900257253a', (('comparison', 18), ('helper-argument', 1))),
    'dialog_pending.py': ('3a12264481ee2976fed3b0b4ff4de53dc7757971097303f1cc5967d7db8f335d', (('comparison', 2),)),
    'dialog_preview.py': ('83426b740d05373eb4c52088ad7bff3c240e1cc2d087bc603881d5fc80781d25', (('comparison', 1),)),
    'durable_workloads/artifacts.py': ('e9a8329f6715dc17e7a5e485d350a0678bdda4f97e4498ceb87455186ba623ba', (('comparison', 4),)),
    'durable_workloads/compiler.py': ('25e55da69b8b8546fc227d2ee486b95f550eee1fcfa4ef0100705a430bbbd216', (('comparison', 29),)),
    'durable_workloads/control.py': ('584957fbb26a18737fd83dbc83d4a5c83de21f721ba92422757475296ece9b08', (('comparison', 4), ('helper-argument', 1))),
    'durable_workloads/coordinator.py': ('89b833a495d652b93c5f423235fb48bed014226955c3af407a138731f4592806', (('comparison', 2),)),
    'durable_workloads/direct_invocation.py': ('6d01fe3bc8c3758154c0e734f1bc7f5136c938ad6b76e53c103055a2f19c3045', (('comparison', 7), ('helper-argument', 1))),
    'durable_workloads/events.py': ('2d84321a2b5fd64cbac8d2ae8dc92862b5bef786a41b697c8cc47dd179a084d2', (('comparison', 2),)),
    'durable_workloads/execution.py': ('cb7e2e368790cbd0439db2cb6c27dec876d8fa7151ca5b94b03f0b7e9842d367', (('comparison', 21),)),
    'durable_workloads/image_preset.py': ('3223b56752d9cb233277732be9d4c02f0987d73fede79649ca9053c337dffb3e', (('comparison', 5), ('helper-argument', 9))),
    'durable_workloads/inventory.py': ('357c5c79dde8a07c3e9288a9cf2f438b24f64f774769a8066b12ce32dff04b6f', (('comparison', 4), ('helper-argument', 1))),
    'durable_workloads/migrations.py': ('1fd5f401b40ca5184b876bcdc6197150a07bea106e249d15459c1f0dc0db3686', (('comparison', 9),)),
    'durable_workloads/schema.py': ('167dff39550ad193fa818fe19094936fb3fe9a9bb6488570fe11caf9abef7ecb', (('comparison', 27), ('helper-argument', 14))),
    'durable_workloads/service.py': ('81c9f508d46d9d50eed49e4075b8c206229ff0a0e2c079abc8642db500171e24', (('comparison', 5), ('helper-argument', 1))),
    'durable_workloads/source_authority.py': ('2a7e30c89551ddee32335a2382d7120e45b464155b848b02a59efa826ce4551a', (('comparison', 2), ('regex', 1))),
    'durable_workloads/storage.py': ('971c9b8bcb83c20c9be76a147d6b09d77e3b8d32d2c00c3f153201d4c48060fa', (('comparison', 66), ('helper-argument', 5))),
    'engine/__init__.py': ('fa4fbb1ff233bc4ea49e407983c0fb2ab97ad5032ff605da1979dc564ece5165', (('comparison', 1),)),
    'engine/autopath.py': ('8ae3fa8d69c0b2659601c978e8567856829eb9c9f2c102c7614436867c12dc14', (('comparison', 9),)),
    'engine/cache_validity.py': ('327054d7405ded3d940765c513df0ad2e33ffd2b24a03d83b3a09e1ac3dcf8a3', (('comparison', 1), ('helper-argument', 1))),
    'engine/dispatch.py': ('7423893dbd4dc755918c741cf35689214f2897110dbeff2ba331f2c0460f2669', (('comparison', 200), ('helper-argument', 45))),
    'engine/executor.py': ('64aacda5b8a12f43946426167d95289c8e68525952be851c921be8677972b016', (('comparison', 63), ('helper-argument', 13))),
    'engine/fastpath.py': ('36b7a97081a54d06998aa0f0ee9b3235e31af07ca93fc92cae7a92df9fd497c5', (('comparison', 5),)),
    'engine/fastpath_promote.py': ('6b0a53b31ac760a68f13c0140e6e41321bd0b38a3954ac9a9f602c6732a4d34b', (('comparison', 4),)),
    'engine/grammar_framework.py': ('1e14e30dfe0909732c4b1d5f785843789c7b1f88b2e9a6f9a66eb50a042ed788', (('comparison', 1), ('helper-argument', 1))),
    'engine/guard_stats.py': ('feaa61f7511dc6e15486fa96ee8df9ffdaf997c2491475ce03c0adb30bb016e3', (('comparison', 1),)),
    'engine/parallel_steps.py': ('8eba4b51f294891328191fb4fea78d03875f20bdcd1d2687def294a12116c726', (('comparison', 4),)),
    'engine/proposer.py': ('e38220edc62eb9331502a454f4f95707369090651c9eb642426ad040b3da29f0', (('comparison', 9),)),
    'engine/proposer_metis.py': ('5cfcc004f5d841ed42cceed309ec1ff3b8723c2da41aa07629cfdf1542f3d49e', (('comparison', 4),)),
    'engine/recovery.py': ('8e29466a625b0f4fb8c21f3490234b7acb9949c2b7e42b0751df70c129b9c3bb', (('comparison', 3),)),
    'engine/recovery_metis.py': ('ca716d3e7896c0df7ce959e6be5a79fe7e57e37d922989427ea4baac1daa573b', (('comparison', 28), ('helper-argument', 1))),
    'engine/routing_pool.py': ('5d1f17b58a888d77eb688ef76f0a4eb8fbbeb3394a1c00e14d185d0378c64b7c', (('comparison', 11),)),
    'engine/terminator.py': ('149e82f8b1b0ab17f54a6c7eed63aed86a4b3b521e963459962b7f4f97207aaa', (('comparison', 1),)),
    'engine/validator.py': ('b695db516e444544a7a620315f51d2c2682b85134076a3d4c7d76a2d23198b6a', (('comparison', 12),)),
    'executor_aging.py': ('a23b3323659557c5e7eaf77fa6fc9a14637582a3ed69d86160605840048ad959', (('comparison', 5),)),
    'executor_birth_activation_probe.py': ('4f4dee67e65a43ea550f7e14894f407fde8509c531b66e7ada5233201c7c0d89', (('comparison', 1), ('helper-argument', 1), ('regex', 1))),
    'executor_birth_admin_operations.py': ('75fedbf55e79076fdbbbd2e87025e73971c10214696d3651f9be6a11a9cd77e9', (('comparison', 1),)),
    'executor_birth_admin_preflight.py': ('1ef98260625c7413d398a6ce70d49b89b0bbde7397a71ece046f7679f57777de', (('comparison', 325), ('helper-argument', 20), ('iteration', 1), ('membership', 6), ('prefix-suffix', 5), ('regex', 1))),
    'executor_birth_approval_authority.py': ('64fdddee16f9ae109ff75b33d98008710d963b7cee4ad2e883598b27575e17d4', (('comparison', 3), ('helper-argument', 1))),
    'executor_birth_approval_store.py': ('49c7f9e7d9f6a4912b4f11e351fa6820aa6ffd635134ec364b1f877f52badce8', (('comparison', 2),)),
    'executor_birth_authoring.py': ('fa7d739a561fe9014fa80ca50d086ec55ae91d038fb3971332be208d38284ecf', (('comparison', 6), ('helper-argument', 1))),
    'executor_birth_bootstrap.py': ('015107c4e7f3567fc182182a40699cd0c707ef98c1c5b4acb779a46ae01f5229', (('comparison', 4),)),
    'executor_birth_context.py': ('55b7957f70153412b3f2d892e1b7391f6a13f04c3912f250f49f1dc22a32bd67', (('helper-argument', 1),)),
    'executor_birth_context_v1.py': ('0c0b1183113cfe831bdc40cc79d8768b75c4b5ed17a742dee0896dc33558bf33', (('comparison', 1), ('helper-argument', 1))),
    'executor_birth_cutover.py': ('1708eb13c16a64927dc023edf702bee3da340cb031d7fd4604c6cacb05c425ac', (('comparison', 3),)),
    'executor_birth_distribution_assembler.py': ('b236cdd040fe101b6950c920b14015fed10f71a67ea5e87601b8ce62292e843f', (('comparison', 11), ('helper-argument', 2))),
    'executor_birth_distribution_installer.py': ('3b8e2ea7272fecd3a52e45c12c1e69e8289c9e3651493500fa1be93a01af0cfb', (('prefix-suffix', 2),)),
    'executor_birth_distribution_manifest.py': ('d4f424c2e65d7a78973387d16f21b69b2daafc7ca4a3e91130fe59320f291b47', (('comparison', 54), ('helper-argument', 4), ('membership', 1))),
    'executor_birth_dominant_startup.py': ('c5db1ad64f97b0e63991d087aaaf386442be51d44c2d97d55bce5bef1b7ef8de', (('comparison', 1), ('iteration', 1), ('prefix-suffix', 1))),
    'executor_birth_enforcement_evidence.py': ('27da48ec1d3ffba70ec221916a7e3d465fce8fd55448a418b16f10a86a5df55d', (('comparison', 1),)),
    'executor_birth_epoch_store.py': ('daf0dc1090d62503a65e1f9a9cd1d98cea28f4b0108170df853d0623fb09c271', (('comparison', 2), ('helper-argument', 7))),
    'executor_birth_failure_review.py': ('939fd468b73460af22db7f348ebacf6146c7e1bb7a7153a3afe34ab78681d511', (('comparison', 3), ('helper-argument', 1))),
    'executor_birth_feedback.py': ('c514fa16f605ae333a0267e3e15503f26c3855c5c77b1144b5ce8c85e41431e9', (('helper-argument', 1),)),
    'executor_birth_identity.py': ('b5b868288dbdf732e8788e65790e6961bd962509e3927d76e52ee644b031baac', (('comparison', 2),)),
    'executor_birth_keystore.py': ('6504cb895bf4d73a7b8d3948ae9b36b1fd557b0212c90b59db7693df1bd23636', (('comparison', 8), ('helper-argument', 1))),
    'executor_birth_legacy_neutralizer.py': ('da58116530dee022e04966426a0946d4ca892fdeb8e15173468085bc9f853cd3', (('comparison', 2), ('prefix-suffix', 2))),
    'executor_birth_legacy_retirement.py': ('ae3ce47dc9cec46994fad03b8f1732c74bc522313f79ba239de942bc1ccaae36', (('comparison', 1),)),
    'executor_birth_lifecycle.py': ('6e3b395c663f5e21df6bc88140993520b4831fae128b5ba1aa543471613ec6d4', (('comparison', 2), ('helper-argument', 1))),
    'executor_birth_operational.py': ('2cfcfb505b28f27e05fea659d734a25dab31f9ae18067b0b41e37ec926a68cf7', (('comparison', 9), ('helper-argument', 2))),
    'executor_birth_ownership_authorities.py': ('f10d7e61d1814547e9d0e834b1b4a2376df0c3f908af824bed014276aeb6135b', (('comparison', 5), ('helper-argument', 1))),
    'executor_birth_ownership_chain.py': ('4078192d823041fef0a90dbbe8d7ed5f2b534e5c514ca879dee4455f4071df70', (('comparison', 10), ('helper-argument', 2))),
    'executor_birth_ownership_coordinator.py': ('0895e0eb4b9c2673a801df162df8c450bf9c3ad15ee0592993ed39023e67eeb7', (('comparison', 11), ('helper-argument', 1))),
    'executor_birth_ownership_cutover.py': ('ea44fec10358acc6b6e221e222a0293047d60a5532a36ba5b757b26f6b4891a8', (('comparison', 8), ('helper-argument', 1))),
    'executor_birth_ownership_preflight.py': ('b83755c189489215c0ec3778a00852c850830c24f3889f588a5182969f5bd2d2', (('comparison', 1), ('helper-argument', 1))),
    'executor_birth_postcondition.py': ('bf51bc292082ab283b96151a28229d002f0c0342b332c0240fba5f6832c19c89', (('comparison', 1),)),
    'executor_birth_predecessor.py': ('00b7e6f69ba50498b86eefd00e291aae79ee2e837809ad89e502f3a516826e65', (('comparison', 6), ('helper-argument', 1))),
    'executor_birth_prepared_root.py': ('679f5feff0e901be3e202e7eab42d40d2fe87e4f1ba3c82becbd080636d0a790', (('comparison', 2),)),
    'executor_birth_prepared_set.py': ('90a72262996e3e30f1e87056d6314ae74a116c8007883a759c0e5e36dda6b2a7', (('comparison', 4), ('helper-argument', 2))),
    'executor_birth_primitive_table_v1.py': ('cc170dd27faf03bc2443a0ded4aa27281a91b1cc10b88c23d1b4e94287317d2f', (('helper-argument', 2),)),
    'executor_birth_producer_store.py': ('d4da7bb88964aa4b47762dd70153b5e33faf5af0eeaf8008eff56834b8dbddef', (('comparison', 16),)),
    'executor_birth_reattestation.py': ('81ffb6185d338389230b89ac19de63d15e399e9e28e302e3286491dec00d41db', (('comparison', 5),)),
    'executor_birth_receipts.py': ('26bf29757cc698f81b73df3dac2dffa286e8982cb7911dcc05ab9fcc7b49435b', (('comparison', 5), ('helper-argument', 1))),
    'executor_birth_retention.py': ('a9c0c2521b3276ddf4007cf9ea5833da45668cef1db0c50984c82b518ac3783c', (('comparison', 4), ('helper-argument', 1))),
    'executor_birth_retention_integration.py': ('acda420905a51510347e0165a1da80e67b03629e918a0dbce28c513f6a600994', (('helper-argument', 2),)),
    'executor_birth_runner.py': ('67ca594cda33e437179443ec7b287148882d44db02a4ca9c39b2ad440089aa2a', (('comparison', 3), ('helper-argument', 1))),
    'executor_birth_runner_windows.py': ('413632794418c52b987a8def18cd34fc77a3afda483d0cb737a6bfe62324b179', (('comparison', 1),)),
    'executor_birth_runner_windows_v1.py': ('2c8779ec8711cb7dede4db3979302942fccd32b8db9c0d91631c09c2921dc157', (('comparison', 5), ('helper-argument', 2))),
    'executor_birth_sandbox_registry_v1.py': ('7d202d6d0936d1ec736f69645578a8eb81709a7468578dc270e8a62a1ee4d05f', (('comparison', 6), ('helper-argument', 1))),
    'executor_birth_secure_file.py': ('f095e79a74333b512ff33b6f0dd5ebe44269161f8e29588688405d57b58b06f4', (('comparison', 2),)),
    'executor_birth_secure_fs.py': ('87976be9d7da4796064546571ad8ecd5cff12a8f4bced15668b947f26aa9c645', (('comparison', 98), ('helper-argument', 4))),
    'executor_birth_semantic_authority.py': ('8f9544cd3bc21efdb02a2541343180ec39666c185448a6ed6e1e8f7310e08c4b', (('comparison', 10), ('helper-argument', 1))),
    'executor_birth_semantic_review.py': ('d067e9471c8bf12548417060ffc035222c74aece6e7d4fd798769fa5196c4982', (('helper-argument', 1),)),
    'executor_birth_service_catalog.py': ('5c11ed6169421b55305530f5a7081eda04323ed1c6bd9627c12a85560c788d60', (('comparison', 56), ('helper-argument', 15))),
    'executor_birth_shadow.py': ('84f2962447c3797a49b3b6c09eec9e1c20641beb20c45b4010ebebb4b7347f9b', (('comparison', 5),)),
    'executor_birth_snapshot.py': ('321b1a7e149e3ad68149681058ff75af340efe3e8d3407c8f218d748aa90fda4', (('comparison', 4), ('helper-argument', 1))),
    'executor_birth_template_table_v1.py': ('6a30a80299d9b6902e04d159d74838c5edf3cb10985d0f4dc7660ded250a18a1', (('helper-argument', 1),)),
    'executor_catalog_identity.py': ('1edab6d0f794c26c0d8fab8411bc50151cfecb7e4c4286acc11d531b256004e0', (('comparison', 3), ('helper-argument', 2))),
    'executor_helpers.py': ('e4769f2120c8c1f6b17bb35dffd1dac841dd90c74b608317f9b61fd77c2df0f0', (('comparison', 4),)),
    'executor_metadata.py': ('dcc9073dd1f5b9dc9b5b2fba191595a6063d19165c82357ccb1e054a183321be', (('comparison', 5),)),
    'executor_scheduler.py': ('efc6336e737378b19db8312f2258e76c7cf0c9cd690dc3e0e174e8558200f712', (('comparison', 17),)),
    'executor_standard.py': ('e12bfb80f0c5b6a35981cc71229c520a9fd4b4d156f6c5c9f9444d1686702ae3', (('comparison', 15), ('membership', 3))),
    'executor_typing.py': ('64d8579ea136a5cf9e4c3e8ce6165fc79d967cdb04c7692b5d5454d3e004a893', (('comparison', 2),)),
    'extract_entries.py': ('aed149cde7277a9083350e5956ebc47b53aec49ae33225af2a5efceddf9bdd61', (('comparison', 44), ('helper-argument', 5))),
    'face_embedding.py': ('98a04bc01d0a2a1233d0749be23b269af435b5d4bdaf0a48fafe7afc5f241755', (('helper-argument', 2),)),
    'fast_path.py': ('da914511a928730fec808be390a7dbf2e72a91dea23da1febcba18c568089d23', (('comparison', 8), ('helper-argument', 1))),
    'filter_field_resolver.py': ('7b33b6a2dee9db29a9097ca766bc700eff2b539c4471988426f355f30b4deb17', (('comparison', 3),)),
    'from_contains_resolver.py': ('e4f1c77d1fdcd33792a930a2ecd700df55c5a503e647b3ebbc020f1e35f9ede8', (('comparison', 1),)),
    'from_step_projection.py': ('2b4a2dc50877e3f4e45b5465f0d9fd9417418afda9ffc43e5055d291c86082b3', (('comparison', 8),)),
    'generated_executor_contract.py': ('04ef3378a8515a5970eeb3113e5afd89052f25871efea5e5236ce29a129e5bb5', (('comparison', 5),)),
    'geo_provider.py': ('b1dd5342808c62a7484ad01cc6c362a4e5c03f645f6ab7084ca13b0c6ab4c8ec', (('comparison', 4), ('helper-argument', 1))),
    'google_places_client.py': ('1e34e06900fe195943a9b12cab2d19317f7b34ad6bcfe99be1a59dc9702d6d93', (('comparison', 1),)),
    'host_health.py': ('21f78be3fc62c678070e62a3b67e98dc9af51193e6afcfcc2f44edc9227e9d49', (('comparison', 2),)),
    'host_location.py': ('c3c603c2c94b0d288c4deecddf7bd04da6e88d3b6db7a33f8c3d0f4e44cbddb2', (('helper-argument', 2),)),
    'html_sanitizer.py': ('009dfd00045355af1fa0791fc4321395b3f358adcaa94e2a88b8ca1997196536', (('comparison', 10),)),
    'http_async_tasks.py': ('96704cdb85d854cd8bb6246b775221cf570125c4ec69cc093e716734a142ee7b', (('comparison', 1),)),
    'http_auth.py': ('32974cf198a302b6192c40216b61ee3450d70139b6b6f0546f5e1235f40ddbda', (('comparison', 10), ('helper-argument', 5))),
    'http_cache.py': ('b8231d02ff7dc5f9f5aaeb551ebf861c93fcba5af6ce51010761c6713947c26e', (('comparison', 4),)),
    'http_render.py': ('8449bc91ca2bfe9d82a5e0cfc98c7392b3f92dac00697255f6efcbf87b5fad33', (('comparison', 2), ('helper-argument', 1))),
    'http_routes_admin.py': ('79164ddc403a6d5e076e2566d4560c180db1555801842ddfd5e56840d3b1bd04', (('comparison', 44), ('helper-argument', 6))),
    'http_routes_agent.py': ('5ebb4cafc027cd77e15439e934d6edf258fd7354a73ee6e44f26672af38e7719', (('comparison', 60), ('helper-argument', 20))),
    'http_routes_durable_workloads.py': ('44960993503debdd2856ab601de85a27b399a7f2bdfef561fa673b79d744b514', (('comparison', 2), ('helper-argument', 12))),
    'http_routes_stack.py': ('ed53444ed114705eb63a060997ef3184d6cd05cf5c17fff525bab13f75e0aafe', (('comparison', 1),)),
    'i18n.py': ('113d8695af75d06a5c4c137b5e23bc85f2cd2508839a5c3dd42ecff1b509fa10', (('comparison', 7),)),
    'i18n_activation.py': ('0abc58c234dca972241de5bec924953c2c8a5a1bbb0225d71e9ae8007a5ec2fb', (('comparison', 36), ('helper-argument', 4))),
    'i18n_materializer.py': ('fe6e9e4fea9c4771cd7ff8d24354588ce47c552f02387189e28631494d0f1b2e', (('comparison', 19), ('helper-argument', 4))),
    'i18n_pipeline.py': ('03dc9751575c52b16d016ce31705c1f5c4e26b159e03913aaaefaea36f6ae96b', (('comparison', 63), ('helper-argument', 3))),
    'i18n_policy_lint.py': ('ac17fa1890525d7c0bec05176468b6953ef0167b4045d3a7f5e71e0595fa2cf7', (('comparison', 5),)),
    'i18n_registry.py': ('971dea9ec92c16b5d8eff0d83d141ff002f0d17272f9680f9f8cc359f0b36bc3', (('comparison', 11), ('helper-argument', 1))),
    'i18n_translator.py': ('98b8926218fc8961dcaa23b317592d62715965606594a74541229a12cdc6cc2c', (('comparison', 29), ('helper-argument', 1))),
    'importer_verb_verify.py': ('d8dd7c6162139d3afda7d6ac132fffc8b8bd50b1ba9a034cead269c62cb94a25', (('comparison', 8),)),
    'index_schema.py': ('fc98015ddae898a279de1542493204d4543fef626b3bdfa5301f33723e91b0e2', (('comparison', 1),)),
    'index_schema_upgrade.py': ('0ef8a34c184d600cc7cc0493f785fd11dd3461330ab1386bc3918c3f874dfd96', (('comparison', 2),)),
    'index_schema_upgrade_v4.py': ('cb78d77e621b14adcc334eb2a0d41ffe05fc8e9ce845d7dd43377c4acfbeed0f', (('comparison', 3),)),
    'install_direction_resolver.py': ('22a551e20b5e0fa4e1cce521aa58c35cb6be224009648dc23b8ac65bd0c20114', (('comparison', 2),)),
    'intent_extractor.py': ('6b118611427cfc8f38aee620271d43954c2e91bbefe89832d5aa3d30546571e0', (('comparison', 7),)),
    'introvertiva.py': ('6cd578a510af29ce28a1949b4c913d51b8205306d460987ce1feeb31774f49c0', (('comparison', 11), ('helper-argument', 2))),
    'invocation_scope.py': ('3b114742acc27b02cfc3da7d84c56d17a2cc41600175b62e5b085f418d42eee4', (('comparison', 2),)),
    'invocations.py': ('363c25ef7556d21f5f5a2faa1d10304c41fb83a1e487211bbc40827669d3640c', (('comparison', 20), ('helper-argument', 1))),
    'jobs/change_intent_materialize.py': ('162bc3bafa598d007bd802fd40993698a57c0ff675778579dc242e82ebf99ce7', (('comparison', 1),)),
    'jobs/detection_translate_pending.py': ('d6d27b3c003314d0aa5a40acd7569fffdc423174bd0b1526d99073af6814c891', (('comparison', 4),)),
    'jobs/i18n_translate_pending.py': ('d47d1081ab7280bd40781417227b7196623d1c23909696b71f66e612de791448', (('comparison', 4),)),
    'jobs/index_image_embed_backfill.py': ('62235a2d7c95b1d5c7ea10dd9b49ca5fa1ed6200f437c0d8c7620222698bce9f', (('comparison', 1),)),
    'jobs/promoter.py': ('ae179534a1023d34865efcee584f8f03ed22ae1414167116fafb785add66284d', (('comparison', 4), ('helper-argument', 1))),
    'jobs/promoter_digest.py': ('cdd19aa72105163bd544be999988aa2658f89152c1de5b25dec662c07cd2f4d4', (('helper-argument', 2),)),
    'jobs/promoter_example.py': ('147c7995902d49ceb349367ff41616f638517074eeb6e00ffecee4d03b89773c', (('comparison', 1),)),
    'jobs/promoter_promote.py': ('2b414d2ccbacfb56eed1128e11b703764ab3ffdb5ca5bcdfb66b95992c318fbb', (('comparison', 3),)),
    'jobs/promoter_state.py': ('461ed51b15c2fff4f87ea6127d9e9744bb8f035c54ea10bc179de3ee26846c4c', (('comparison', 4),)),
    'jobs/reembed_path_context.py': ('9bef7e0da32971a11b6818a1b592550611b3dd3dca2b3def5ffa164f27978200', (('comparison', 2), ('helper-argument', 1))),
    'jobs/skill_sandbox_watchdog.py': ('d7b8489256996b34967e6c5471d216b03ae4ca5855b47f500c2cb47b5fac06e0', (('comparison', 1),)),
    'junk_mail_resolver.py': ('bfeed627f4237e2a9b5916976425e51e3534d4938a1cc6a3b4e9877646ea77f7', (('comparison', 1),)),
    'lifecycle_summary.py': ('3f61c18717e3c0426e3dfcf961726a5c7fc972187e182ada52c5e7d6777d617f', (('comparison', 2), ('helper-argument', 4))),
    'llm_concurrency.py': ('4b4754412fcdb2327ba1813f88035169f8e1031fb426a5dfaf1f1c04b1b642d0', (('comparison', 1),)),
    'llm_cost_sink.py': ('e950cadac1638f4b1f479e6c0d68856ffe10c2a0b6ca053f8fca4f5d9649c0ad', (('comparison', 1),)),
    'llm_helpers.py': ('05e8efb96873387830ab17976b409011b39a496092cd8d308fd7a03037bf6b53', (('comparison', 4), ('helper-argument', 1))),
    'llm_provider.py': ('f4afb5566c37c221eaa9b17318aa493eb6e85fe5a3ee59d29018b376df788d8d', (('comparison', 17), ('helper-argument', 7))),
    'llm_router.py': ('684e26db53a9c0fdecaa3398e7629aa699fdb8590e1a443d7c0f976c5bc640de', (('comparison', 16),)),
    'llm_telemetry.py': ('1b407a56a9fd225a5ea618d04c72974fca9a5341eda899faf678a41a29800a4e', (('comparison', 3),)),
    'loader.py': ('44504dbc30675d51622fd4ab84b4c5edddc91236ca4476e7d10989161afebc8e', (('comparison', 38),)),
    'location_store.py': ('eca811eadd6b30b3a891c55c9584d1bd8c89ed5fe3d24733a6e3a06b50d74d18', (('helper-argument', 2),)),
    'log_lifecycle.py': ('4ca599ce5c9d919da6f8455ca39d54e64954d373abd74c98a193cd585ca1bce1', (('comparison', 2), ('default', 1))),
    'loop_detect.py': ('e38bf6bc1f1a72b457e1126ab3da252b4b657c753ced798d09ef0280c00c7858', (('comparison', 1),)),
    'lre_config.py': ('15925252343bf03b7d4ff134c844243cf6c0d9cce73414242d6aefa3d29d27fd', (('comparison', 2),)),
    'lre_submission.py': ('1d38c5d17e33a687b16538c5853ab7dc74fdcc7733986ca0b2f966d0cd51a307', (('comparison', 5), ('helper-argument', 1))),
    'mail_account_resolver.py': ('985cda4450bd5d207c8a80cfbcbac0885324398ab1df934ef6815d771f6e6299', (('comparison', 2),)),
    'mail_client.py': ('b19f59d423e1ca08bdc44a77d55873b175f6033cc2168caa40e12bed96d31f92', (('comparison', 11),)),
    'manifest_inventory.py': ('bc0067ae64663f3deb0741dd8dfe868602d2515c8a2c257d3bb913d47a159649', (('comparison', 3),)),
    'manifest_lint.py': ('7f2d52e47003302ddf814dd26272fdd1a22f9646a1fe1427d4ac3fbec45e84e8', (('comparison', 14),)),
    'manifest_normalize.py': ('66eb881f7a5223fd582fc2df477cb2a9f47d35c582933bd9627b8984fa16f8a2', (('comparison', 11),)),
    'manifest_rules.py': ('78348748de7efe5ddcdb7a497fcdb5cca1dd74512f84465d688c046bbdad9e3d', (('comparison', 2),)),
    'metnos_http_server.py': ('85656746155c5c1d1a841e9e1710f8c5eeb6998a5d08872f7a2592816306f88a', (('comparison', 2),)),
    'migrate_manifest_descriptions.py': ('f279d63a85581c2de3da4c308ef0943f93b0b68ef3ec0159893370d2b1cb6bbb', (('comparison', 5),)),
    'mnestoma.py': ('5e8e36a858e61c8e1e3afc403d631c554321a77cbcfc680471f7046c6e9c1ed6', (('comparison', 17), ('default', 1), ('helper-argument', 2))),
    'model_identity.py': ('3d9930891d2e47e62afdf1c80ab2669d0bdcccd21cf68aeacbad9aeb80e6dca1', (('comparison', 2), ('helper-argument', 4))),
    'naming_grammar.py': ('b2300f8e825d5ad095785a9408237fcfdb3d17581f316eb08dc868e050b2556f', (('comparison', 6),)),
    'nightly_orchestrator.py': ('63198c2fca47c6170921a238915dfaf671564e1c04f8773ff62135eed1c8c7a7', (('comparison', 2),)),
    'nlu.py': ('a3d7dac0348b73354b4464d84931d4509c7d306176bb86bf5f28835d2ece085d', (('comparison', 3),)),
    'observability.py': ('6a0a910f74e821a8a9febca01fedaa8cf80098b6f9c190db1446404ab6bc29c0', (('comparison', 5),)),
    'orchestration.py': ('41f48ec006e05fff5dbb4179074d709259e64829a9a3df34c11049fdcca64854', (('comparison', 68), ('helper-argument', 2))),
    'ordering_clause.py': ('079e02f456c85755ed00a1b3bd09e77abc4636bd14031f1fbe6a80e38d319bf2', (('comparison', 11),)),
    'output_format.py': ('26890c3121f27cf156e8c5b36af86c828f7833cbf94cdba5b713b6b19798bcf1', (('comparison', 2),)),
    'output_policy.py': ('b444f642af62260833bbaea6bfb09d34db417323e88bb3a6fc0d0ac881ff1a50', (('comparison', 23),)),
    'paired_device_arg_resolver.py': ('cd4703ea17ef7756b1b31dd403937bfd442dd81b40522bc9fd48d286bb6d3ce2', (('comparison', 2), ('regex', 1))),
    'pairing.py': ('f946cd1296455f11d183c09e3d89282f65280b92029eaeba683ed046629fc961', (('comparison', 12), ('helper-argument', 1))),
    'parallel_walk.py': ('242fc2bc9168b8be43330bd613060debaa4cc28d88cddeaa60ad45f63ae51ab9', (('comparison', 1),)),
    'path_alias.py': ('707ea8dd01406aba8feb330eb5676cba5400b91897c69fbc4afd4a5376970247', (('comparison', 1),)),
    'path_shape.py': ('abdc636d6235201961f33b133999087aedbff67bc1fce51111c3948ca78d42eb', (('comparison', 1),)),
    'persons_registry.py': ('86fe970b430aec48e924b90058c693bf36fa0a5b40d8028894849466169e00da', (('comparison', 3), ('helper-argument', 2))),
    'photo_fields_resolver.py': ('31730e2c2b57e02f56902d19c6644e98c928a1739a08fba2f642b13a66044f43', (('comparison', 4),)),
    'photon_client.py': ('8411a1f1c9f6a70ad86fe35b267a60f0e790d03bda744a2108e591bc0ec97363', (('comparison', 4),)),
    'pipeline_effects.py': ('c4532d3dbcc494846e31dfe104f7c8ebe1c25713914f3f2dfde74271f6227308', (('comparison', 4),)),
    'pipeline_shape.py': ('9daa84fe77b475d6cf20d6ffc007f5159f67bcf24db072d0521e83f7c6c097fe', (('comparison', 7),)),
    'placement.py': ('90384f76c81e8113aba1d5db2560917fc0e96a7524278b9e205e09ce594df3da', (('comparison', 4),)),
    'platform_policy.py': ('37defdc80bc50dba9f69dd91a69a75c35b99f23f2c1913bd0af14f7efb8fe10c', (('comparison', 8),)),
    'playwright_sidecar/action_resolver.py': ('3ba49c95e4fd433732854b7f8104e3fc0a060e1d25fc3c7508669dd53fb6ceb6', (('comparison', 38), ('helper-argument', 1))),
    'playwright_sidecar/credential_injection.py': ('eaebb508e27c343ef4457aa7a62995db2b8a2bda2526a475c811993725697681', (('comparison', 15), ('helper-argument', 1))),
    'playwright_sidecar/factor_resolvers.py': ('a7f30d001b87d5c301ea7515c34f17994b3349ba0e6addf81ec0ffcb1d2b6cf3', (('comparison', 8),)),
    'playwright_sidecar/server.py': ('6c1937cceb42603b8260f6e628053abcdb39b63db1b96281826b6ae0f5b3aeae', (('comparison', 20), ('helper-argument', 1))),
    'playwright_sidecar/session_broker.py': ('22ca733da88777d83b6dfe20162cd799c382b19bad4b9fd526591a0b08bb9376', (('comparison', 80), ('helper-argument', 4))),
    'playwright_sidecar/stealth.py': ('bb1d3a7c794b0b8794d4d56f28f98f8682f40a43c91c0ea96300e7cbf7456614', (('comparison', 4),)),
    'policy.py': ('7ead7b39920ffc32b745144101ad676f8b30145ad5319cdf086ef1a788ed4b3b', (('comparison', 11), ('helper-argument', 1))),
    'prefilter.py': ('e70f450b40f9988c3382c6577e0bcd4885ef153b99885bac8d467b35852190f3', (('comparison', 16), ('regex', 2))),
    'prefilter_rules.py': ('0bea0df4368509191cea23dedfe5e0422668d5803696461021e0da66a5b3cf60', (('comparison', 1),)),
    'prefilter_stats.py': ('fa28f1234067a8cfe212eeffc10e7341ebc3704cde35a1b5c129b5a27ff3b6e8', (('comparison', 1),)),
    'prefilter_strategies/__init__.py': ('a07f7e406ad64d31682826c69e6bff620c7f849a194458c6dc704c65d93cde42', (('comparison', 3),)),
    'prefilter_strategies/bloom.py': ('b1216f17e7338f41cfce5c1a328c220dbcb115c2d578119f38ccf12075c3a503', (('comparison', 1),)),
    'presentation_contract.py': ('0d4a6e273ad39b9c57ea67ff234f047b55cff459beb9e416c562a99fc270cc65', (('comparison', 1),)),
    'prompt_loader.py': ('2cf41dccaeefed2d117f22f233f00659cec63b02bcb1206be08a1d900c4d85a5', (('comparison', 7), ('helper-argument', 1))),
    'prompts_lint.py': ('49ab5ead58184b078bf320804cbd2db7c92374665b7dc5a7ec75f63da0d76d88', (('comparison', 7),)),
    'proposal_actions.py': ('1daf5d89113cd5c641e362750fc8c4e00a9891cb9ef46aa10dfec2a4cdb0df64', (('comparison', 5),)),
    'proposal_evaluator.py': ('a1bdd52177873ae7e482c3664a789dbc36d7432925277a8a417f89ffbf84d702', (('comparison', 8),)),
    'proposals_cleanup.py': ('79d6a2f838794434ca6d9c8d5126754ae65f5501b4a8521e19b1c71595581d93', (('comparison', 7),)),
    'proposals_eta_index.py': ('474fdc53045b4f323b1c77afd4ed59e7d2e668ab16068178fad410a4da9ba2fa', (('comparison', 3),)),
    'proposals_state.py': ('c816a2598ad5d3fec8f4666750ca6d09c30ee1f947e8863febec8269e2fa1f3f', (('comparison', 3),)),
    'protected_undo.py': ('2a13687204fb4b0cf010f1fd980e7b47ba2578d78a82c7ec54485b0eb7e16d6f', (('helper-argument', 1),)),
    'published_docs.py': ('0a97ee39fe3e7b11f4ffcc438065efdab1d86b3798227b361590d84a0e8fcb5e', (('comparison', 18),)),
    'read_format_resolver.py': ('8e28ec1145fa245ad043a11f71d20852a91e5ac1a5579a6f98bbd2c4eb7c6e66', (('comparison', 4),)),
    'recurring_tasks.py': ('e2388ff38a10972be35816e8d4ea74c55d5aa071292bcd5358534689278d432a', (('comparison', 17), ('helper-argument', 2))),
    'reliability.py': ('b2c7db512fc23a691644b1d6229b4c152ba41dda572a225ac28e3818a3ca2234', (('comparison', 5),)),
    'remote_exec.py': ('ca253c567b99ae4cf11728e94a3276d6428b60574fcd03efdb1aca2cfac58b18', (('comparison', 1),)),
    'reverse_patterns.py': ('74a930d05196d7d86fd8127994aa27ae00ea5e8fe90a0bec924c91c5e7c2e59f', (('comparison', 20),)),
    'route_disambiguation.py': ('f235793d0b6cabfe606befac143e8782df56e9d906cf48f2f65d12326f236cbd', (('comparison', 1), ('helper-argument', 2))),
    'safety/canonicalize.py': ('ab1b38df4110138ea67288133efacb68d862a29bbd32cf7e2366b14aa3ab92a1', (('comparison', 8), ('helper-argument', 4), ('membership', 1), ('prefix-suffix', 7), ('regex', 2))),
    'safety/seed_bootstrap.py': ('22560da20621786274dde1f7499dc02abfb7e344cb89f613c2bea3f07602c015', (('comparison', 1),)),
    'safety/storage.py': ('bcf055ea55d4089b863ebdac58e32e81e052ffd825603e3aebe7d86cd226bbd6', (('comparison', 2),)),
    'sandbox.py': ('e44eb9f0e88fb6e2011be830ea9c5af4520b844a41ccb057f62a0f124adf3384', (('comparison', 33),)),
    'scheduler_v2/builtin_callbacks.py': ('5d79acf1f1ac317496e79da01d1b463154cd75fc68c5f53889d8d1e7f8a74ec4', (('comparison', 4),)),
    'scheduler_v2/client.py': ('ef424f76cf231e6dcc71b30d78071aee6d0dc7285a09c238eea53df5a489600c', (('helper-argument', 1),)),
    'scheduler_v2/health.py': ('80fdc47a3d8c1a598cb0cc26ff107721388d150ba17206409dc37e0c281d16af', (('comparison', 1), ('helper-argument', 1))),
    'scheduler_v2/migrate_v1.py': ('61f3db0017ee5569beb6c5342dd3930e4dd64f158a855a9b7dcf0ecb887ecf6c', (('comparison', 5),)),
    'scheduler_v2/schedule_parser.py': ('1231c0fe8151cedc5321373f00b3cd39e853dde423e7c7222120a0f0b03bd5c4', (('comparison', 4),)),
    'scheduler_v2/storage.py': ('16cba0865563c2dc0ae9b565c3685fd24e4b45c0ded9ee9be38e1a067abcea46', (('comparison', 3),)),
    'scratchpad.py': ('23999816339cb44939b699d4a6e0374b69609c05a31e35f7e28c00fc1f83f981', (('comparison', 10),)),
    'self_recipient_resolver.py': ('9509381e4493ff985704183a9fb622f47cfc4af88220e96acdb99a6ac2b64155', (('comparison', 1),)),
    'service_health_monitor.py': ('8b2c1acab7bc5af8f86f1a2dcf58ed4a40e46db7ca1be2358d1254aee84acf98', (('comparison', 8),)),
    'services_registry.py': ('1600e636796373c7d1d5c34ff277cefb19d7112832bd5bd820e5aeb21ea5b886', (('comparison', 26), ('helper-argument', 1))),
    'sign.py': ('7c03a48db0fa1bf4eca13d336900523d00cff47b285bc1f94faedbcfa75c4d1f', (('comparison', 8),)),
    'sites_audit.py': ('567f0063474ec9a4e24517ae7017433add7c96796583f17169128de0258e4c99', (('helper-argument', 1),)),
    'sites_cooldown.py': ('73a0f894a478dee09f06eac0dd76884d6adfe8a1a3c479785109b2b5f3234cc3', (('comparison', 1),)),
    'sites_observed.py': ('b2a5cec4fb3a29deb349807cdc5466c5e4c3787ea5d50461fd73badd0598b5ab', (('comparison', 1),)),
    'sites_origin.py': ('fa14c8ba96327a65ca87027cda34708aa0b06e0128e4847f3b888152d7f1d4e4', (('comparison', 5),)),
    'skill_admin.py': ('37cc7480f72925a29103192a90ba6d1940a8fa9fadc45835d894e3c44e07310f', (('comparison', 3),)),
    'skill_admission.py': ('de629694f90b2dae5478840dd097efce4555d4f70ca5e3f5c9d547359c374a7d', (('comparison', 5),)),
    'skill_audit.py': ('453882d73907df48314b71fa0c351055a60956104f131f312b75c26e9c1c9b71', (('comparison', 1),)),
    'skill_codegen.py': ('bcf727b7ab51b893d1c9967eead77eafb343e3c5196fba074822e94d956f0377', (('comparison', 31), ('helper-argument', 3))),
    'skill_description_llm.py': ('a405323bafc718e1618171c82cba07a846e7b4f8889b1bbf4dd8673f76ed0df6', (('comparison', 1),)),
    'skill_fetch.py': ('ada3a2e607f51486bc9bc19523a11234c7eee72807c6fe41bdc4790784debf9a', (('comparison', 5),)),
    'skill_parser.py': ('72267d3bca4ef795b43c3f4161d517230ffab5234248ed2e5b27aea69e1b1fb3', (('comparison', 11),)),
    'skill_registry.py': ('12fd28feeac4b8a7b3104c94d0b602aced6acac5046d24e89b6f2287af61e792', (('comparison', 10),)),
    'skill_test_fakes.py': ('e9f64679bec9e7d7c30d3506c49c07380dd29049b2dcafbb090a28f49cf2130e', (('helper-argument', 1),)),
    'skill_translator.py': ('186a4afdff13580c09a7a1b896499525024c027112c4d67c46cbcf4eef20d63c', (('comparison', 27), ('helper-argument', 1))),
    'skill_wrapper.py': ('a5fa76edfcee514723864c648d57922057b900b28ffa6b2eaf5bcc80a4535834', (('comparison', 2),)),
    'skills_catalog.py': ('d9bb4b088e5962d77e199a09e218e1508bdeb947c7c2a668fdb65c64d2a7da92', (('comparison', 2),)),
    'smoke.py': ('85e7a8270dea94c5f675408430995435710764d739b9bb29aa2b6cb39429f521', (('comparison', 9), ('helper-argument', 2))),
    'speculation.py': ('5f38222f6f1ebe3ba3c450c40474c791951ba2e0c31f4711f59c0190336920b5', (('comparison', 1),)),
    'stack_migration.py': ('5d2de580762897d18948fb709fa8aaefd32fea9b9967f2a4fe3fdf62bc3da206', (('comparison', 13), ('helper-argument', 1))),
    'stack_reconcile.py': ('cbc39432d9e56e3652b22e1c56a69fa0d1a4dd96f603081841e0009cdbea71f3', (('comparison', 34), ('helper-argument', 2))),
    'state_receipts.py': ('3f6c7c64e5073821c8d1a26afd8dcfd4666e53d8fd3c590156deedca111904a7', (('comparison', 2),)),
    'store.py': ('45927fda29a44168cbc00eb9ce4f6bd519edb4b3729723b68b1fe0ff349b8608', (('comparison', 1),)),
    'store_bootstrap.py': ('5ec6ad37a946c48c4fe51447ca84d58fc1b4ec910a5e8a6a5e536544e02cd7a7', (('helper-argument', 1),)),
    'synt.py': ('44728663d66dd1caf72a7e1291c5429fc51fd6c3cb889e0ed6c425ceb59d7c4e', (('comparison', 24), ('helper-argument', 2))),
    'synt_multistage.py': ('ee040095fa8899018df10468fab0f49a0cc64f23eeb3452dfbd8f73b408c9765', (('comparison', 5),)),
    'synth_orphan_cleanup.py': ('a491427a95833d225ccab7f831e4a3287e2da8b1194700146ee446b3e1b42c0a', (('comparison', 2), ('helper-argument', 1))),
    'synth_request.py': ('8438e98b42605f41658f2d706e820cd805982f1aeadab00cc53c5af19654fdf8', (('comparison', 3), ('helper-argument', 1))),
    'system/admin.py': ('75fca68b6f6faa60f84512649ca249e9be868a8906e6f4b787a741ff064ffda5', (('comparison', 35), ('helper-argument', 2), ('iteration', 2), ('literal-lookup', 1), ('membership', 1), ('prefix-suffix', 5), ('regex', 1))),
    'system/sudoer.py': ('5fe41121259fc6ba5b913434fe1c822c3c665fb53922b3d5e2e6a003f46e07b8', (('comparison', 5), ('iteration', 1))),
    'system_binaries.py': ('768e45233baf739ebf3287e14ed06015dadba2216aded7d3acc4c9c173cf1338', (('comparison', 2),)),
    'tabular_projection.py': ('751ea12e60e98f456ed0e37f1ef6662a8b356cc29b7aaac343351836f226abc6', (('comparison', 5),)),
    'target_device.py': ('0662f91370ee9e0fcbbd74c9119c3f19fdd5d26a8b66b9fc44043cf3e679125e', (('comparison', 13),)),
    'telos_introspect.py': ('2f81a72f784be539b9abcca2eb21ac139edb2d40e1d843fc00debfb66cba831c', (('comparison', 2),)),
    'telos_lenses/__init__.py': ('231e6a853e027e04c93351b633f40a69cf9fdbf6ee2e4e449dc3b360b01a8136', (('comparison', 10),)),
    'telos_loader.py': ('bf3fb57f3a794d9bb222db59cb242e039f8e4a5786733264785e91210f857c1c', (('comparison', 2),)),
    'telos_proposals_store.py': ('e2841b6375afae80816125fc5e9287c4bd2799be4ca61b20477776401740499f', (('comparison', 5),)),
    'telos_synth_consumer.py': ('aaf4ff278417c498ac3712ff4ac0b33128d485513cb38d7acba75226efe5c0b0', (('comparison', 2), ('helper-argument', 1))),
    'test_runner.py': ('8e0bea2983522a4557fd18d3ce23d9e74f8ab8b37e07928424db99c66024d63c', (('comparison', 20),)),
    'testing/populate_cases.py': ('2f7bb767807b15247450566c10bf54a56587b6d895fb7539cd924debc104b1a5', (('comparison', 1), ('helper-argument', 12))),
    'testing/registry.py': ('cc9222c91ddcfb2797838af2e608f5660bb56a5403b6c4b2b804fb790d288bb5', (('comparison', 1),)),
    'testing/runner.py': ('17732635c0cc5fa202cada6312839dcd8453b06123dc54ea6c8cf503d427ba96', (('comparison', 9), ('helper-argument', 1))),
    'testing/seed_modules.py': ('e4fd30a484121c9b7dddad8377290fda05b7d2645f458c65c1cbfce988a758c1', (('comparison', 1),)),
    'time_window_parser.py': ('0fc33b4fff73e9e3a35d8f756b9b597f6cc3f8bda42859575d5f0c42bba570e2', (('comparison', 12),)),
    'time_window_resolver.py': ('ab271a4ecbac0a3a4f1050050d4ff59b91cc65c4865a772128c4af68f0ae04a8', (('comparison', 6),)),
    'tool_grammar.py': ('add8e647160305006e53d6a16f4de16c60a95944bd54f2d49cf0af32c6d8fa5c', (('comparison', 17), ('helper-argument', 6))),
    'tool_schema_slim.py': ('273cd4a9d0ec466eb795d3d9abb3c469f738c16322862c409f8d5804a3246e4c', (('comparison', 4),)),
    'treated_issues_guard.py': ('16f164d13e8c226bd102f261ea54101a9b44b14c0e4fef368ed09fac3e20c3b2', (('comparison', 1),)),
    'turn_events.py': ('4458d320cef2f195d40c83076fd3ae8ef1ec47d217989e3fce2e792d33b1ec52', (('comparison', 4),)),
    'turn_feedback.py': ('ea5b0da1aa89ebf72386205622d5c593715ec120a7cfb3252c05c953f92000d8', (('comparison', 13), ('helper-argument', 1))),
    'tutor/associations.py': ('fc9b06e5d4b667c66c87c4e6020a676bde731de570d85ad4b3ec16739072f078', (('comparison', 3), ('helper-argument', 2))),
    'tutor/catalog.py': ('14cff1b455dd31ddfeb092a8843187bc2a7a4bc54133769765e31c99cb625944', (('comparison', 7), ('helper-argument', 3))),
    'tutor/compose.py': ('ab5dc6262535f3659e10bf10e4ab9adad817d3d540f96422d4255016388f249f', (('comparison', 1),)),
    'tutor/gaps.py': ('85ca449498b7ee28472aa9ec1c6b493ff7759a68f36d23b0d1667a0b3f8ccdf8', (('comparison', 5), ('helper-argument', 2))),
    'tutor/handoff.py': ('f7502bda22f0cf44cccf07cdffe13ceb4181a8475d95c4584e436bc567bb0be4', (('comparison', 4),)),
    'tutor/mode.py': ('d3f587370d89e817e18a8d5260c45cf65e25d89f15d80646a10dbb2023ab87e2', (('comparison', 2),)),
    'tutor/obligations.py': ('d2a17a84b175d27f5b57976bd7766bd0f31b60aaac8aa6f5bca3055fde843830', (('comparison', 1),)),
    'tutor/observation_views.py': ('3800a490fdc8c89c20f8030237167c7197a8fa5a86b37a96f8091de813b2ffd1', (('comparison', 3), ('helper-argument', 5))),
    'tutor/probe_worker.py': ('e2a4791a0e052e3558cd10f721ac4edcc5f2ce438dfea73e82b6787c8dbe9ad6', (('comparison', 1), ('helper-argument', 1))),
    'tutor/probes.py': ('34dddf9e486a95ce976342ab067ae062590132cf145535483edf833809a58f38', (('comparison', 6), ('helper-argument', 14))),
    'tutor/render.py': ('9574cfade1933b85ab49109cf36e72b281b62f4915cd3602904a49b7014b3bbd', (('comparison', 1),)),
    'tutor/semantic.py': ('77f08182bd618cf53ae2284c63bad7495d782968fc0582711a74f464b76fb03f', (('comparison', 13),)),
    'tutor/service.py': ('ce959fb098189c37f834900f4bd2a9d5255b43966f7b0da3b20f29a3a8e02b28', (('comparison', 29),)),
    'tutor/sources.py': ('c6464b480a8ff01f58fcc617b117a6f34eba84fb825f5582b71d6695d7111146', (('comparison', 21), ('helper-argument', 4))),
    'tutor/telemetry.py': ('365a063e72b311a91ae2eaea303d7f55a7c594705bd5a90af2053f34b1607c08', (('helper-argument', 1),)),
    'tutor_boundary.py': ('dc4be09d2d39db384a7f949de6db2de6461392ec48172d5fc5aa4572a325f2f6', (('comparison', 3),)),
    'ui_surfaces.py': ('4dfdcb292df74a8101c7ce43437187f8bc5870a149ba2ab444abb1e038b81623', (('comparison', 4), ('helper-argument', 15))),
    'undo.py': ('e0b55993ee4d4ff0c0468962001ffd439c468a3173a71581600f58fa9a3205fb', (('comparison', 12),)),
    'unique_rows_resolver.py': ('405249c233d60428fcea3d215cfbc3778fa96c17d54e5ea9569cffb4bb9814c0', (('comparison', 1), ('regex', 1))),
    'upload_cleanup.py': ('738a94cc1abc1dc46a767967ccebac1034924b9f4dc140116d20f4789ebdf9f8', (('comparison', 3),)),
    'users.py': ('4cc994a51d4a944ee20c6761ac02de362cb2a3808715a631da8cc62c06f16514', (('comparison', 10),)),
    'users_pairings_sync.py': ('1f6a6b8d0c21036a610b0e79e0b9c680d945d04cc44d1cbe012389521320327a', (('comparison', 2),)),
    'vaglio.py': ('6e6eff3a46fd0966fd8470c99eb76ba25bdc1590943ee2a307a3c6de6f4d2924', (('comparison', 4), ('helper-argument', 3))),
    'virt/__init__.py': ('fddba426ac6461151ab48766e6a71c030fdb25a2a608f1e84bc3e8b49bc230e6', (('comparison', 9),)),
    'virt/config_editor.py': ('a8925a8735f014addd11d72a3feea64151b32eca760a1e311799e710309723af', (('comparison', 20),)),
    'virt/configuration.py': ('47961ebed9ec791602997e300558ecc1cf6d9589591c514c55cd2047b2b05dc4', (('comparison', 14), ('helper-argument', 1))),
    'virt/tiers.py': ('e7e6d00bd76b312946845363aef11cff5a68cd57901d0492fa63a5e9638b3bf4', (('comparison', 2),)),
    'vlm_client.py': ('c76b486387c23e3170a334df7851417a89bdce5db279726b3a3ac2d412e9d765', (('comparison', 6), ('helper-argument', 1))),
    'vocab.py': ('bbd4aba97093da4f276770ebac5f6ea314f9bd61722f7aea7b066a18da13bde6', (('comparison', 2),)),
    'watch_progress_telegram.py': ('c6958573bf621574c7c58252fa132b891ed925358befcefb096edced2c3d87ad', (('comparison', 2),)),
}

VALUE_BOUND_SEED_GATE_FINGERPRINTS: Mapping[str, int] = {
    '00e4f57dd9df05f0807b9eb87c53d202329ec1df5e678a0e4a608c2c908116e9': 1,
    '02e6f840d0876f1e03f68b14ac48c9684d4dd18235f864ae376f28adccadee29': 1,
    '0f31699a784bbbde1dabc7e44c29a7f4f1f09597afb3d195c441ca83a2367c16': 1,
    '11b331d6130a69421fe1d2ac19c932bd3703cec68ce0de0a56b82e574f3ce63d': 1,
    '12920a6f2ce383b924d4185e00026497bd096f6a683c41e311ef1f8caed5a0af': 1,
    '19d5a2608399bdda7af5d99cc0203be1cf93914efcb956d25f39be6c977d22eb': 1,
    '2677308ec410798ae0db0d960ab749a48b7b051dbd0afff3f56517a7046d3242': 1,
    '37cfec95a2ad59d41dc4561b45c1c7e648f7453e88d8e818012bfead85d9eeea': 1,
    '394f5e264619574906af020ba22de7d1ec106c83e1c9ab91f908169b1876c0f7': 1,
    '3c5a4e56c1da5163502dea1f9e6ade20087ba9838b7c511edad93d3082ef2520': 1,
    '4038e72230355cae792ff4f8e6dc45f091b4744266e93a874b87e8ff46c52f96': 1,
    '59135a2b39b593259d809e0052fab1830a6ac1b0f6bdcd0f8140c91a9b5ad67b': 1,
    '5b7972608c1745b50fb43dc4f50dea6734b5c412b27c1760177e1fcca7aaed15': 1,
    '6748e5817a40594aa02a78dd06ea61e9c2198a4c8bc836273e43d7d97be2d430': 1,
    '687539682fd7c3923849904b3ee80f66cabfca2a40f6b5ce5695b354040acc9f': 1,
    '85153d1a51b49cc83bdd7ea85f5f2c50ebf207ca8fa8f9d7b434ebc12c726272': 1,
    'a1680122bfb86ca124740e89b87f13e1407414998b0926caa80e2395565459be': 1,
    'bff372442e0bf1fe92238b44f62ee9969eaaa25097ce1b778b97c66948fcc905': 1,
    'c46f6f231f08164dafd12b4cbca495e88da5e9eceeb96eee127d06cb0bd2530d': 1,
    'c60805e60311b753fdd5ec8803ed547ef501ed95b26f22ff2ea930f53af2bb92': 1,
    'c8d15bd528d21c18439137febf1559e030d5c1dd788152758bc01721658fc568': 1,
    'cd819228255412065391aa6caacf2f1953d3188334b4b5fae6b2ea9a113a5c48': 1,
    'cdb39417a8096d831f501f889994ba641a8cf016024b1d79db734662b37246e9': 1,
    'daa390055fc3c83f1e42ed7021aa938584ec67dff50e2729ed867f90d0aa6b1b': 1,
    'f0f71c47450b47cba242bd525d4cc1adf9eb0e6de5a73ffaa76a8f3d2693e384': 1,
}


# Known historical tables whose names are too domain-specific for the generic
# name heuristic.  Keeping them here turns the one-time adversarial inventory
# into a permanent regression guard.
KNOWN_LINGUISTIC_SYMBOLS: Mapping[str, frozenset[str]] = {
    "channels/daemon.py": frozenset({"_EMERGENCY_CONFIRMATIONS"}),
    "llm_router.py": frozenset({"_PROMPTS_FALLBACK"}),
    "playwright_sidecar/action_resolver.py": frozenset({
        "_VERBS_FALLBACK", "_OVERLAY_DISMISS_FALLBACK",
    }),
    "agent_runtime.py": frozenset({
        "_THINKING_LEAK_RE", "_LEAK_IT_PAREN_PERMISSION_RE",
        "_LEAK_IT_STANDALONE_RE", "_RUNTIME_INTERNAL_LEAK_RE",
        "_NOTIFY_CONTINUATION_RE", "_BINDING_WEAK",
    }),
    "backends/messages/email_metnos.py": frozenset({"_FOLDER_SPECIAL"}),
    "engine/dispatch.py": frozenset({
        "common_fields", "default_sheet_fields",
    }),
    "backend_resolver.py": frozenset({"OBJECT_BACKENDS"}),
    "calendar_resolver.py": frozenset({"_ALL_CALS"}),
    "mail_account_resolver.py": frozenset({"_ALL_MAIL_QUERY"}),
    "self_recipient_resolver.py": frozenset({"_SELF_QUERY"}),
    "from_contains_resolver.py": frozenset({"_STOP"}),
    "read_format_resolver.py": frozenset({"_FORMAT_RE", "_DEDUP_RE"}),
    "photon_client.py": frozenset({"_AUTO_OSM_TAG"}),
    "credential_intake.py": frozenset({
        "_LABEL_FALLBACK", "_FIELD_FALLBACK", "_CONNECTOR_FALLBACK",
        "_INTAKE_PREFIX_FALLBACK",
    }),
    "telos_lenses/_base.py": frozenset({"_PATERNALISM_RE"}),
}


# Adversarial guards for the P1/P2 boundaries reopened during the RM-0005
# close.  These names deliberately include the *old* neutral identifiers: a
# regression must remain visible even when it avoids the generic WORD/HINT/RE
# naming convention.  Function guards below cover call-site literals which do
# not have a module symbol at all.  Keeping this inventory narrow avoids
# treating every runtime string (wire enums, SQL, paths, messages) as language.
AUDITED_LINGUISTIC_SYMBOLS: Mapping[str, frozenset[str]] = {
    "skill_wrapper.py": frozenset({"_TRUTHY", "_FALSY"}),
    "store_entries.py": frozenset({"_AFFINITY"}),
    "proposal_evaluator.py": frozenset({
        "_REFORMULATION_TEMPLATES_IT", "_REFORMULATION_TEMPLATES_EN",
    }),
    "google_places_client.py": frozenset({"_AUTO_GOOGLE_TYPE"}),
    "telos_lenses/_base.py": frozenset({"_PATERNALISM_RE"}),
    "extract_entries.py": frozenset({"_RELEVANCE_GENERIC_TOKENS"}),
    "describe_entries.py": frozenset({"_DOC_AUDIT_VARIANT_TOKENS"}),
}


# Within these audited functions every newly-owned literal table is suspect,
# even if its local variable is called merely ``data`` or ``items``.  The
# functions are small language-decision boundaries and currently own no such
# table.  Heavy protocol functions (notably Executor.run) intentionally use a
# more exact local-symbol guard below.
AUDITED_TABLE_FUNCTIONS: Mapping[str, frozenset[str]] = {
    "skill_wrapper.py": frozenset({"_normalize_bool_flags"}),
    "skill_admin.py": frozenset({"handle_set_skills"}),
    "engine/dispatch.py": frozenset({"_normalize_result_folder_exclusion"}),
    "proposal_evaluator.py": frozenset({"_bow_intent_simple"}),
    "http_routes_agent.py": frozenset({
        "_apply_dialog_cancel", "_apply_dialog_pending",
        "_consume_http_get_inputs_response",
    }),
    "google_places_client.py": frozenset({
        "_google_surface_types", "_autotype_for_query",
    }),
    "prefilter_strategies/constraint.py": frozenset({
        "_provider_from_query", "_extract_constraints",
    }),
    "change_intent_adapters/telos.py": frozenset({"_infer_arg_from_action"}),
    "telos_lenses/_base.py": frozenset({"paternalism_check"}),
    "telos_proposals_store.py": frozenset({"_semantic_overlap_query"}),
    "admin/promotions_review.py": frozenset({"_canonical_choice"}),
}


# Exact neutral locals in large functions where scanning every local container
# would create a flood of protocol dictionaries.  The names are stable because
# they are the historical regression sites exercised by the mutant tests.
AUDITED_LOCAL_SYMBOLS: Mapping[str, Mapping[str, frozenset[str]]] = {
    "engine/executor.py": {
        "_prepare_static_read_args": frozenset({"deep"}),
        "run": frozenset({"_deepcrawl"}),
    },
    "engine/dispatch.py": {
        "_normalize_result_folder_exclusion": frozenset({"excludes"}),
    },
    "change_intent_adapters/telos.py": {
        "_infer_arg_from_action": frozenset({"m"}),
    },
}


# Literal membership/iteration in these functions used to turn words directly
# into administrative mutation, cancellation, provider binding, exclusion or
# proposal evidence.  Their current implementations use typed values or the
# native-ready lexicon instead.
AUDITED_MEMBERSHIP_FUNCTIONS: Mapping[str, frozenset[str]] = {
    "skill_admin.py": frozenset({"handle_set_skills"}),
    "http_routes_agent.py": frozenset({
        "_apply_dialog_cancel", "_apply_dialog_pending",
        "_consume_http_get_inputs_response",
    }),
}
AUDITED_LITERAL_ITERATION_FUNCTIONS: Mapping[str, frozenset[str]] = {
    "engine/dispatch.py": frozenset({"_normalize_result_folder_exclusion"}),
    "proposal_evaluator.py": frozenset({"_bow_intent_simple"}),
    "prefilter_strategies/constraint.py": frozenset({"_extract_constraints"}),
}
AUDITED_REGEX_FUNCTIONS: Mapping[str, frozenset[str]] = {
    "engine/executor.py": frozenset({"_prepare_static_read_args", "run"}),
    "change_intent_adapters/telos.py": frozenset({"_infer_arg_from_action"}),
    "telos_lenses/_base.py": frozenset({"paternalism_check"}),
}


def _invariant(symbol: str, kind: str, reason: str) -> TechnicalInvariant:
    return TechnicalInvariant(symbol=symbol, kind=kind, reason=reason)


# These tables are allowed only while their *shape and values* remain
# technical.  Unlike the ordinary symbol allowlist, this does not waive the
# whole assignment: adding ``farmacia`` to the OSM map or a localized label to
# an admin choice makes the validator fail and the census reject it.
STRUCTURAL_TECHNICAL_INVARIANTS: Mapping[
    str, tuple[TechnicalInvariant, ...]
] = {
    "skill_wrapper.py": (
        _invariant(
            "_STORE_TRUE_CANONICAL_TRUE", "json-boolean-wire-values",
            "closed true spellings admitted by the typed wrapper protocol",
        ),
        _invariant(
            "_STORE_TRUE_CANONICAL_FALSE", "json-boolean-wire-values",
            "closed false spellings admitted by the typed wrapper protocol",
        ),
    ),
    "google_places_client.py": (
        _invariant(
            "_OSM_TO_GOOGLE_TYPE", "provider-protocol-projection",
            "canonical OSM tag identities projected to Google type identities",
        ),
    ),
    "admin/promotions_review.py": tuple(
        _invariant(
            symbol, "i18n-choice-schema",
            "canonical mutation value paired with an i18n message key",
        )
        for symbol in (
            "_OPTIONS_PROMOTED_GRACE", "_OPTIONS_REVIEW_NEEDED",
            "_OPTIONS_ARCHIVED",
        )
    ),
}


# A flat string-to-string lookup is the highest-value false-green shape: its
# local name may be completely neutral (``data = {'apri': 'open'}``).  The
# discovery-wide detector below compares values with the governed IT/EN seed
# corpus.  Surviving technical/data projections are bound to owner and exact
# canonical bytes, rather than exempted by a suggestive variable name.
VALUE_BOUND_STRING_MAPPINGS: Mapping[
    tuple[str, str | None, str], tuple[str, str]
] = {
    ("engine/dispatch.py", None, "_FS_CONTAINER_PRODUCERS"): (
        "a1e88820df692d683554c85f76e8d027b94ec8a9b019f52e14a5a0fdbe8b9ab7",
        "canonical filesystem producer-to-argument protocol projection",
    ),
    ("engine/dispatch.py", None, "_MASS_ACTION_KEY"): (
        "41b5955021553f0e56bfaf2db10e1230ec00bfe80c3d945bbda38c12ac2ad1c7",
        "canonical action identity to i18n message-key projection",
    ),
    ("engine/dispatch.py", "_route_mail_delete_to_trash", "new_args"): (
        "a0484f41541fe310b4bf18129ce84a0e5a2b361cad9069ffb551567bdd11f344",
        "fixed Gmail provider label argument",
    ),
    ("args_extractor.py", None, "_LANG_EXT_MAP"): (
        "e6c1e2a15494f5a6bc3ab994c321f5f048c9b0714d6424b17670963f80f25c12",
        "programming-language identifier to file-suffix protocol projection",
    ),
    ("fast_path.py", None, "_CURLY_APO"): (
        "8279595aca5e6dd9e45741f30100c0309feab4185723112a7a9b6152e0f35780",
        "Unicode punctuation normalization",
    ),
    ("agent_runtime.py", None, "_BUILTIN_TOOL_MODULE_FILES"): (
        "58f2bec34309adffd09290db61a91d238cc85886fd1d79fcb28f4ce40e9bf278",
        "builtin tool identity to module filename registry",
    ),
    ("agent_runtime.py", None, "_BUILTIN_ERROR_CODES"): (
        "d31d81c933530f857874a3740159dcc92994924d34552469346de76788761c17",
        "builtin failure identity to wire error-code projection",
    ),
    ("agent_runtime.py", None, "_CAP_FIELD_FALLBACK"): (
        "7faab151b762997560409b7ffdb351965db70666d96bd88032f7e3427f8948d3",
        "capability schema field fallback",
    ),
    ("credential_intake.py", None, "_CREDENTIAL_CONCEPTS"): (
        "6137f043910976e9cceac128dde1bce1a9baa39de30a9e2d72e60074689605a4",
        "credential schema field to governed detection-concept identity",
    ),
    ("system/admin.py", None, "_DANGER_MESSAGE_KEY_BY_BINARY"): (
        "59d6b0d8cb91f9f9bd71b197f5b17a6c36800ccfd0e9e471f612d6b9d28032ef",
        "canonical binary identity to governed i18n message-key projection",
    ),
    ("skill_codegen.py", None, "_STATUS_WORD_BY_VERB"): (
        "f1c6e8f2d8a56d7f68315fa9a71e99c28160aeed4ffb23a54cabd903cd787afa",
        "canonical action identity to generated wire status",
    ),
    ("skill_codegen.py", "_passthrough_flags", "kebab_known"): (
        "7ca93c291c5b38337ae2e0e4efa46179c2d8d30234252389563e0ffb314d6027",
        "canonical argument identity to CLI flag spelling",
    ),
    (
        "playwright_sidecar/action_resolver.py", "_target_variants",
        "semantic_targets",
    ): (
        "a44711c1438f6282aabc6bf708e44a0dce349cb8fb6cc6b215047601fd60d03c",
        "canonical browser target identity to detection-concept identity",
    ),
    ("telos_lenses/scamper.py", None, "_OP_NAMES"): (
        "8e14b2d5e9e165e7d3a841b34a18e3e8508a454841c0a60cb31b631eb8612ddb",
        "closed SCAMPER operator-letter to operator-name projection",
    ),
}


# Bilingual literal sequences which are data/diagnostic identities rather
# than production detection resources.  Their exact container kind, order
# (where meaningful) and values are frozen; any edit is reviewed again.
VALUE_BOUND_LANGUAGE_COLLECTIONS: Mapping[
    tuple[str, str | None, str], tuple[str, str]
] = {
    ("prefilter.py", None, "_FS_EXTENSIONS"): (
        "ffbee5d23d4d5544a91964738ff3e221a346546eb5ab6be5b2e965757c52e57f",
        "closed file-suffix protocol identities",
    ),
    ("smoke.py", "_bow_intent_for_smoke", "calendar_terms"): (
        "57ea4daf8750d613ff7f4724645702734e3bf36ab9764490ed338db3be8dc97e",
        "bounded admission-smoke corpus, not a production recognizer",
    ),
    ("path_alias.py", "home_dir_suggestions", "xdg_set"): (
        "2017fe556c2b4de7f7f5d503b6e544242a025b7f27be4cdc9d48d8dd6edda50e",
        "localized filesystem directory identities used for output ranking",
    ),
    (
        "engine/dispatch.py", "_normalize_multisource_entity_report_pipeline",
        "generic_scope_tokens",
    ): (
        "14162aaff2b1b1ce4fb1bef50c10095fe30ff7ffe4239dcdd0871547d67b6775",
        "negative scope-leaf exclusions; never positive routing evidence",
    ),
}


# Discovery-wide executable containers which the consolidated RM-0005 review
# classified as canonical protocol/schema/provider identities, generated
# output data, or diagnostic corpora rather than input-language recognizers.
# Every fingerprint hashes relative path, lexical owner, symbol, container kind
# and recursively tagged payload.  A name alone never authorizes changed bytes.
VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS = frozenset({
    "00633b553875fe43307e5e52208a7a338799a84578db5bf2467ea977e8a99cac", "00689c8956c5bb7a8721445388cf3cc2ad8449d3799b727ce5162a490bf32dc5", "007243b2af7969fd8d94fdebdc9eaac18d701e8d1ec3285391318c9f41740b4b", "0076ae91fe1b184524f41b21bbcc04c11eb67ca6f20eb72247fb3ebce648cdfa",
    "0147761646e8899a248f18a30b2acf7c83631794878ae82d01762cde87e36325", "01ce9176afb9fce5e743e549e23c9a4ed4b7bab55d1f82486f7015958878f9e0", "021275e3583682cf578087f126fb89f32d2b2f5c564bd602bf1d72988c55ff01", "027b8d4e688a7e2f9e70f0a595d463f5bbb7e1262b44d5fb091b72a100fc8e99",
    "03e6356f34922b749b54555c1a743279470072e8dcff9ffdfdac03e1403226b5", "03ed552691338b61006d7729a823ae62bcf641e28fa3a597a72d15dd3028926e", "03ed62778a8794bcd3bb581b0b3b4cfbcbb39f8789cfc6a52f05f3b6536c9059", "0598ac65a25af71b76addb53b755ef6fb1bdc32de3422e7a03edc3651abd815a",
    "06ea8a5d17f671c0eb13cc8d73a47d5f0e0af631c28e427040aa59d5f2999ee0", "06ed1c2e1ab14e2318bfbb2d685807ef8a434d1e38ea77d91edce8ecfabe36d6", "0727cc8928f188ac7f5c6a01f767bdc5cd2e2a4965c5025d2506a8d60878997d", "0745c667c6b5729091645d02fd0b380fa15a2b9008fa885f80a47307addbcd44",
    "07e64c7c7b97e110a45ac73d16ab13ef4f530f587c23ee19bdb129a09a6e5206", "08263de0fe9226e32a19d44f68c454039d822cdbb15920c0ab0f11711125cf5f", "0843a045989397a467c525dd8d58182d045eeb5ad63a86e01e6a2511db157221", "08a9ed6775e859079927bf53199640df19a6d5cda6ac43c97aa149329ed46805",
    "08b89a903a603c21e3dbc615952045c9fa3a25a2d9117388f00498bf963db4de", "08cdbf8e793bee0f82a6f52def282e2c2814d90b5f400d0168914d28c8214254", "09ef2773164a722ff28c8ddc020fc50f36b296f1557501c0542e75879a0b54f4", "0a184bfeedffb1bee6f47d8d205c9eedee06c33bd8cfea49f7e675c3786a1f40",
    "0a4de53baa46682823cd96bad328e13b0b6c696144b16f152eb31183fe055b73", "0af0f9cdbe13f79000ff78d82f3c40b0d92798fd6f79132573f6f49a515dc632", "0afb53bc2f05cdb24274bb204665d4e28598c470d9c904c027195148192f1d49", "0bb56ba1909489dd57d8d550403c3c3e2d7ba55b7a23eeef3a6c6dc62252abf6",
    "0bd03e1bdab1a3b89d1297263c57ec7c0c095e016c41e30d9eb4abd7ce8fecbc", "0cdfa1676a5ac836478573e07e42664344e0470bcfcabe604af3b9ef963e6300", "0d166f219a6dc0681487aab444902d24c8e2b90bbd5ad165373afd5cc770bcdf", "0d1ab771dc2ff85580baf244351bf38c520dc8d1c7466a731ca7a691bf282d48",
    "0d633733df436b21ca4049c90fbd94f63c47374b0a70f3de807fa3cf54625e56", "0e2ab832725292713e804c89a6072c9a44385c59d8a0487cdb2e75244359af12", "0e4433378d383eeb8edaa9c714cc7d9fc45039d7c7e3102046fd26e1fc79984a", "0f153efa184eb103978e9ed9cb2065e89535a3edbe800880e78e115499348e50",
    "0f60992ded6524f334f2efaaae438d50c801cab3e96501993b3d042c9535231f", "0fe5c8bdd554893ee0ffe60a691d0a29935f1aaf9f908c20bc305d39002bd454", "103925e0701d42cdb5e11ed76bbca277f24588e4003fd04190b02d4f171944a0", "1171d515f118a9ae28c32db9b93991f34c3ddd5033a3fcbb2f9f4d6195bc444f",
    "11acab9fdb86c5af861ae7b215123720eaeee17f797c8895bfe0eb2ce425f3fe", "11ad25da7a52006840fe5591781617838f30e327bc8d82a66bec2fe8b0f34099", "11eceaff5f58fe2f59c77c2294b2570f32121bde21efbf82ab680170e72e448d", "123c5eefa213a592ec82e578be6913ea5bcbfc27d740fd3838cd3c67a4310af0",
    "127e9b1bfde75bde6fcadce5cc72b8d4c8910a8311854870c22ac38c5ead138b", "12f261cbe1081a5301a507268722eb5672b11b31589a97cc2f26c8ad1a3145a4", "131a9f311b6cc712ce4826bec14f67d0c7ec19ad93638d6769625c4dd8e0821a", "13390b8a495fc13f0162130ccadff0a60b7a2157591ab703421f5be479f192e8",
    "134c46ef8f500d5d15617e229b1178fec7d10a45df75a58171b0c07ca6811016", "139742d87d27c648e06cbf2d11b77d5274cfc01fe31adfb9bc32fabf2ea353ec", "14710dcfda6b8436d41ce2d189d05c2d99e455244d890d9fda3611d2d8c0d96a", "149b656e88faa99426487034f652acdd8bb73244e12baa7730813b7b8400001f",
    "149d3160f43a0eda5a5a15086772defbae62d18736b3fa39fcea285094fba879", "14c1b5680f549cade788594e8459c1c5e9b9df29d220289d5f4c32e3e1887b11", "1517140e586238f0555abf060a662277fd66c35adeeaeb5c2289c05f77d28781", "1529887ddafa1d1c2ceb93f9941b24ef444272e2e2308aaf400b4c5d4e66c193",
    "15426e8b397b1153e6782c6d1efdc82b96ed1dc850a7049d6a12fffe431da8d0", "158a4cdc4ab40ab5d24a726b2522b4bb47b7a47d81a331ce5b9eb4f7e537764b", "169278ddf44a27d547e31a014bc9a8fadf880aae1231518ca5a50d2cfdd3f49e", "16bb09ae07326a9c74b144eaacbd77b478eceb618bc0a0892d8f84d6c0b66008",
    "170432e921d539c6a2bc9f0029f6aa8d0cc42e624c3aaf4c62c7dc12edea72d9", "17607aa16aa42dae46b93c40c0297fa627f34d61a77d05b2b6823cf9bb2257de", "17713f509cd0eca941f1b653d5855287f330c649a1b79ce7a06de372b5ff7aab", "17d44f6401bfcbd314ec40ec3c7ce141e2cd556fc8b8bd8b6bd76412eff6adb9",
    "17f855c3015d83978c13d5acb8b7f80680e35b87a42418352406a162f435ef4a", "19565c84b10a48d8cc2642ac3b1916c4383adb766d48b8ef8f65eefe654bdb5e", "197bbdc07b12629d1c91fcdf96f213cc6f31847024b986db08eaf2f02963f65e", "1987ce10327049f1f86f9e5de74abac18d410f51275379d4bfd596e4ee70aac7",
    "1a519cb2a729058855354c0edfff18a1757590d71ab007e4acc7923e6a57b444", "1af17ced36ed951d9697d119bd74f53fcb6852600c4315e4b2a6d570e7403cc4", "1b333b43997a995e162b78ac3e1fc413c5b68a21e3ac6ee51ddf31f4a92cb016", "1b62632bb34bf49fe2e402f01114366c351d49ff1354d6076d1e4d28f7661c7a",
    "1bc97eded1d5cd43761d3ce2dc9ad6df80341d2e0f02967f6f249f70c932f896", "1bcb95609847fc3e516777055070d591c3774d74dc2a634b8a701423ad2c402c", "1bd5e94fbc072282723535b291dbc94296ef1e7fad3c7eb007dd4f94f95f1e1e", "1c7d540988852aa0d5e9dc361be2bbe651d49f05af6b3e639fd06f27cede735a",
    "1ca27a89829cf6ad3585c639ed5d1bc336980b980720f590fce359aa4e12fb5c", "1ca8e467ab5dca048254a94cadcfda7856003f3dfda351855bc996ac3386b228", "1cd4ed5db2faf6a7522f0418c77928ffd8ee99c3ff0ded60ece308aa58f508d9", "1d40966c5469558cc32b6dd346ff5f1c481c657d975098c6b4161415f81fcf1b",
    "1da0a991271da254a297f9bbb688fbd117c79cefba4f3b7f7bb597f84392fb0a", "1dc82eb1a13ae0509df5ff1816050af07cebcf05aaac57e7049dcccfb34d9f54", "1e6dfef7e288c4572d1a6b013b6cbe856eb7e8dacf84f31dbc46601cb96e93c3",
    "1fc04f7242f9829deaf9899a85a1fb1220d3e3f83f6bd6366188e6bc9a8d7a88", "1fe892d1315467e7e9b7396c39ba64310b610c039dbbb08c7510b5451044b4a9", "1ff778f05fa491ed14db802207583a5d23247754f5447d5651298671f7392dda", "20f6e3e5e235e28bee20112bf56fc09894d8b2976a8fdbd871471ea34c016b83",
    "215d5178fc1252f4e3837277d8cbee14135c7e3c283eb619002dc945d62a061d", "217696fb3eca53f8b12244fd6edb98934edb71b7eb75b3ce3e6bfc0c02c51e8b", "21976e702a92196101035620ae3d685a1b105860aaff3ed4b9a046c52dba63d2", "21cd0290f434bc0f03bacb2ec5b7622b39a07b98f7a2167de7e14c0d9ea842fc",
    "22a18ad1099c1c01e78ed0ba963de57daff6b070a1ab5138dfd3f5e7ac513f1c", "22b13952fce4872b9c50746b9a1ce8af0dc2f0d1d20d3412b9b177fa1b1f1cb3", "22d33e4967c07836931f066c0de2cb0ddbd4a5f4273545635b4dad204beaeee2",
    "22d3a984aa81f9021a3d0eb6e652aa1c8c1aa14c0685e9bad3c762d758d0fe6a", "23543c7cc41c001f6f444a2a57b7ca3ea01c0a211e0cca6bc30b2e2de84a7651", "235bd219c7655b73a7ddeec367e56c8c83b2e1cf52661713c25010b1c18f8531",
    "236ae258e7758a7294ebb05c72dc2334311c80d868dae1abe47675c5cfd408dd", "23a727c5f020fbcc1fc0acc8271a50b02626d165ce88443e7e7cf6203c8051d9", "23f9b1c42507623b779a4960286862ac6784e28b38ef87a1cb66f9ea5db04cf7", "2463b53d9515529cc585533938408beaa18121231d8c74c7ccc8f5829e84a9cb",
    "24b108b0b6ce9cd174580250ce9e24fd71bf85abf6d334de22a026eddf7de103", "24cf188ae031be1f85e580a2ff9455df05f1fbfc8febcd1540e89b7f2a261332", "24f3bbe5e1e92ab142c74e507b902b6d8d1e68b11a63957957d148c2c64f42a4", "255463a8ac7c8c0d6bbb8ef0bfafddf075a07c14fcb4ea5f56e50ed96360b5a9",
    "2564436a1a794e1190ad2e1d72de9bd7c7f75edc74ad4292d98d897b57ec3649", "25d13dc94d16a76ce6ad7827facf5df5b9e398f39f7c2a9f7a40c920eb956ca8", "25dfcf02085a7f5de3d2d1e60c5eab7cdabefab38f1c1750376ef0e25df431e4", "25f09e9d462754cd020f8d109e142fcf9256e5b7767522494ab6243ccb4a4b9d",
    "2609e4c567efaf2bc7c08568ee7ef03be74769e595e5856c12c6bc00ca5ddf0d", "262b6d2957aae3d324b2ef71b2fe2ec234a454bc4ecc3f77e14eec68fe77b264", "26a869bb6cbfb1bf3a11fda4b12b24ba7e1f76e8c29d8f3639ed105eaea6ce09", "26b2d174b1a32947c97dd0c22cea3e6968e27c6e0200c1d1869497d1ca6d56d2",
    "26d5ea7f24b07ca8e43fa44c4ecba500eeff61b43e72c53ea2ec52df31e69812", "2700219f915988498cb0c6e74eaa73989628f78e3793165e4207a30687e629fa", "278e9043d44ffc6ed30e07a7c5ae4a54ec7988acaf4ca0a4c0800ca0371244ce", "27e76b059f9c9f96807d04dd5b0e23b4d4a599bc4eed20e933a6ae6632a916ce",
    "29422a32d3b819b238982c849431c1b5277475208d91222bad26c4f8069f7b53", "29880f7c006275f0db9c87cb8dc1b5012985c012df655ff8edbb79ef09f97c05", "298a88dc5682ee89e669b49cb00570a18737f91ee0db759d9e87ce52a089c64c", "2994f47a9757c8fb061996506a4fdbe566b03944f3beb99fffc10ebdb12c92e3",
    "29de2f75f5246feb8bd4728a9287230091bf56ca460efc2c947c994f5eca2ca3", "2a321c0843aebd2b8959c67cf1901c7cea0ebadafd48f14cad87d02a403dbbba", "2a3765cad825d1b4e441552e9a7e7b31eaa75f34f1382745b50ec33ff700d6f0", "2a61995d53e8d306342e8d0d2248caa33d8effbdfd8ffbfc04144a1d93902521",
    "2aa23d9e179671f4fb8c87a3ee8effcc9b7bae93cf957efc3ba8de74c693be02", "2b8d731159dd919229e75e6c20054e8a2595941892aceef2fa25dd36048b1e60", "2ba696dcb9c8fe32f1fa9717fad55bda63cb95ddb4b5b4b1f112f3afca4e8431", "2bc343f62d32dbe2fe8faa5d724854ba4b7faa6cd1b5193342d76b058c2f04e1",
    "2c5c2edd500e5b0b36224f328baa6c45c0b8ca5955bca246ed7cccec02b693f3", "2c690f089fd70d0b6e6d0dbef05a4e79c3f51d037489f7df5680a3d3cba3fc64", "2c7a47bc7d7d956eac5c1791f0527e52161a3b9c69ccf170f117cc0709a0bb38", "2c7baa938a576b8e3756e252af2d813af544d2db0bc422523abf001b2cb41bff",
    "2cfa7324b546393d3ea281567ee7783d0e00aaaf2466642207225254a7b81194", "2d1b0de516cec67d842628cd4713adfd1e29905fbdf8371fbc6280ccb8ca5b56", "2d5662fd4d1e2f560ba9e0bff362b87cf3deb111d1d5feb3d79b1aa8beb54d30", "2da20b11d35d1a215a411321255d4556ceb8d9542be9d48bc4bd89aa7b836eb3",
    "2daad113bc6a3f0c4ab4ea6d7e38f0140cbd4d33070acb04829e5965ba1ec66f", "2dbf035d82c3e0913194b7a911b9c99b058945d8fccddd1f757ea56c3911abe4", "2ee7679d27697e0e30002717f32f35e619b79b33ceecf803c2766c675be7e20a", "2fff45bd3f5be808f12d4f7d21e67b89eb5d30e1235885aa8f2b93d610036652",
    "308dd6d5a828d8d6ecc8355e23b44b2274b3b2a440b8d1d74f7aa087027a8674", "31bfb81459f3ead9f42cc05b531a177eb594f5ba732fddc37fee203af201e903", "31ebb6c914f46c77d425aa927945f57306207a8fcb2036e31a6acfc2e4806df9", "3200b5353484bf64e4ce7a079646c140406c6de34b6bae04099d9cc6eef2f903",
    "3207d52f15ae9901ea3a269417a8bb80260079354c16b3dadb2ba1c307a7c259", "339851e9384f65c4fee4e43ecdc48480b68e808ce59f00170ca941e626538370", "339d4e052102c430b6ce0ac5682fe42f1543695215e670c55142eb1baa57940f",
    "3437c72590309a848b8d3c9128269a12fb4b9ae4ebc0766360274d9d232294f6", "345e029ce50ea983d1e0a7d8cac784f53a471a5e44a7f2b03480ae50570ae7a4", "34cd2107b63785700724b9b23c474c2f8491c509a8356a7b71e7e0f4593b8a3b", "34ed7334ab02a4af2848dbda4b50a962b588538e6a81c1e15fee98af850f8e64",
    "3506655f34307f36b1a4b1fedadba42d0193c10749956b492cd4b9078541d0e7", "35137f193eb37572ea8096e9c67efe7acebb800b5dbc799c0ffe27847beae2a2", "3591ffee4aa03c98a06a67cf37a2dc6df7c8c6b6ea453f12c42ddc01f41cac70", "35c5ce1876d9e80fe3637f8d11dfe1fbe880dd248c5eba480934142abb252839",
    "35fc11cd042a8465f1d43c172c6638f8ddc8b42da9bd947e474aec3df08613b8", "3602edd73f256cdc343329b92434f452ba5bf3e2f45151968d56498a74c650df", "364b14730a253d353930ae7e1aa036b852305208e2e98addfcb9bf0bc5d1d7cc", "36818afe742a93cbcfc05849581d5e53846c6f2f2437e81e3724ddf526495cd3",
    "36bf4dadd2677c0e7cc72337f9e07cabe496e3f6b113f91325c8f3803f4cdc21", "37a100e316d19812c27a99081c220a199e28817e1348ee75ac8c42139e5ba076", "37e7a698e52acd214f11e28c2f3bd812f5ba42bfae120d95505f6906c9b4aaa9", "3893598094a595fc0f91da848b4ec5aeb4cd6d4b992da70ef1be2f88aa33552d",
    "38b8ce81a40abaac7bcdb237253b7ae073bac35971c6e94b78d3c9de29bef804", "38e8785651e9db5cc3ebef2b0b8ff7a715b61e908dced996daeaea7235898cc5", "38fc9583be597b58f65f58192ed2a318e63406e227edcb8e738e788a8bdc0bdd", "3989662b439059726dcc4ad939c90973da7d3a4fdee2120519777541673b19fd",
    "398a6d5d2c7f6f8ddc9a205c7b9729bde6c960a5e6817838df9de77dbdcd813a", "3ab9fb0e82264ae675332f9f8377bc102cd614acc6346b32f1bb86264439cf79", "3ae8748dc93ad2afdff006b3638b251ffef5dd467baadb273c3af2e83ba6d118", "3b5c25f8fd6bb790acca5c54faa12e41a8bc0cdcb96dc967b3940de2b6e5785a",
    "3c5ab3d5b9a79de64dd1a0ec51d98131947b13d2820b6b374c818d4f7b7500f2", "3ca9a94964218d2d561e5dd0078e47b309435df06f9d98e02befe0b2e76f3382", "3cd68f217f6248569267d509e6227df27973076662530217bbeb2347283be21c", "3d7be69538745a7a1159562e92cf05aa3648b7437a23a542ce4f9e2f150c0494",
    "3d94e0a23fbb70e744e76fc2e3ef37f06263d69da0dc28a3ff481d2f57f24840", "3eacf5ec00fc115328cd77e7ff6c377162aa895be6645ec6c2dd7875d292dc52", "3ef045257e327c68150cb2e129bdd31a14bba7cce34207c8fe372d93744ba4e8", "3f98920b00faeda1a345d0c7a6bb3d30435c03a75c5365a8039bc1d698528d95",
    "3fed6a619d6b87305ddabe37b639497fa0a94636c92849126ea1273d5dac268a", "4036e9b4e5bd839ccac0b501a506a39c766cf5d3507410664599a6e9a6ad36bc", "40a1a823a35318a6d41d2484e7e1eb9ef610aed7e7f49da72eb898f6ed0ea572", "40d172ea49df5fdbff7dd27650cd8df9d618b7bbffbf4839427ea56ca0af1f40",
    "40dffb312eed363efcb734dcb6e2a35b895fee7e2d630c7c1aff8e11e9e739bc", "412fc1e04fbfa00b5ccd2ca4574116d874c69a6462f502a54745e7bd56534395", "43612cc78f74b594eec202f5f2b2cce9bfdb51c113fe98baab2307174ae36e9e", "43d55ca2b856b628690a92ef9dbedf62ef617f73492f6510d21a2ada0313cacc",
    "441b20a85eedf98d85e2cfe0bbed1d5bcbdb3d90946eebe7484b22ca5d96778f", "449addc0bc38d8b4163f19ba7d16a2a76fe44cfe8eab28d01a75d9f238adf81f", "451ac7d000612216a0af7295b72dc19a021398b67d5e2297cef532b5ff577d07", "4549ae1d54cd0b317d67058f9ce5ce02a35fb279844e26e32a6849fe8de22589",
    "458ea072d887ab029f0d86b82fd519249a16f59361d551115575e6ee2e7beefb", "45ab36ba6961c28cf01588e1998bfcfcb6f2afa12c8a48017e6147e77a1ed456", "4676c04e809951a769349f00846577ca2ed860d769d1aef5eeeda6d4762b7ec0", "474fc18221c812dbeb5eec3d8c13d366d383489a5e1040c0f61adae35809f01d",
    "479b73618ddb00aa892f7eba7b9915350c5747ce6660b3e9bb5083627eedf572", "4819322ac00da62d4aa2431b6137c85c1c58740168bb8ab1a9622737da9f1c7e", "484f37176eebfe3c6ba06c202710fcfd7fc06412c8e1d54473e9477dd29489c6",
    "488f1977c5f28199f07ecf237085e1d884901c67596cc6e0422d5b10ded0eded", "495d5bde9f2448ab2e177412e3206bbc8b75490434a7f165cbe278bbe418d006", "4971c79f9e54c805fd3a2b44aea3d87bef122630e6fd1b5b94a1ae94803ecc75", "4a600b9b4bc5dfc6a048abd1feda64674169846722363b66dd8ab08eb7f6f6da",
    "4a707ff9b01734fb625e5f3ef63653330fc79c764520d49e606ab99561decfd2", "4ad993cee11d2dd2bb385ab4bf6a25e50a857ad14264d57fedfab2f5dfd4ffd9", "4b327b53c2d07b4c985ec559bf64d24759d682b190f0a142cbaadd8a74d36a6c", "4b8558a1b0f5a076227b4b0b0255cb632067dfa9c0121500bfaa1468dc8faa60",
    "4c329d576270c8a0a618d1a499c5ef56861139941642aa3ebda928d46453646f", "4c9a555b314cfa5b46c556bc08ef7e83d590dd1c87108122ac68ee3e3bec35a1", "4d6be07f96f8eeb8eb940fb0424f2a7ab5b7bbf727aba82abee6a6ce13871982", "4da3b779a9a70e13c46c05161fad24e37529f6514614e2de04a90b65979d6844",
    "4e1ee9416b914e6964560f0826e5780e7fcc851193698c76b24d134da97e40ab", "4e5d3f4213c2c5db6ad3966149f25bf434d58e70b667cc43f6f67a0965004772", "4e678e2c5471cec542d78c1846b346346bbb6714fd2e86a3fa6627a7547d11b2", "4e67baf7f922e97aeac3dd0ee5c21d95d9b6c6e0983c80e40d19492baf9a93ea",
    "4e7667335df0f78fc67760110e5357c569a7e14e36a1caffdef74ae7f436905b", "4eb4c72826586f7fefb53fe471134528a5d31e451f02c38d8a0a76c35725afca", "4ebbb516293e5a2c0e6e51c3a64e42aa154e00e036efce132bb064a9141c2732", "4f7dab41382cd5fceb475412d5008bff195019b6e29c29f6a191166f52c9efb9",
    "4f7fdb3b812c4f66455719fa3e0c5357de6141488e0f1230fc871308cddec5e8", "4f957719b06c373d1f12c7131e5be2a33b55962a7d767c22cc8f35c31f16f0eb", "4fab1cf08fa573a80ff8f01d733617f1051a267cff357878d62c7cae9e5b7180", "4fdd3a58c640f3c4685faa1c4c7366035013d3ead64995161e5f64e2381dd3a2",
    "4ff5261f2f9cf6fb5230cdb48a425cad863dae35f44ca0587b8eda3f1dd84a20", "50043102f12635c4be55381794320683fc276043be1d872bce8fab661c81cd5f", "504212171d9b1e1ad4fab753affe63d7d0a21f77bbf05b6d347634ea43ac5547", "504d720e9fb9351ec7262139ac0f639f0c81136d9d79a429ccc3de131662c4bc",
    "509a56d226be314a40c419b9ce957b1924037149f0644e8d0d126801165711f8", "50fbc387a06e9ef5e2b8ccf3b03e4f6865c81e6bdb6d600fa43f00724e421ea0", "510f0d27a740e0dfd49582c93659f4f098ca3165c06c38e512c0b6c426364ac1", "512e9ea6516f1cf337a35b4bcab9953264c1c56362b8c131f45020ffd78c3a06",
    "51539161809fadfd419942cc57210f9be14c5da1852e47d3fa3a894cf74afd57", "51ace0bf4a9e897db3e4d0863c3872f57902e0b591938258c33f5ba2ee546ae9", "525d6746acfb4e812a40f72101c9d6eb110cb257fbac4ca53ba37ce2c2579c32", "52735e4539d79cb1c74e51bc7c6ba9f01af3b432d3f884a91f0ddb475b212733",
    "5303bc4487329d7ea435311c16ca428f6d690b4dfc2746552ea2cd1905989376", "5348165b18112abd14084770d07aa3313c1a496bee428bf0d6baad872eb0d6cc", "535241c92c081f2246235721ffe70cfdd9511e767de2333743bea7902e6e52e8", "53871fa3790788219a1f61c246512773503b52466bd6cb53c3168227dbfca321",
    "54471872d4af3101382a091d90f74f3625b87285426e5432fe86b746b84b7761", "568c2eff829f51318ef9f4fcfee91fb2b51659358f49ca67a9296e921c0e19c5", "57304d00d1a71aa800c4bd16c1bab451b875b80f942fa3e397236b86691b136f", "57acd9293c17e4b643c795a2d450fc395c2c21ca0bb80fa27f6a060828ecdbc5",
    "57b3c37e117f935e3de6378c8a6af00d00b8d491e3b570a9e58e6f8bcebdc4d6", "57daef83b3076ef1e996f02fcc44dcf1a020c46938482c04a63518b03599aaa8", "58286596fc6511e9ac2d1f9f97124edee044766f6af0c294ffb5e9725a77f0e9", "585f1ea01b2056c74575a50275785dfb612bc846b10c9a2da9c0135e98e0c7c1",
    "5899f468c20a237f720991d0805b06f80c863fd1ee715331ca21ae15a3bb3618", "58a0786cc195fb7717acaf3029e3a70a55963b983c0d434130e53a6f5c89dd1e", "58cb09486c9d93b4c3f7486bdf963588f978a583818016a9f9325d0228bd7828", "58e618bf328755fb9a7214499ad5ecdc4d44ed875fd51808fa7565350ba4dd22",
    "597d3da6057cc3dfb12a3c824ab79ea1a5930842f22330dc64cb3a195d43347e", "5989e0a4c7c623b08a4331fc630a9ef955d03dae8ef3b0735e116b32be0c1580", "5a0f366b8eff17b9591f5ff22b242c1c4e8e947e89764f52011f1b68ecd04995", "5b1674b3272bb6d4acd632d7de69e22ea5fabf9d2aae90ce7febc37049c33008",
    "5b2583d7dfbc09f76eab7256d8fa3883616f990cacb07ed9c50ebb16c97858f5", "5b45cae9f2720b683e8b5f93dcd99fc992a332eedf47d3852554aae61b65bf25", "5b7645068b851a01cb4b5eb11d2a2256fc72acc58cf41fecb9828521595fae91", "5b9912c61b6e29a85aee5001c4feed93905340d4cc172985457923bbb2feb9b8",
    "5b9cd465dfbff70bc32dc9d5a35fa2b5c9cbb9209d73ef392ba33a78651f518c", "5bd65631ca3a8dada398f96234571e72cecff050dbe951e407722ed7023cb09b", "5bfbadd7941722a62d49e80d7c5637e67fb4d836444aaab3cfb495aba9fbafcf", "5c4c1bf17bff6090a8a0226e6cb68c9596f459464651d13851bd79bf56be715f",
    "5dd831f7226251c1cef4de7176a9f5c7ee009647830ac8555ca822655a952486", "5df5d3bf41ed65149a6542b2580007a4c5f1ea52945a2db9b1c4df2117f80f05", "5ea6f0e01a6cb5f4eb53c0bcf359fde337d712110741bd3b2bd8dab48eb3c985", "5f18e74a7656831ba7df9d85714a2f5bf872e8f5b846b2c9bf90b62e2a601ddd",
    "5f2ea423e8392b73fbea2904969b6efdff4dc2406baaf1d72a722643ada83d6b", "5f5ed1c747780aef0c03a05288a6b3922ff4afaddb9ce607c36dc87a6eec3367", "6005cbd86b89bca3099900847456f2b0892b7cff1157a3ebff6f64889a08907f", "60126ab6f56ac98dfd9ed1b8cbe361f18ddb2b26c8561274ededfb453d2041da",
    "6024ace164f8e45f7461f51d5c183a039307eca1692b94de9ca8fdc2596ce337", "606a43025f360ffc817ffd8be60efc9fe9979d3a1ef17f894d0df546ad259955", "6087f5763ce0c2a0f174f3bcda9e7b72cbd469e8ef205f5baef020bedd99ab6e", "608ff07ee3c1af8d6534c30045caccdbfdf9e0764ac521f26e5680570823036b",
    "60d061832968a374302e5d37f6679102e8609ca2b37637d4dc3f567ea9d17e61", "619b577e93b165bd391df44f1d74a0c4af003eeb11dfbd599e66e73c1c939db7", "61b43b9641193ff0f827be87368a5901d7fc7c1e34ca4efc9fb87008c304092c", "61b5dec021bdf6146860d92a34606ada11c1072fa200c2282cac2f0d6929f1df",
    "61f7f96a605e5e8a302cd8ff98bfdb2fe951007e2b534572eb25be3e6ca14643", "626653fffaf9155028ad5fba7d599612ccff9ffd2387bf619aa6c7b187042120", "626808250317c831d94a7c89f66e1d959f2abe7b70d560c455b33b0ebdd70bd0", "6269eda1360f1b4f3822519369ba517c8469eb4409a9fd2448946563c1adda76",
    "62e704ad08b52c9c788c438209937165de53f88e9d485acbd5e110eb3cb13fd6", "635fe6ffeee65a15eaf8b88b4387bf7050dac67a13a0138fffbea587cca6bd58", "63a88a1e58a125284ff7b899546b4a93784deb2847b2c37b2743ba64604877d8", "63e6ccf45edae670232834c9646121d989f25eb3fc81910601d326861a3902a9",
    "641f84c912c0958c3651f69b073478e11e4c44aa78c40d40b8dd4b519cce7a20", "642aa1839630ab369504e7173ba646a9d7b4e8a4d83b5a161af19e1a4344c03f", "64645a200aa670f619da93c448673ebf3955a323e8b8cf9e2cfc6b36ef02c719", "64aacef028ad8ff751556686124c7aa08d2bed01d7baffc614d3d8402c0f1732",
    "64ffe7fdf81bb156468c726e8449d92318507331c6545101fafd0eacc5ee6cc7", "65ac77a3b02fe95ff1d3726561039a0f382e8cab49b2cfda7bb1936e517c3c49", "65be0e9ea23b5665d0fdb0b98829d6621c97d68efe6969344b4a1565f92f79c9", "6600574bfedc44169ee1bf22b59e0682499e8d94c37f28c8e42d793549cad89d",
    "667d4a149f014d25395711741607c708adfd8cee9ad8591a379fb7c798894914", "6699375541ba320c0a6e5f6c9ca4bf4a006820bba2600f34ba5ad079bba8f745", "66e7272c7891fa463998645086ee083cea13da9f51946293f836ec3c460791d6", "678702062d9ae972e678e129e13082b950b16b4ed5d2294c479c0b42df166ab8",
    "6923f5281463208c2083378e681a76d5eaaea4fd68682c7aeb63b167be54eb41", "699c313576418d1c2b64ad3fd72e2c859dc0f38f268c665afa1a5bf9641c3558", "69eed43cc6c89b80eea1b87a4f36d9b39f84b6c7101ef202deff0fd62b494a66", "6a2a8426f03f2422d1674712f6c220cdcfbab158df1a944472a86738e3ee00f8",
    "6a3ca4d840756b84f315ee69712e9b1463209d764da05cf8064b54f98d3472b5", "6af40347836a4396519860264435995fecc15c93689f3b46208cae8e96405aad", "6b96ff0d575b1e17311f2bfc9edb4cc59b79502f5198422b894ea6cf5b578dbe", "6bd673c1fc1c181b7c72bc942365d3222fd22a9b06f91a539d8667a740051ef7",
    "6bf81393b19101f4d26976d705b93bc505a2fbea9d2339cc8c76c743aaf97453", "6bff65f45e1a3ba7452f744b24801ca9687609b56a7d8339acfac1d299b64bc3", "6c425c89b5e37b0391aa091806d327f072cc851e21565261d17f17fcc06d4fbf", "6d55c91f40a92cf57652eb174a4623fede8505cd01eba09fcacf18c4297d3b0e",
    "6df240221a2241880c2e4e9f9527a7f95cce5ea96db19544d81eecac3b84a1da", "6ece39e1030173a8aeaab2f79f90eadf384274146b6620bb00b6e3956dc5e994", "6f29b9a64dc7016c2833cced0597bcbdb0fc8aa977c11590f58c0d53381a84ef", "6f59154966179d39c0150ec6effed0e9ec7e180659b765c9fa10a208d08c7792",
    "6f67f153d88d2b83b784ee39023dac2d6bac9c4c1d9b01e1e67b7aeee4d0dd0e", "70499e649745f47800cef1d9e5e20ae5ca43cab88ec1d44a779c449497c21906", "7054fcc93dfab06b46b9a1c1032a103a0316c500d1d254aa48b20039409424e9", "70fc96dd2709a17bfba069ec593a87c7be23fd13a84aecd1dcf07c6a3d363971",
    "7135a156fe03d37086d7160713b463792ee2d148d5f1bb46b6d757e509546307", "713af21f77d3a83c2d70ea33340ab7f72f3f5b51c20348b93e86fa6750d887e1", "7188a0eb1a7a1cf4acd606aa043bc4a505cc5bf03c213ce37b25d83b9f8bb8f9", "71eefae84f2c95b3a10e8d017f73e15bdb8674eabdb9431f2e85f7508ae4657a",
    "7213a6d894ddfa6f6941804d4320287fdde2c831a5b9ad875a2238494a0107c9", "7218c42031e60cbac8cecbc535db9d0221c74dc1828a242845b838c572329682", "721af5435a6fb3d704cee717d399ad44d17c0e53a71b2f013d45a2b62bebae8d", "722f7eb41460a932d1dfb0f0582babda59008dcbe887f9461545ef49108fbabf",
    "72bba9f7fb12dd4f507186cd0ae79063cd12cd3fb0416747d83450af688456ea", "7393b65645c1ed1ef37298ac5238cce0e99b3e856f459b4edb24a9da93189afc", "739b54a6af3a704147670434742b404a29fb80f3995f985ba3dfe05f6192fd9b", "73e996376d071a5906db6071f0c1641e408d7fb9dbe52c0a4dfa1293f830dc95",
    "741ca598a64577c8971fa34672b1256de12a33b624bb926d89f713fd81831281", "7437b86249907f256a52adba48652376526798fa189d3866a69f62fb48b78739", "743be1ad132e54e308e0b594975d1e79b4cc3dddfac9e4242adfce9220016843", "74c9e4ef631f44ca15d1c1d8949ab45514e3de4edfaf6433a02f1142d76e9738",
    "74f190aa50c872126d673df7104640c6b14f05ad43ca4f2f24d3f75d67c9f487", "752cca6cc5e024137edd84fd08fcda52bf412bdc43d79931e2bb5d962ce206b3", "756747aa2e3be1714a33e85ec4ca2e3f62297b6ad68defc598f76f753ef59923", "75ad7f8e689d200da070af0347405c19251e1240de6bd3287081973da0f6ab36",
    "7706142ec0af841486daf8a9518efc1a19c0ee7213492f3e1dfd23e9f2e6e737", "7731a13de91f7ebf981ecaa352fed7b667fdf858397daa0b48da841b16964976", "77994ca38716fc6445ffca39c060ca29106434eaa16ededf60c7c45e9690112e", "77a90a13b4c14d61ef3fed069262e334cb66d1a98cf5d98eb2d4818e52c13199",
    "79324d36c54d353b59833f7e7df64d1b695178ac173e19cd42a35583fad99350", "793347bcfd3649db7dbdef3da5a7ddd124a2f659dadf02f94128c2b537594958", "794ac1ce59573787012a6df5d29fd99b153a2ee7776f568fe78abbae3f65fe86", "79689c4ecf19a5474022ae2c73ebe1d8e35e482eae3e91995e54295858740bc3",
    "796f83a3182d2cb15715d728fcc74a579c7d73038ffd8685960eeae9216e4960", "79bd0151f04ee9960e3a2788a7c7bcd53abab8c7738fdb12583121fb42e1af34", "79ca29a048ddc2878ec0afe3c5f203ae574a5f19a35373bf6311ddb0bbd48246", "7a1ea4b5ffcf1c7959af9086ac068d868aeb3ee829363c209211c33ae1b734f2",
    "7a1f405766485dd9f76fb8626733f70d66649ff6945d5db2dcd3e917e50cf872", "7a467038a55860596bf5dd4211c1194899d22f487e3ff84ca24edf40260b5ad1", "7a7cfed187c3a7ae17adabe1bd7b1c4279128b62a9e45cb7ba87695f86105955", "7a7dfbfac2011f407e8e2e5fe7176439d8643c8bbd7cc0695c83a34d7243b783",
    "7a98aaf66247c6328bb78d7243d8ab9eac6cc11ca154de3fa5713acc39828450", "7aa62ab6c18dd51e33f25ea0850799f0ada06bd0ea256f30900e5107e9a7d2df", "7b54a5df2508478d4eb26cbc4f600e498ca6e8995193be8406efb580ce91ac13", "7bd5f3f81efbe2fadbc0814da9c3c09d024031408f9aae6dcb5ff773adcbf56b",
    "7be4907e0c54daf741ed609bc6024a8682361389b17e2f78605db68056cf29fb", "7c3123e3909ed8ec0780b737e6556e152e454c0fffc73032631562c8b5dfb108", "7c6bb5cf5b448493a0832621f75d9d636628ceb648dcbdabf3178428121c716f", "7ca17ea1a542c0d5e73deadb78cdc9365f33cac0b937c78b8110b1d68425bb3f",
    "7d258725b2efc509878da19e076cfda83ca9d081ac7cda857a43bea96cbb2d82", "7d2ec01e99efcc142488e9e4544ac3112f7dfa326fb63adfa621a8cffbc2c1d8",
    "7d6018ce0082920fc52ce6e2d73e96d8b9d7accd812e51cad2151d68fa1b047d", "7dc1a1a5310680721813962e55c66b1e93adc5327f4d1b34ab71723802bc126f", "7e752970841f4008ef5b4ee7af91b977d00d9f872cc5f10f72a28f634d965ccf", "7e8cca5a27c4a545a4c7c168411f5c82da43dc37b585e0fc6c86e7c8f1260cf9",
    "7f1174944a8eb79f4be794f9d1feaa87652c528133a21c14da1e72fa48a2539a", "7f8654ef3b77a9afd3364d2f9bd341028fdae60f4992730fa9dad701eaec180d", "7f981db36e8828ade1ff95353c909c7e39bbc3cade9dc62b0fb3f38bfd0c116e",
    "7fa79031687d1ad27674fd5ad0bfcfd8880445456ac4494d3b0ecbf349594bd2", "7ff671ae9115ba285e2e9abb0e4987328a47a0b319eb292b4fe15ce7c9a468ab", "800b879208fb9bbccb571a93763e73580e0cccc7c6066dd400a37168c3adf891", "80a2d86daa9978b72cee04c1188593ed85d1baf131e80e3528e5fd5176113647",
    "80ae327b9e1abb50bd4cfc891dd5ca9050f3dd19c01268f1ba1826805375dd4f", "814f2e9deef23475e7c23f09229b4bea916405457ac16c79db13f689c4f0c239", "81507678d3f40715a0d77be3bf8b27bebaa11b8b7b269aa4cc15874ac5906e8e", "81833770c3f549b7cac220e920f5d3fc0c29150da8561a6c553d826b76511db5",
    "81a18e94120c1563bc15b78f5cd822cd498b6eddd23ce7f57391bb94eebbd7c0", "81b995ffcaa92745e4e9d3583e1c61852ad835094d6248cac7b535b24b06bdd8", "820b10ef551027ebe0f4140a892c226a70d7c7fd3ed34e6a6ca9131c30bfbd9f", "827b8a82ec39f0ab8416a7774543be23b82fd4ba6ab1164fa5dd79a93c1fdb07",
    "82d2b70c77a59954c2c6d317a533e5b50aecd04109bb224645c5b20bc96bc0b4", "8336e828b243e52c25fd06ebd4f27a9cc6afcde32f7c90b1afae10b0543b5b12", "838e38e9ed098911297363d6e41eaf6314826121aa741fc73b2f2a86d9ae34c5", "83f87300752255219390d30ede63756756ab418bc0b469f96e4b4c1b9d400e0d",
    "84268d5a75eefb78d5a6a0e8eed6d5eb5b108868cae1f9a881669be75a12152a", "84333608971ad3b647049a43bdc2a6054d0bcd06ad1d00f2c37adaedbb28f9b8", "845a14d2f107770174961e14ab76a7dd54de7fa22d20e3859606ccdfee1ca894", "84a3eebbc2a68bf7b01ce5e445cb2b0594be994d50762bcf37ba434b138a124e",
    "85b88af23e42f7bf99891ac3c93f4c8c1e717bf2e5b8a962d82f490282ac6298", "85bd93f30dda1ac03d6e5cb28021e840c6f551d415f4ffaa8a459249d13a502e", "85ef230c48d5864dd3a0ec90c3c850dcb254207a777c8038f2f9506b336a1d6a", "86b270583028fbeb8179f7b09f4babb4996f34e9a6cbd00c2642d0088c6361ab",
    "8726fc3f80119850aff7e22365600014e048c247ed0b39604fbcf22db41c2781", "8781a8f08431325878ef9d54950f0fc7e6c9d974f47eb201dcbdf87e28c129b1", "87b8b1a8c51521040c088ab09641653e4e7823ab057d37644531d238a6dc5b31", "87c489366e27d7d63ebefb5840788bd2fa219ac8212bd0cc93ea88ccbced46a7",
    "88b6820c86c0865469c44ee94adfd5c709cafd46e798c734690ba3db4cc9c146", "897098b0a8ce496479f7c511266b8a007bb0da2c972309ec6c438568c308c755", "8a3fc607df008d087171c235d73c1dc788c075b6c997c47fc86da3246ee05e91", "8a5b6dce13941ecfe605067857441f082ef58f40d442636c61cf135824d21e5e",
    "8aa91843ebcc5c114e9d379cfe6cf3f9ab191f8c0696fdd659a1710b2d278755", "8aca6911e502d81b4c9a2f56013a2e9f1505a1e46997a849f81b7c7e3ccc1933", "8aeaa5699ca37dd1d7fb7c7e475d172a3b6a31cbc5357c4161a731001da60d30", "8b0157a82a97a56377f00e1699d0691272bfacd2fa9f75c92c2bf714b4d841e6",
    "8bfba7efd00ec23caaa964bbe7675ac26b79b12d0fa07ab2b087b04211547334", "8c0689c78a6280be079f860eff5ee155e99cd675bea23e408ecd60a62b54989c", "8c13f8c694ad8a4b4ddd9cde6bda6b285e412acdd25b95eb1afcfc14e2e30e0a",
    "8c5d83ae6a77b7b32449c283826f5f66728469c8bc53001ed62f21371ed754a6", "8c8601ef6069a888d360adccb23d2192bec157414e5b3d17a0cccb7380d6eee4", "8c92d3011aae114b101a66976b6041822ef3a7d394e2cc96a2a19f3e77c44fea", "8cbc94ea3a9883e86a1d00322a97995d675544868ef9594f93e30da31b426fbf",
    "8cc004b1a07b1b2ea8c3a2465917fac1c3c17c70216e5ed80b8ec472eddce414", "8cc24508ab1b99df48b889f83d272af96c98fc01179b65ff3d54f00980c0df14", "8cfddb60b76367da8be8e875b3d11e405892171c6fff94e2895451ae62b2b859", "8d2c03f1d2cc891c90de5edd2ad4fdd4d07d60bbe98440658439c9f837c9c61f",
    "8d34db2a4108da5f0cff5a82a01eee0f7ca15c930d58c056385c966c4c1376a5", "8e01283f207713f8dc9c47bc978d3e33aaa4961d4a83b7389ed21b60a47d9d5a", "8e8410a20f46209288e48726cc57538bd6a8e0a45991b4ccae95ebf0f7fc739a", "8ec5fd80ce08f35646ea5c46c08ea4b84df5c2c7e0ad807bdf3466c52db22fe2",
    "8f0258e63d17619c2ea214b883ac143e140261bbdd47ef08df3796cd7ac6454f", "8f3c66836008abf4d0eaf8772ed2ce72c19041cde74259553e3aaf4346233535", "8f4128f86fd480de9c2872dd689cf9ab5148e009196db857aa939e49eadd9ae3", "8f5ff0e406e79df3674901134a18e980201ba93deb38384bdd47bf8c62f97a1e",
    "8fbbe0c4c396e6cc9e327a48653b441fadfc2d574401a431f8c1e11991d35671", "9068c5661bc0e6ddb228825c3bbb1f269aa7e92b3e6d6f148536083a9b99b7db", "91729e7f18f065770d0a02814068a3a533cb351c8d6768ff62de58361cabd6bc", "917f17f122096653ec1d4ffa21f01fa3df434b564000d53fde7a0bf829a23862",
    "9209ebe2cbcddb087dc1f4bd538221f0af864cbf7f2785611d3fc50f692fca2d", "921774a703e3f34f85484eda4e512ba58b457de22f1f66309961e91d93608fc1", "9238043ff4564c67630d3fbf22f1fac7f06070ec1fdb72f280b655e381aed9bf", "92d8843c99f073fa76b50e81320fd2b70231f905bdb720c13981bf86c4983431",
    "934dcbcc08bd3702ffe49fea5be1b72ce6ca0efda0683305792e5a0fce8f74e8", "93641e59a4067466a3cd90606ec8655138d61bc6ab758e88f3a780b204fa478d", "93e6c09db4a54c9169cac6afab7e36cdc327d62626aa3c25379b447b10562965", "94292bf57be7bc275f884ff8703e246a782ee64b1c95ff5d03bbd7b070a533b8",
    "9441e516435330323b229abc371130d6b1a9f97f91f2e96c633e4221a9553f10", "9486b38a7458aafc8a48fdb9bc6e87c7dc91ce42bc797358b51006b74027c67e", "94d38dd140e40e0cf33a1cfc2b0dd81fe9bf4d89af488feb1eae71eadaaf88d0", "94d77b8ab2b98520848aad7c435d4cdd5fc005d14c1442f1ac23e09f0b6b03db",
    "94e4e4460a720854bc99547c6f5cc4b2fcaedfc4a7484e3d29971b90ad92711a", "94eb5d23c3906608a3be0632310665350c7cc410944eb03a76e3fb9c80f355f1", "94f55e415433413c9bb84de4878195636ed45536ba97a607f8fa26a8e401db9f", "9518f5e040844268d7074f50371bb9684c30a278f927175c49c2b918f590fb09",
    "9524c997326d8f6e88958f9d88f7323b9b27c74aaa0dc8e7b0e4717434ed777e", "95bb1721a87faf00219d614b18cf16f0f0c232531ff902f715a00e8fb8a22152", "95f3755b12237fb44cda4d7f5111e34017daf7e9750c34af36a71ec19bfc411f", "9607f3efdac0af2fbf51950eeb29eed5f3593ad05f5853b48ba797d87bca4446",
    "96585d15b492e18f58009940e1f4c9ad4344426fdf876575ec6a2caf9e2ed633", "96e73aba49305c6bc9049adfd5c686192648bffb97ded5611c2810345a00b333", "96ed50624b0c6fa9f1205344f71d5fc0cf7e63c0a2b17fb1033643a162a2903c", "9787454dd546c7b33ede02e2a8df34ccc0494b06ba3b601d0867b51207fd8fb7",
    "97c27c93c096d1047ada72ad193d2f82b429807c3568cceed2efd8eaa92ede6e", "98144936978375e4e036092cdc25d76fc271e174dff0fc66a64cae448490a915", "9923d68f2a13e3a6c56c957c19c0307ee302a2d8e54fd9e35c03c7f470eb4070", "996e3d828d138cab696959b022333a682c38bdfff95397aedb3fbf34f97a2426",
    "99ad748d17874f961ed145129bef4389f155a50026fe67737153e4192e17ef9d", "99b235aa6f5b04feff24a5d033ad5c69bcf2e661a1b407c3f0f196681d0c9dc2", "99b75e06a0b28a6250ac4c36980d224f8b6d62f70f3101504d21bd145d0b8bbc", "99b89993ec92c9559c202081e0813fb065f528f69e645f064e06e9ef02f73505",
    "9a6ed1d9760b373ce48434ad294941130dd15bd8520b52715b6b59a66a8b5502", "9bab10cddf5e198f783d256f8f13f29240e4bd3521919abf4e811242ca780ecc", "9bdfc5906b45ce0afba0a9ede1ceafe2dd860a7769a3a42a38338f0585f32a33", "9be09cf367fa474db23b46aea150e76397c661ab13f9b169196644f0b90dd272",
    "9c45ab46163155901abcad0aa86ba0d029135f36d1f1f3f18be5606a4ea37458", "9cb3e6d4feb1fb018c5740e2d200c9e3e794cde1a7888aa3d211d5a617d93ed2", "9d13a046f5e8d06d9fe98fd3306c35758d7eb85d05ea89513c4d800b407bc7a3", "9d979569b120f1ce6affd2087da59de7db9cfebeb7281a37ac1e03d07d1132ac",
    "9e09590e52642c373b67fb7929fc37f16a80ba4f78fab54f4a7ff57b738c3adf", "9eeef5fd051ab4b7ab2081e3f3609cc920eb527fedd8aae1a06d19d52cef17fe", "9f3ac5445dbb1fec4e42df995886a9a61c17b48414749c2b75533f9c6fba2198", "9fe5bff0e45b9c3a0e0209a604471e2b8c472e5ee10b53ba030bb4273aa7ee04",
    "a06c58e2df8cdb107f961f57d3e11b709c8334dbbc1c7065221520d88442c806", "a0d1f6e6d70dcebd57fac3acedfef2a9ca4ac8b7077dd53abbd920be51e56362", "a21fc0dc96c4925ba01c209a196c46374c39ed83ef309782e5cf80765d51460a", "a2a79eaa5eb256d753d6a2ceffd312925b3cccbe50806c91e7f3afd511b02921",
    "a2c8789531b0633d42ebfb87ca0f66ecb94f057dd860219c0245662118808f39", "a382ac863d8ec62b69c37e2dca17457355818b784a371019df97eae098c7790d", "a3bcbc5954cef01aa07d1387cfab3f6c3e4627dfa2836d5b3c9f3b6929805a73", "a3e28cb0cd33f5e79d4d356055353456a4245e39daf19234d347b06c1ef55223",
    "a3f4fda2c9fe4d1d17518d46bca16f947c5ac70efd68c3c5da1ad35aa69b5730", "a4d24fae2fbf404af8a21f7b6e4c4bde9387c55f9a47371ded7db169774a8102", "a4e80a560e01a4a8abad7ab3ac65ab0e27d0f441614dedc177a121dcd09c6390", "a56b7931cf35d0f9e4ce0ea33cd5163969801b881cd546131682cd26f106d54c",
    "a604fc0f52a908ffaa8b3b6ad8459e5d794edf848ea2cf042b855a4ec46db8aa", "a6079a75ab171c4b1445bfd926bbf29fbe7f60a379b6e0f235edb59c94916fea", "a64c5413e04c56df11c2d816b830ed55b3af44134ab39b70fe126964dd4e36dc",
    "a6c2f2dc601fa37deb75dd1014fd9a582b2d6994076a4dd87723609b5ab3b24c", "a6ddb6aad4d57f9df19358a2d8d7138cdf312e6099a8ffff0e21a15192d65863", "a6ebf6374595924d324c6ad199431476c8b5652184c01a27ae8f597c34e837bd", "a72fd868debb02a9af4f3e4bf90932b2ab9a0c34b9667d75d41ae54381eeec0c",
    "a7abff521762b192d531c1331dcffccf506522823d10bac56cae55d7602e7548", "a7e1a75d8021602eab4eb54409a03fa35e58f7755bdac8a545ca9377f36b40ee", "a814d64d3b2c202519ccf74229858991b8f62e35799652edcb0152a6bbba5648", "a8ff533bf075b90af4447d0fc96412427e7a64646cb9ee74942b175092547db9",
    "a911c1be3c1ef799c44adbabab26263c73db488a76a790b062783dfd2b2757a8", "a96c792226d48cc3632df0d75c94fb31a1bd4bc4ce8d887fcc337aee9d5d390e", "a9ce50a1c174d9096cdb542df5089d9b56f4180226174c83e5456596d79cbba5", "ab4457d9d605ae5d5d085575f738c73a07f190a07e44ca524427593e19c66b5c",
    "ab731a81bbb8cf1d93427d5415ffa410e3f3008702b1be38445b4646cb75e9dd", "abdcb27efd8d893787cbb24cbb20fddbc113f180c613c96c2ae20651a882e9a6", "ac0821c2e32b96a01a2ba1023f75f16173291f09c2214d2adaceb410895d8966", "ac5be4c08a5b8c8f8f55945bcba2e8335deb7678d2722446f171733b2927be60",
    "acab7ed70a73e81c97b1596e95c70c382beb748fc4e5fb6f7f6ae70ac1eca0cf", "acb28417e73a966f8768cd22fbaf78128ce9d5e7224a3880148fcf9968c9b618", "acbd3e7766d15dc2c124a929b8c756bdc77ccf29c418aa2a08ab7c5908fe75ce", "ad710d65a859c5e299829456c68d27f172bf3bcff9a59c27aef77fd4a5558e43",
    "ad7b28fa185e6ba5a20469d48e73ca36ce8327021e55d1cbb4d1086d1d773308", "ae293efe847fde27ffaf4c9c95c4233ca604a91480cef7acffa65fb46223c193", "ae8d30ec82ebe2a88b0eb577f1703f5d7d9ab7f0256554573b044bd5a5069aa0", "aebaaa7b4b90038f0c475da7d9b31c520a97e0f1b73eb962fef989a5bb4a58c8",
    "af0c49f6219bf60dadd353d563f0bec7cfb43d3cbd13da60437fee8345d278a1", "af645c3ce7e74a1e0a1e4f34cf44335e02693bdf3060ea703149f82a24a47421", "afcb7800a0b03c11dbcf727f026907ac8b7bebd2c15556a97c3566fdfd0b5707", "afd9487137fcfe01f6c2e07c9f88e72924f390d0a103f48214df1130573f20b4",
    "afdf824fb97082e5c4454c51452e9878af12b7549aa517d6509329636d195aae", "aff911f39b53f789a6e0c10650107f54e0a9876c87de3e7e8ce3fdc9c9183124", "b02e49c13a770e079f34385e7a0b0c76ed268ab58bc209c8798870976bf7a39a", "b0af5576ff0586a8a35732e19ea2309a21f7d187d65ba5ee235ce5045721181a",
    "b0cd1e729f6cf798ee1ec187e3a596996ce491a6568291248707879e03af0628", "b1b2c3bea69359b1a59e0a073de8d84fb02edd42bfa3c0abf4a7b12db0858c1a", "b2109dfd1ad72bfd6deb9eea0b1806815cd41ab3a021f1c9ff31c05042f899a2", "b220392d4570a7122a49eebc9a000152ddf936c7121d8d268d885f79a7205ee4",
    "b22fb3da575680194f2ac08de42a2dee145707982d11b1e91d6cac11ad4b860f", "b2c9a3a2e22f2b7419e7caff8fd77e0cdad56e3fe9c62d0ff8a23d7ad6c7ad07", "b4371e5e2e65944bee08a7bba8826dd9692d1842090d14f151027b22398cd22f", "b49182bd6611ce1f59f59e72a8125d82a42cc991aece819e1498af12a3b07813",
    "b4b21b700360ae9584200645b11a1ea35edf5882f8c90027daa254b520e4a35b", "b4b55aa164d58cddf30af2893b112ca70ee0033b8e09c2cbe947642d3262f2c9", "b4f53ac486c792a5b48010013442c9e4729b37f271b3172192455c01c94978ea", "b5e0435b7a13b81ec82ea29fcc829c16dc9df4b6493e4c4cb4d5c29d84b7823f",
    "b633d40e3f442e089b70f7a2e64b2e637175d8324cbb6d472e592cd387607d86", "b6a73790b9a1cb728670dbee5c9b54262aa822d2a7077bb4072089f0e3ca8ab2", "b7291cfaae5bec3672032a3baeb979362356d0114c76eec8c0e288c15004614a",
    "b77e2c95667c753af9a3336c2b3264c62493c01127d86e0d90e6312cff1ad3fa", "b7d655d0a119ef525547e116eef95529418fa07c9ccf9442d5f9cefbf24a8135", "b809727ff28bbe04a0d87060f339d6d435ced18c6f43ddd33a53cd202426cdb0", "b8c6b76fb0cedb9c391ac8b73bea9ca46fd55e7e46cff686a85aa49554e1b3ee",
    "b92f19abd99e9b6b1dfdef503e14994ac19d94a7f22615645695b71ff57adc3f", "b9a7c396f8dbedd255221219aa651fe94e54ca2ca2742bd73860d49d7e784a5c", "b9be5cdbdede47df2ab3c967ec8136d4342d2c45a428078e9f7a57b9a09786fd", "b9c92e7d14b4fc3f43ed520a07448cf4dd97dfaa83ec2f4ae46b7ae099973242",
    "b9ff8f844e40adb06e9979740111a9d73ce39be527aa158b991e6f1f557ec503", "ba14adca2057f361fb76fe0490b799164f5a8fcd0673f1faa472519116c87943", "ba331d5ce99e60a58ebee8ff6200639ab5c18b687af955485722de98186e9c9e", "ba8f29b8b3198f984f5a6d0c9776b052149475ce81c0d3a74593b8edec3d986c",
    "baf900d9e5fc89bdf9582179581a37fd501881784199fb6b16f67a26e3aee0fe", "bb36eae4306b511b02759b376930272bb525b03676d026be900a4297ac2e2148", "bbc31d01d85ee4e63ec1998eb80150a05517b3e52601f36cee85848bb3d87037", "bc00497a9860d4df67b06c451a4a394263925a70c99be829608a898027773ba5",
    "bc74b417625da153bf26a9ffa0947d23a66b246fa1c231eeaa1be2ea450af078", "bcf66d0d7687a249d9fe6aab1b5b08df824114e2f486ac97257a3258af6e2068", "bd0a2081034e617c4027d4c90c8b66cb1ae969990a2ba6a261653e5e209ceb10", "be3f39686a8cd5bb7e4eece0ee6433579f1b9d667a40020876cd8db44d9efc86",
    "be684a568ba57f2af81416b2c93bb5801210c793ba2441689fd19ef832b7b00f", "be70929199746361417ca0818bbe10f8a9f20350a5c46d480c58b815004f0e29", "be7be6e299854ac647638c8b36b6cfff844501083fa4b1f40a9d17541838c96e", "be89920b45607d28a7ad24d89d70fd6107568648c4aaa8a2191839fee1422db3",
    "beb648dbe917e195d04222de74e59094066d529aec124fc66f8f4256e8076bf4", "bee1b77797d827406db8ffc6982e978c7f00540a332e47d4bb0c6b61eb328d35", "bf0bc482c7f7b9a3de4e84116f33feec6798d751177d0aea28dd7b0bfb869c63", "bf1e3f3e1f5c2db27c4a50d5177a7b3bd57115d5ca911932b85b19cc2e6849d7",
    "bf53c0ac1568ed4e6334a50359ad480db002707cba6d3464bce2394369c0c2cd", "bf75f752477646af682bc37da55d751dc5ba13a2dbe92494b66555542afa9700", "bfa155b55ce0e28290494d6ef5d266108ac9ca8255c56e94fb8bd24decb66f01", "c0a4e373cfe2b261e51cb015da3dea2a9ce1f17abbf0af4903e120ab995a1c5c",
    "c140dc6d15167c6ed84281cfe915ba656c2654df43f00f2538f7e12608876182", "c161ab0a96dd84bda040f9b704a2fab6294e747f25e843a796002dbb8504532c", "c19adaed0e18c49b9e05c9e1bd9bef68fd61232a327a92ff29429d774c3dddc1", "c2724bccc1ea25fd10e9c7293506af66d480552fc2eace69fd5cb05dec1d0f3e",
    "c2786170df818b243122cd8382c4a6e75e95b8304b8a95a1e7a42615f36bafbe", "c285c886d5e6a34c95974d0c15479734b63955eb0c4efcd95a56f0064a0d4da0", "c423a2402106cf4fc07a80542d5b35f17c655283e7c56f321bd4a63b6b1e052f", "c429f400263cb8da0d1142c8970d53db9af805693ff8eb84938e3b69a7702452",
    "c4360cd9a5ef5409f2c712132d73ede1b501c0d8a308d119174d700c3d0fe459", "c444a1df6f285038a6048922a88bbe9a5bfdbd6716f226a743487592bf4257f7", "c582e1ad3f6b2dcd1a9f9632739b49e4984b41b50ba18d906484fdfafdaa7f87", "c58cb6e1fe4f2e36ad5397a961cefabb03259bbcf6b4da628290900150a394f5",
    "c6503ee3a27d9aa32d841762d1d8b8e4da96aed3b937098f8e70feeeec723b17", "c6ade79dda7cb99c338c1cf9e3bce3d89ec0f3c82416005c592400b59748cffb", "c6ed472729db43f05d57ecf31e90407b5b87919a7640496cab1b351965c254b6", "c783d374733e7bf747e8adb7c66193839312e4011ca524aee984ae017c37d1ae",
    "c78d42868f04c090bac22a3115b71fa17913494e8e677cc8e5e6c0ddbcfb49ec", "c82e1c7d277ec621dc1bca367963ca337aab1068f19b0355d80ba062fd62bbe8", "c838455a74ddab48b053dfc4bab3ea1c7ed866bc07680f88a933c70bd02c3070", "c9661c2d694026826c32ea6b5482c285b44db2343ee42d81aaf93347bf1e3998",
    "c9b49d046e55d507111fc89ac72a5fd10d3c6a4e905c1abb179b0b3fee1e8aa6", "c9d3ad0df68545b97bea9ddeaf1fa4bc53ff80391b58096a071ff511cce723d2", "c9f8d7f722a8c56c1b6f557d9fc70d9852a715ab5597fa2a6943daf5a2e7fd47",
    "ca1e217d6b4e18738b5faa0921193e62c53a14d1e099f60a8d634b3c44b5d5df", "cb10a55c21ac21ffd6d1659fe8d5ef1dc779243d64162f4ff415cc1ca33b74a5", "cb26e416a022cf099a8eaacf711420ea344f8365857b464e4addc29297c932ec", "cbb72f125982953c4c42986e8cddbc84ab9960190435227a98423b13170269ac",
    "cbf1b42b5b7b87e8009dc1b986f5961a3a7adf1ef3858965fd297ad08ddbaace", "cc2de339f68b15ce118d02d2655391acad7e0f8b906e7baf515c5e77b206cb1b", "cc3e7994a301581ce8d31c29926543c6512e022f6a226a1e9d50f1904e6ea0d8", "cc523da756864f68c384455d339f98de667b5f10a64065b1efcf384ef7243db7",
    "cc58b733951f1d2b3dee2b3a059949dbf5edd17a29ca47cd29e9cc38450f6178", "ccdb8dfdf8261e53ad9db838a9d42283fde5578756f43e7520b5d6004e3eb962", "cd0bf006527c5a46c4353647edc64725e6d243769d9cf4950f6b2a442b600f79", "cd876e66aec58cf8e3465141d951c738d9290d36982a2274e7ce3fff3a25afe0",
    "cd97157f459d4c7d864a78ca6facf6cc634a4c73a795eeadd2a1557e74314b4d", "cdbb6ae8ff2f41c076d1b7f560490204addfdab5f4213e548e78250548855816", "cdbb74593c6bfe0fa8676eca7fecb5afd061540e3e2b4aee28992e423afe3b67", "cdcb5cb34d51450f6393f8329d6beaa62a767fc0a675221e641b17909418c9b8",
    "cdd1f29fbd6e329d2adc2c6dd4f1e65e730be183ddf18729c92be23d5e0759e1", "ce47e02a81c5c768056c1c742a0eabd174c673fcb0a5ebae29f9d802d853a45a", "ce9684383f2b067a128a4553da24d6c441639e37d794b46409466af039dc8f88", "ceaf049c0b1101f146b6ff05b65cca48ec3a9d33137e8c8a8f771d8c488f1b17",
    "cec337a0668ee0e693c521a0f15012c72d7a5a1ad1a28b26ef08cfda7fc3498a", "cf12a451208685a0a2048bc606ef939c91bfcdfdcf4bf41617cf39319fa67df7", "cfab3211711604ab356cba0097fb363da19e014a4ae2d62ad28d2f68a5e6eb6e", "d1063ab52688529437cc77fb1ee667318af1f4f2b116685ec1b3a0f11f773e56",
    "d188570299662464d0bd57a4dea038a56c58ae47d033e13bcba900d5eb59f469", "d203bf8241d2f62915f21f3e87b740780228e9ae38fcae4f57914c79d443d03c", "d2a93474cacb1f87e2f827ae8b16ecd59d077c531699dfe538e0ee786fa7be58", "d363d6a18498a8d8148a3941077efac1bfea73910584aabcd3af6c3fc00db001",
    "d39c0e98755295f3e7b80fe56588d8003e79150a331856b8362c0bfba04a3ebc", "d3e4d58a3104377a8824564d6bcce7681609d0b4b9194643b9ff4619ee8575a1", "d3eb40a9b4b0225a0e12e51970b91f28a1621ac4220a28bc5d4c1f46d601e4cd", "d40903aaea668312da62883e7e3dde2fb41d0e6dd83b0aef285aae878cc5f1cc",
    "d436335183378425a4d686264b4306cb42f97588f12a1c7833a1b5c4cc9bf2af", "d46b5304b7e1601e2f873bb4dffbea765d45aba75244b8c80bf390584a9ec44b", "d46f0adb80c58324e72f1bb2a93aeb6c9d72fe3e7fa54ec02760c473ee19811c", "d52a717911a8ec5ffa2388d350a81d9d7b70c73dc279b69d2f69695885a62f74",
    "d5eacf1b870fc9039339df271318b9d2626170c5e1985548587a02b51817e6e3", "d61d13134a5ecad0a8ab6e3eb9d3fcf4840f332e93a8a5e88762ce2e590cce3e", "d65b85bcaf232f20a652afa29dd7c315934b8442100ff267732524934d468cfc", "d6a0479e2be0a40bf0349ad0398c437c78d6c19ecaca1fb94ea62b74887679fb",
    "d6dfef38590f7527dfa2a13445b3345011737ccb6972713694ce2a0c0f4515f6", "d8172e76ddc708dd6d62ef3eb8420c791e5b169866d3b2bc6eb545b8970c01a9", "d88b25a0bf7c80fb52ec38b5517388a26f71bfaf153d03df3f7d5adf3e0b40f4", "d8a488cd7439ebbf60e278206a75b435e409978c81a2a72028d042a26edf6bcd",
    "d8a809acffa5fdcec3ff03eaa4eeea2c69f9a6314d328aa0e0dd6e4aa39b950d", "d9e884466eef89052b3b6345a2c4863580c0af53878290ea6b8991b664663c6d", "da0dc91d37a97a67e64d7cdd3fead88b554360e461d83f5eafd7b55f2dcd35fc", "da77024c2ccf953652c98384625847c9e506dabf4a9d820e9001ef7786e2de09",
    "daa9f0c994ae056e7a8ae218beb02bd4d339bc71b429510ca83878fdb6e4dc74", "daae5f3e0fa65e7c66dd3487e9d2645a9433c3d175515a505c53834b1ce825e0", "daf27c335fe1cb0408ae2ec33d80370adf5273fc417a977b4a25199db0226e14", "dbc3612409027391c50b740387e8ffcbbf3284c47e1c688752d301450f4aa02c",
    "dcbc9ff438b1d712ec08e3015f6c6ecfb6c5716490f24ac7bb4042a13fcd6fd1", "dd083983783c2da99968ed6e026cd8f42fc12b3678699cffd6bc6c0e0b40a7b6", "de1182c287c583eddb7a28bf9476efdc79194ae54abdb04f015882e0546b33ba",
    "de329df554f0d5cee3d9e6e7735f501028b53bb88f45dabfcc033e44ad919f21", "de77e047e00c1551dcc6c0eea0155554af5b89f28d7c3281ac59a27dea4308e6", "df73caf870acd62e7ef60af06cec3b0dd335bfcf47fb6f66fd6448d1a5140310", "df880f85e589e3e3c537565ed21ac028cac64b5de6a44dec4aadb6ed3b06a7df",
    "df923d204f95f5258da6504ccbd713a44a14d1c4320dcfef44d28fbb5ae44324", "dfcb18b7e80949c3d27cd398d9cb09098c806ce08467d2c070e03a2710ed6220", "e00bc39be8067662a59ae9c94b7b9992c23bc8877c48aedde12486935514115e", "e01e4639f71e4e2ed4b3de5f3f5e6406f59c94e5b5159c47fb33fe0108d8724d",
    "e02e08b2e31bc63c08c3ae12b0a15d18eb6e138058da4b9a053b4c6b3532344a", "e02fe6a4a6d056fc142d13b0ef58462e1df743e82c3f4d995d7e97d1e93d4665", "e04b47d51a5dd38e372251304853f5b16bbd80e79f386b1ec1630eebe49ab3cb", "e066d0448d0d97e5242857eb7dc3ca87c8f4c4d50119723eb844c944cb756d6b",
    "e0cda95b4d6cdfff1fca4e76f21e463aca980031e9352ac92aadbc1dd1f2bd89", "e1206daef48bbe525d94c536efd17babf34ff3da590e62a6b38f457e79cc8a88", "e19bc2c9674f0798d5cd070ccaf3fa2e883efa5f0ce058a9abaa04eb3b92b92a", "e1aa11d75fcb03dca2feae55f2d5225b0c448aac897d8433d7391e4e6367f264",
    "e21f47442ce9ba71e99e0ecb5a63741134b51b79a68ce3765a2c70f24cf7f263", "e2268aace7a731a55ccc71633e87a49500d5803cce5f8e4d38c09b62a556ca85", "e2272acb6dd0861e82ad8fabd39881d1caee9548a84cf3aea632642b43518637", "e269901230f00c456adcca25e51fa679ee0c3895091b7656b8b83622b97a59e5",
    "e37228f825312e608e2b5f65f0c4d4ae103e30c1bc01488298427c256374e007", "e38ca264cfb6f01dffdb76b5f333d0a4dee96bcb66ffe0a5cdd1ef8ee45ab333", "e3b91f1af1d3e07fb02424131bc787d9aa67ac36cf33a3637b2da3b165eff6af", "e3f6579a69b5927aa6d6402216bece29b39071e324fb4c921d71064c9511aa8d",
    "e42b588eec3f3f4432a1fe677d897f72cd31c8b7590b5c048f2eff1ac8e99231", "e44b040575bdb06135d6325213a8865256639b796e945c8424266973471e133d", "e488e9f398d8b5437406065768865f035db714a6033267b579c031074ee157cd", "e4d0763ed1f173d6850c562b7528677a4b86754faf1d8e230bd9e54292e93949",
    "e6c50e0e2f0d93024f294aca7cebec7b0c7408653857d04ee486a2714dcdbdcc", "e6d7c67fd6c7378c057f3b2da9afebe19fcec2b1fbae4aa6a35da727e77ab167", "e729db95c7d063f2110888d6cd69ebe90e639d66bb8ad417655a7a28122976fd", "e73e90cb7c1dea0574ed44a457e0d82fb8d98e96933d4a2c4c2e39fa34b5df84",
    "e89dcc49abf764bccd9f328f3c757ff476c2276aca65140c148560dda4aa489a", "e8fdb1324584c1fdaf61e8e560db48e4b75b4970f957fa700c4510b4b91de18e", "ea0fd1212d754aeb18ec2b619fee422737227fae4cde19f992ebb0c4430f0681", "ea46e36d7fa688911f9eae2ff45e0f0dbb9c9c3842afee91501598a38c0a9a84",
    "ea6b0c7e9b629b15d94f4e46d5f7c863f6f9c6b2e60023839288426d6383b348", "ea988d7873c6bdd587c74d64ccbb6b8e5be3b932d91c64a18f8d7f15dce197b1", "ead82a311188c9263cb31b0bbbc6ce1471bb889e3e1ecb55c5824f5fdd0ecb41", "eb64f4442119762b83d8643e368e766eb2308c36d3b8ad6b3014f753256714fa",
    "eb7ab06dc0e266f59530fa59e4d24896ff9dae93aee1203bfa2cc4cf315f1671", "ec5ac3f258c4c40e115c66f3c013318cb463c3e1cd0751e5e79ed8c4260864b3", "ed6024bd6d62d110b9059af760451d47a3b5eb4ad5d77ebf2ee22c0c21f244ce", "ed81051d31f742348c3c328b512ed5a9111a6bfb93a36fca3471ae530b9351c7",
    "ee1039dd5e041f0ff02b5aa8e2ed849d870531d95f7a0de98e808f58f5a9b5d6", "ef0cb5d945005a94ce89f003b194f30cbcf146dd4ebd2c9d9097c7321478e455", "ef1ed9ef7ce4f2697d860ee5e5d9705dc82ce22aad6bbdd95f61d489169ba61d", "ef27494374a22447d25742bc7eecaf8b958c8f376203777474b8e7cff8affc52",
    "ef5b2eaea0c34fd565148fde11a2c8f40f55298a2070810cdf91cf05407b2b7f", "efab8a2c8f8bb8d5e13b8e9fd7352b6d074c3e9f5ade4262bdb278aeefc0cb18", "efaf407ad01dcb43e95f43bec19cb644390de555e02d473e8911a2f26b93866d", "efdce2218bd7ad324ab213819e35ef2f2e731e78261b8e17430fc70ef2bb07c4",
    "f0177a7e1c7f6f431b053ebd3a28fa78bd658508f3c33b39f4a8fe59a624fb1d", "f01eef1975a116a67cc8a473766bcf3d2bb914958a0f9c309642f19ea4e431b0", "f06dcb5d047d244f46e4a50ebfec96f8d98f597235d2de195900c256128cdc69", "f0797558463153501ecef44a5b2d83ebbb95f00b5cad884e50887d96230c5d62",
    "f0dd4e82a3ab7433597793a2b3e1c70db12e119afaebf91eb701210db10ad9bb", "f0ea646fb2669ee4aa3ce5cf51247296d52dd746255a2fdf5fbc2dbfe6b092bf", "f109106ef14a8ddceb883c3148cc55655538f5fa37be45b7cf28acd8f34b768a", "f119d9d06420b6be14b291714ccf2f57ecbafcfa76a37ebe30f89399d73f9c6a",
    "f1de99566f1bb789d306eba1f3af06cb5e9d3d3ef58f26aa0a27484eea5499e6", "f20bf81fba22507d549a681f15f4a0e8b63e4b410269c67d6adcc62831c6ede2", "f2142caf48da43409a8677f793bc93227dd2ccd9543ca682ae964a2b936219ce", "f288e7f85a851cc33a25fb69dd2317028c1eb744dc9d96746216225256b3b8c1",
    "f348c1401752b25dfe0c455d8d4e9022031686aebeb00c87894ca78b56d85826", "f3aba31e121972146060f599e3b327791fa8ea027f1e4aee970aa7223b2e4e6e", "f4015c7826457f3ea584407e28f0e770aeb15cc1a0dd3151aac7c929da775ecc", "f4181303401cce8b3aa2e815764f6cd664eace0f5a18e889156a6e5407c79b6e",
    "f494110abc66de6fc8d1aec34932cfc77107565819d9ce2f154e136c78a809b3", "f4a17ff196eec916a87883b3541fee79d54db90a7f03982f5e9ada7e928500ab", "f4e8fdb721ae170ae06e0fd4d8f17573e699b569f7ffdf5eed4c3545b8806e3b", "f556398329e222bb1bf495f6fa54b52e2b1dfd9da845f15274ce3892748eed69",
    "f59e33450487e62a3e2a5701ee6f890a04f5a80c5c7d7fd567424a368e010e79", "f5b88d79f246115ad8ad702c6e4d4010fac8e2cc74a3920efe9781c65acebecc", "f636f5f59a72f0ca1ed9f9a8153758a09545e5aecb5987a16062ccd5427f2bc1", "f6d1366e6499992428f9b685edc1dcb14e2863abab5706173b8b8472a970328e",
    "f6e9e357d3fab157c6c7710957d2e357f3f805e3f730cea9de3a158f79ba7c47", "f72072d7a3c85249aeb6ca31bca53ccb316bcb26da7defa4206fe72166bee96d", "f732e77ce5f541ee90f3077142bec8fa2069cf9417f6430350ebfa7e386aa148", "f780d094b9a9587c6d5c5578d667c34e49bd809fe11762e6e6e2da3033f1ae15",
    "f7dedb63ef1f0e5f60957804040f1f6e7aab24d0ea37ab0449277123773c9705", "f8085208c32a3ffb9ef100376105db2926ad3894291bf1b6c001fd9c1d372b18", "f8122c2ddd0c529ffd39e11c617eb162f15cc1228d643bb43ea109c4153fde80", "f86e0b665ca39d3a6aa38d90e5e45eb6a6ba6d699254054b5b8a9e8dd27f43b0",
    "f895948f53aa818ea28215a0670ae3186bcb84e5c750673cf50351feb42972c9", "f89f00db59a25aa4c651419460cc41a8e5d33e0353329bc69acac34d6d058919", "f8eea83ab64c0463a23688abc2a27b4ad4a0eeb3591785083676698fc926520b", "f9af7a9e93db5d7007085b89405b70dab7a39f50fa2bd62b5f8f9ac297ff0b2d",
    "fa1b6ab01b607670aec9c233d3f5dee984ee7e5f3a7bd60b9fb88abccdb6321b", "fa1daa4aeaf2121d6d8dfa09bd8bae9901cf72ca0d95b84000c40d513385e36d", "fa3958f7e13415ba59b8843c7b5921dbeebac77a213fce296050f6e8201e6025", "fa59639973a54dfa835259496e6e95752fcda0c5ef55b321d5accd9675c4f907",
    "fae42c17f880da11a8676588a9ea1611ae6de894733949f751c56e3466bee146", "fb2db9ab0ae86706c0766977dff15dbb4ebbb145ee0ff15599d2865f6b42bf1b", "fb7c9577cb0da81b82259bfed66d12e7c9728b280ff2cb22259a4e85777f337c", "fbc69906605f5c00dd8e4df925e857ce25827698f632ef3d195c5244657a6a1d",
    "fbc6acf14723e452be23c1eb070ec31b7b128a8666380a12d89f26ef3516eba9", "fbe8431840ef64818979394e02baa7fce57298d4313e88df0f3f137092cbe691", "fbec41b1ace7d0ca71c81ed57972783ac1e17b4a09d008d741d7683063e1742b", "fc088ad682b328c2c11769ee4ddfa928a3e5892cead8b49c64649e2c83a27edc",
    "fc342b73849650d6c62c3180e8df98aaeb3dd518e329b47b1b982ab55ef93641", "fced890e8497ad9096d90c81f102e15556c9bbdce31db59cf8ad9f51e4b8bdfd", "fd4f8e26ba6116c3cd3400cec82b9511ef1b33398fb6fc6aec08fd0b8e072a38", "fd8de3982388057b26ce5d1b1ff18a56e38e848e619b5fab4187f7c236b4e3e8",
    "fd9a519ac5f8e5664e77b481aeae8a39782faddc750bb1b75fb85a19e413e5ab", "fdf4edaa02fee95011ba9915e9fd7e2f90e844638aa232c0ba932930b0e12e0c", "fe25f84d8313c25a9e45c672687b6ac26639c4d56fab92f0acdb3bbd9e83158a", "fe43bbd00671625c692440a3b89b2a7b6629bc5e67a567dec544659fe871b51c",
    "fe76f4ba3056b23b9141316208a8086b52b5133614672570b8125c8d28d06e62", "feb593f6b102626e742178a45757d02458522e7ec272c6d2ceb53780ac154692", "fec8cebf7053179771046e36ecacd3b382896c6e74f9ebefc5528cb7e5611dbe", "ff0f960bafb9e3bc5eb57da852c7673de2b85fe4659f97e777e722d56ef1a71a",
    "ff142298013b892a587261e81dfd115f92b5149ca9b2d6621af0c82a726ad9e4", "ff45de358c872dda4b77a9bb82141cbc78b53dcf0c3c3f265476befc450527b4", "ff662fc25b5d33a17821c9fe96fd9db3de9e177f53ef9ad0d12623468e612600", "ff8496718ab90b332f4c6caff1a0af360f42386cef0dcfae8a871022a2c411f3",
})

# Technical containers added during the closing review.  Each hash includes
# relative path, lexical owner, symbol, container kind and complete payload;
# the adjacent reason records why the value is not input-language authority.
VALUE_BOUND_CLOSING_TECHNICAL_CONTAINERS: Mapping[str, str] = {
    "8b4e7d43512ccd9c70fc650e65693553bb0724cb6a28558e81faeef602d56a06":
        "closed retirement actions that neutralise a unit by masking",
    "f649f61d39230e50c7436e94b02efed460423fe0a1ff2163ebf167ba201159a8":
        "closed retirement actions that neutralise a repository entrypoint",
    "cc3d12d0ea243ef8e9da4931d56f09b94a4ade59f99138cd061978cc40a2c607":
        "closed systemd unit states that count as still in flight",
    "717600791b5dbe036580683e37250568bbb77fb7cfd7d392a8586d5efd677a23":
        "closed set of systemd directives a gated unit must declare",
    "0e98a95a3c159358e36866a9e7219eee5a2701a2ceae92727ae5cadbec6166a9":
        "closed boundary API registry mirrored inside the signed preflight",
    "1723360c898511beb5fe3d17010b9470470fb684d6807600f723b4c385279aba":
        "closed boundary module registry: import names owned by each boundary",
    "18d0f95550d215cb86ae67daacb35e638c9444b05cdc5b35991da45dbe7be64a":
        "closed boundary source-owner map: repository paths to owning boundary",
    "2b1befd1bf1ebfbc2d732e86a323f02f538d56f8fe0671da65ae6e29d1e0f4bf":
        "closed paired-device identity projections declared by the manifest",
    "48a7f1b6265f07d93c17c77313ee726ac77de45f523eafc77e4388e53500aec6":
        "closed boundary API registry: module names and their exported symbols",
    "4bf749f996ba74ec59487843dc8aefeef4e000c3cd001375e121934374459933":
        "exact source path and symbol of the sole authenticated launch scope",
    "6976d22f44f73fb145454339b9f7e469aab90082238dc8b269cca51f5e9caac0":
        "kernel process status field names read from /proc/self/status",
    "8311b9536da68fe413acd9b5f1f805d5e9bd0234d2c128d015bee9da8c9aade3":
        "closed paired-device match modes declared by the manifest",
    "877b14a01cf40c7f6cc54299a7c6e6b4301fc0b2f0898e3494e63bbad5259079":
        "closed boundary source-owner map mirrored inside the signed preflight",
    "952f0c82ea63fbe78fa6b1b99a9acf7a1df52d92cc35b50e0673a546c887b676":
        "closed set of systemd unit relation names added by the manager",
    "a67585e0d3fcd9216f6bf120de4ef638d5b0768eecf2792aa1612de4ba6c39f1":
        "closed contract-store failure codes for an absent code payload",
    "c4b3e524038d791de8b5e724cdf0f03d42babb3d1c21184f4773e7b6e622e9a2":
        "closed coordinator store owners, addressed as file and function",
    "dd841db9c1e52aea3a221382c420196f53f973d7d131d3e7433af473626f6b92":
        "authenticated launch scope mirrored inside the signed preflight",
    "e247f4933aff9080b927dffd83ddd1a00e8141c55c29533f84ba0f92f507a336":
        "closed boundary module registry mirrored inside the signed preflight",
    "feab9cf3c4d89cf4b407b1c463450a4e6f76ebc3e8a62a72484c2e14c6f83f98":
        "closed coordinator store owners mirrored inside the signed preflight",
    "5b8160bd736d40166b6ae4443dd2ebb99fafdef5aa0dfd2c7b01703a0f4f9c29":
        "closed systemd origin-property protocol requested from the manager",
    "d36dd62ba735b02f3dd085ca60f631efa9ec31c3a5915ea6ba2d7dac10ec6891":
        "canonical tool IDs projected to durable artifact-class IDs",
    "9b53d263d83d7e6791ec1d1d85b4a4e403a40e4fe0ee03bd39e520b0782d3450":
        "provider email category-hint tuples projected to canonical classes",
    "054a3450261ae5e44d92704ed001d9d5133d07af6759932d2bdbc68be7da1215":
        "provider email category-hint tuples projected to canonical classes",
    "ab5591b1cfaa38fa0c97ae957ce923fd31ce62380af5c3eae257306eba707d69":
        "closed authoring capability identities at the Birth boundary",
    "a82348c2a927794924a4fd89347d9e59abb60b00a807312db085047dfc7c6fa2":
        "preflight clone of closed Birth capability identities",
    "eed858715eda06f1ca3fdae97dbec2b4d8d642a322300058e99d0b6a66edfcb5":
        "SQLite table-to-required-column schema",
    "2f08bbce279bd966ce35e80756f61961bec76be426acf6dfe9322f9435c9d544":
        "closed trusted executable-directory identities",
    "263cbb5b7ae68af4ac8bc341a37d8ec4070e660fb3d9f9720f1c22a0d0a0788f":
        "closed privilege-wrapper option grammar",
    "1cd9797538d3d33bbd19c41060bd3ba78ceb4d7ebc604ee84200bf48482ac0e3":
        "closed privilege-wrapper boolean option grammar",
    "165e893bfa7cc9a72524b8f35543ec00f0c8d7763c7403c5cc8e236e4690484d":
        "canonical destructive binary identities",
    "eebe7d4a9b48b6f411e5a7add4675d2c9fb8795b793a96e5a62438db92ab3b88":
        "canonical destructive binary denylist",
    "5b2c0e5b1dc45bc88fc9e82dc4babfce0bc9d4c696982a52aa0ad4ad52261cf8":
        "canonical binary identity to governed i18n message-key projection",
    "557dd38c8ac87b351c3a755ad0a488ce1d4c25fb0669a9a524f717aceae25e7e":
        "closed legacy describe header field identities",
    "7b1aca890ac2bd4e07be4f8a3e4d5b61a80ef51c62dd436f3c7141298ea0e117":
        "closed raw-disk destructive executable identities",
    "acf8e508a6116322e0c1ae23ea5b20b4005b7cac114f728c34ada2f03fb4000c":
        "closed administrator approval action identifiers",
    "e6106dba6546ea003fe1317cd523e0d5c25c0d12d6377e44f8d6f8e59d252a7d":
        "closed guest approval action identifiers",
}
VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS = (
    VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS
    | frozenset(VALUE_BOUND_CLOSING_TECHNICAL_CONTAINERS)
)
VALUE_BOUND_TECHNICAL_LITERAL_FINGERPRINTS = frozenset({
    "0067a0b6a8dd5928d295caec3ce0707289464020f41fd7a8e68105ee204d63e1", "01973f92808ce9a56330595453fec3e0eeddee70207d3d39a61f9c3e4e702cb2", "01cf5103c9b12d11d10322370836a74240a83f54e28979f4fa77b679c95623a4", "03040823cfa5260291374bc91cf97fd700abd3478dd3ec3857f81e1ccd38e45c",
    "0348ef773f0a6d615f619b1363c3542de46fb03180b95a615994013f6ca86ded", "03f4c25d0aeb819b241afb7f694fda67019b0c61d7fdb41b4c3c10ffa48fc2fd", "0806d4e0492d36f69f24adc32a00d9c6be0f933024d6c56c50abf83af08e3787", "0824329be5d1e3c09ff8a7a0b386f8d86efca6dfb54221d7f7ba147b7ab4bfd5",
    "0e7885ef8b0043ecff9957542e16731c77e484882f3249483a8a7d10af483827", "0e881d0700fca41803b306ca8e0f5d3614b017f36d2d7de8a97bb4793955801d", "0eeee703b5ab8595a0d7659886bbe8221d25e050b7ef9fc38b96f1b67cefc599", "0fc9ee27345ee827f21bb922fafe3d13eb2aeeee8c918be9d3a2e0269e54f273",
    "111a24dce0f191928b850aa8175c6ac337ae4c7f82bea21cdf0c78e1a96a0628", "118d2d759b7e87df4ba1cca12d485987259a21f943ad560b933838cdfb2d08a8", "11f620c57aef3a5f6218f24cc8bef46db24d65f9fb33c8a1d588e86f497443dc", "139296faeb15376c30bc4c53bb2ae28cd58f18ab37894d3f4ef5b752b5e3851a",
    "14db818d0f16e612343da50c6787220e6867d57c1f2f96352e0958dff59224d0", "14ea99e7d8e075f746e6d13f43f3f53bfb166121f5e666b17f5a128468400d07", "15bb8534343d73f231680877ec436e70102d76638f26bdc2e92488bd230710c0", "1660300bf6b8e06b522cc0e26ff861de9b057e2eceb604af7c9825f3eea81376",
    "17794dc9a044f21362755acfa7bc6586834ffc05329ec286a737e26421324f4a", "1918e0a83ab4d537748fbe17295873ecf6e401bb90cbc8e6a22ad795bda810e5", "1a9337ff331a7ee225a56b1c8e857490ef7919cbd83a286033cb8d5a22f4836a", "1e7666a84b3942d809f0fc09254196d62c16f623ef6d1abde4bab4229bb94f73",
    "1ff4fe9e6532a48785ed6245c536dd45399611e29a5c836dc18c2b50d9724a88", "2158d00a123ae7cfea758994aa444ecd9cc6afd9c362517cc7e65a570fb3ed72", "22880233d5d3354e4578f5e922e6a80a80cc6344514fab6c8f498cc83e5b69b2", "231fb664cf89d42e4cb806993fc1c7e2977f74af494d23dc37579a96fbd1c30b",
    "23699fbf2e03b09d8176b937e23763e5b8b016355c427845594b28a7b6b24ce7", "23bd056c064f36b8721d1a745c4e280d1985e9ad7cdc454d759d61333ee56303", "24d6626f5659310c16ae086d5b734df043dafb47aa4792764594a0765a30222a", "2860e0942e3094b6f932cd46e5c811f19593561ac24cf274b055117bd562b646",
    "28767b496f2f86ca4a65db464c55e42a159184bab389f347fb91b71538c9918d", "29f9c108339af11c1324a20196a2a2c9f58d4e54c351da674daabae89660a4fa", "2a97a2c77833640e4f4cdbf89bcde60ff30d461d00a085cb1737346a9011ab7f", "2be237769ae0454d6dd0e7b17c28d8e6ef0a7272f4c35a3938a2d9b8416e52a8",
    "2f6241d2e6b09d6a67e840be26b3d5f3f525aadc428bd9f83f241bb458fdf4d7", "305fec7af5dd0c8dabfa530db93f124d6511ea01ed38119d147fe29c08d8e669", "306825c03c9dcea92ba565c59c29832644be25f11cda0c8f20d93aca532a6e3f", "30789246844d6a31e5b19b3291f436e63302190f0c81fb0353838910e46835e4",
    "30821a9b451773f04fc6ffbbd84b1f5924e7e388e693429d2f2368b4a093d725", "3174823a8ebf254d09921f17b4b0648aa0e7cc8b9d8d1c958305805598db4c6f", "3582e96ba371f095ae48888dcf0928f0444fc11739ffdf33711eb1d0fe7890a8", "3a85fa8229732eeae6aa2749c8560471a1d28c75445d51dd821091e061b78539",
    "3af4b8989d3feadf3ebd4741c12463c47c90ac66288f8e7ea4e207c29ac29d5f", "3c15c73b184a6069a5e098f71ae1f20d32cab3bed99777bdf54a57231c6c196a", "3c4273f95b92a178e9080abf8996b56df9aad864647e49bf6483bb588cf7f716", "3d0a8ec80f73c56f1a933f53f38fbdb931b2920f00904ebb1940c409f78a866c",
    "3e7d868515548730f21e7c775f4cb4acf688a2a685f59fcf251f363216ab442d", "3e97c300efde4e1292651d9512bb21d7bb7aa9f064ed5a21a39a3c7ed2f2e034", "453ed4a23f14a28e4b91b0ad0cabb48d7f2cccabd3e965e61e357c2988baa70d", "46c1a1f8bcca18fd5647a812225c683c8292cd9e3d6197d995af0ea2dd083b4f",
    "4992354026f4ad47461f5510c8a91cf86a9acb8d97437d22d0b14af097b687dd", "4b5ba9dff20fd256430af98a6d454fd05e07cb2ed244269aadc9e7c328116cc2", "4fcc97103197766ea7928192e5336d40093bdd1fa74d39d192242a693afe7d69", "4fd0354643598bca959c2c230cb49c27afecb97bf290949cc42274e2713f3973",
    "505b8f43489e56b6318cf6cc4e26eb5dc43bb91cf6f1a2cb095cd1c174d2088e", "515b0aa97b4b9ef63fc1acc7d52b08f0e8ea4ec5a4a4825ed37012b4e74ab789", "51bb445b5314a7eec096363bc0a2fab34f6375ea5111f017c7e2c6e17845e6b4", "533f8d83592aa571470ad3bcec9064f77c818add3a20e905b5eeee4b9e3e722a",
    "547a1b9e353b09e40111cfd0785b87046c025dea3243fa962cf9e6f2b024d249", "549c606ac4614199306a81debad5a81e878ae50592eec61e77f3fc73a3f795de", "54af8df83f0ec3eae190849def214fb119cb6c196b1477cc27f44a8a26216497", "59ea74c7368ace28698c1a115f52d84f9ba56d5457961d587cec30b373169fa5",
    "5e7397e65e06b6bab4108b9e7357a4b320780166a9d690f57da942a457421a09", "601a9004bca39cb6e6b4cdfc8f8311ab7bbcad99a84d8c7167566db1dcc7789a", "61fb302b5a6c965a14d120e26624dc23140d7ceebc552748d789602311ab854f", "624481103cb40d0804a4cb27e89ab4e4e493673f4f43a142728abcdc2523a67e",
    "64297458a49c57bc33a11734af113d6205d891ff7f0f88dbf5dbab88a40424c6", "64fe4793aa01d6593d4b297c5cddfd3893b4ea441fcf26324118ff61fdc6a1c8", "66d0b839c1fb70fc4f65ffcf818a713f58a31ab1d883af26fa8b43a2c3d2cff0", "6752e75425418776f278685e5d63fb98c512c0f577dfc4c2cafc69bfd29c2dda",
    "6760f75456aa1fd46c0c3a171faa5a010a035b5931e2f655a7c325380f4013fc", "67ba0c6145b89265e412f7622e7e75ac7135b4fc36e5b734ab2abc5479b4a5f6", "69c66cfc435f4ad4b8d587e35f31e9250b847fc0a678fa3b4e02b96d7b108319", "69db7142bd0d4b874053af2c907564d4f2c5149b8aad81d3d0a86b339470dd26",
    "69f2689d49a853a298739020202202d4f7c7787d14bff08c61db7fae1a0d6d92", "6afb910f781d9a813c5b31cbacba67174ae6bbd184467bd8b9f9de6672e75458", "6b718d76e21e127b897d74c5926af49e855c1192bbb80494cdec85fb6d05fe30", "6b9eeecfc7e436621721a4d7daf03cb47b08878f068c4bc0b15236e404e650b6",
    "6bb4577e636eae37084abb396be2705056667d7dfb6ef3f18ce99cc7f6c66ddb", "6f0ead88df16fa6ab84cd1f48c92f7bbfd6b9e993f26829e61795c6006ec0ea9", "7074e73b40dbf89739ed5417601d0dadb0151a33b1daae23d9d0392fc8016b60", "71d8acf32400e387cd52a6012b829bb36646cd51e6ed003c5d80f046065fddf5",
    "72d912df783dbbe5d87a582fa7f7afda536f6f4723925d63a945efa6dcede3e3", "7318007a2991dfb7159efd58f1124636e6fe02778206639df25bfc52e55dea25", "7380e63a34b7182424441af336c6aeda8978baee9c32063864321613fb125fcc", "7443d99fae4a960b00a0eebeac8fd0208dd252483df977a37c1a674a26637c1d",
    "74797c12cdd4265de49aff21fd215e805881e26ea1bf175f2bf45e3836e617ff", "76a46dbc18f6ffad26669112ad86f956530097337c989dcc9355d517a3935a19", "77d88ca4cad63ac92a47b8a4b4d83731db603c2cb1bd10c2bec72419db039efc", "7b75de196a5a4357ea7409ff7e5c22094561f2fd8a979b8f8fbac5031842d141",
    "7bea13df2c1d3eb5589bfb9c7c0b1fd1bff8f755f59c0f97f137d8ace2284cb2", "81f83a90c37c5b2932e336ecfbfaa45f5f5587807fb6028235c4d2926a5e3f17", "82d79f71e76a7f391cfbbd0dc45d300e4bff68bc4ea08c06593ae08a940c646b", "87f3a77a57d05ea6c7182cbb4473c48f7c2d0f606e5a4589f7978f01ac548759",
    "87fb48ba72a6e5cbe2801432999f393f0099464de584652fb10a48c27860a9a7", "8afbfc01944ad26402cc89dd24419352748e44a207656df050734ecd9cf1457f", "8b4fee52796c45d972ac0a3e9ee924968496a44b1228244621e93df7c656cb1c", "8ba91e80a4047f6077fd30301b868e165134ed89a29baf60bc0f976ce897bcbf",
    "9073167d72da032912c9487f95062de2c165f3c23e07057d850cb1fe57341d1d", "923cf9f7025fb924718c4eb667008be8c785fc9e7187ff3c39b4c5c24772e51e", "9362816c0ea087cc058bebed38036ae9be02a0244d4b78ae8ddb7479c781f5fa", "93879bf5f13abfdafdc29b6e4a33f31376ba96050d7e5f57c275e20c17b9dde7",
    "94a111b9f23ca885c5d86a976e99a216707760711eeca1354ea2b73d47622730", "952d41a11c08f201c7d2dcb45c637a4692fc8504a19644e3e5a7d8c85a5cd582", "989660a2903cfdb968064a878034234083f420608fd9fe3727c2589610786166", "9cae98ae3fcf0f1f15f27aadadec650ebf8be9430c3b5bea9e26a3d16f7f3cc5",
    "a127edf0a99e389b3f5ffe4fbb469938e39aa399689c71a6832a2b9bb1583808", "a35c393dfac8e39638060b824656f93f8e2eebe702c6663acaef1efe3ff1dd43", "a67b4eecf7d718bd129d90e2cb57383535ea571bf0104a732d41bde7277609e4", "a7b3564d6527e3fb4a63df4d32204eb3808494b690e98eb174f4ed2f782b3203",
    "a85e1888991e89f67d8bf6f60f73c47b09e27dba662684a8a0bd0b68318bde1d", "a96835a4c30f2a3c3e4579446458b6896cac2a317798c8e12b0223c5bdecc8b2", "abb273837500c18905bbc9147adf81b9f1e872b1d2f06b8704e33cd1439f23d3", "afe1d69933ce918a3d112be19a95f291afdb22bc25564b5021c4d4cbcd04848e",
    "b3220834de37c23c137c50021cac16249b31869487ac1fcba7beee6a2caf21d9", "b515a19d3e7cfcd489b3295fff6cacc6fd5a3d8d16ab596291545f054a9ee567", "b548d1e855a0533164861f0b0b0617969e011e5ae5bff9f3bac2f346986f80ff", "b5bb45e0a04a6546c02a2c0ba7610e5699affb90cc10cec2c2ed66194aae531d",
    "b5bcb1dc722255fa3f84faeebd35e963f38fea96c875ef90bc4ed5a405390b21", "b81220285309dd2ee876b2cfcada74db11901cbe9e9a098c3ed77aed77412ad3", "b8fb59b6e0548746cfa04843c0dc2d6156b70f63618f73b568241b7955c28f45", "b9b17781792d091721483f369542b6febd92c584107098c8a8ce0d7af0be24ce",
    "bc2d4621b5a825cbce6afb5d170ad94946892dbe86e68c0171237ad7e5522d0e", "bd2143650af501991b7453ef75a2c0544a0e7c15e0c9aece9aa1486ad41258b8", "bd7c12556047cbd8b3b8ccf702dc78028194f2e679d4383dbb4ee87ec842fc14", "bf877251356dffb108b4f464201f244db165c2ab7c449afc649ee2b3da4636fd",
    "bf9f93a27a54dfdd31deaf21c2f87086a9190a44b8bd353d17e85f44a147bfff", "c3b833eec24e5d120dd7594bb68621258abb3185ece61243880881bda8971802", "c56fe6b6bbb1f7339fb3b1e8c734664254d4d2e57be3e25814da92b7928d2292", "cac9eb600769a9beaef5c95ecc3b98772f9a7792cb94b857d731a0382fb6dc65",
    "cbb4e941c8e6861ffd3630051be729598d409e26e751b93e0595b512fa7e4ad5", "cca7be0274b61bab5bd11f47da3666cb9d636892b529679fd5f1e4fd13b58d4f", "cdaa5c5c7b21dfd818cac5526fed7eaf3304ae55a5c858a26db28d1fd7fce2d3", "d1e42e099c9673a887fcd47a77aea4c8b76e1ca233dd9cb3dc55c202234bdbac",
    "d20fd54ca12f5013ca875df945df300345cfc8c247cec6851e8814d1eb7044fc", "d2c477f6c11b78913122b4d30cebab0a75d16abde8539c0082a1d302f0cac464", "d495f606aa10d1d44604ef59f0742cb00bc5011979961fde70d074de2c06b8a8", "d52a3e2aa37539aa2ba3ab4bc7c41cefb7856dd16a38b4f6042576830f7776ac",
    "d73f3e701e79889092156c68cd1d8d849025e462a07e02a60b28e456ef3b1b5e", "d77676dca1c818f85d0412cee4a3a57bdd8e2797eb7574431b415c3a37afabbe", "d785c4142c06e191a3ddde9e345b226251d20b12b91c3af6ece5fb10cba8eabd", "d7fbb525ac244d43e7f11fb35349fd701c13eb53a7184da6654c1f41067786ae",
    "d8a6403d23aa43bc97c42b1ff26a2b4b26be4c76a30bf09f9914548a27c3c5fa", "d927897567bcf87ba3f2a03ed13f6ca3b4b875a51905426e8aa488251ebec857", "dbfdc99f44316bf79814dc5c3e7a65f6540fc4d0b4c56b446a20615ac77bf9bb", "ddaa5858605883b12277ddb3939207a1b0a1dccc9cadcde2a45c6e4c57b6718f",
    "deaaed92d627049bc9e2a3f1d6085f7637897d361456cffe52b1e24dd57663dd", "e1c1f3b521585ab8c63eca8d0b22089bfea3fc2c7f31eef369c39f2485f836da", "e582a42286ae32e693bee0a4c9db07632f302676608c4713a044866452297337", "e7203cda527441b1b07a9831f9790c07de9f0f8c51d1edc88c4cd61739f73f3d",
    "e830cd2e23549a6e41ecfc26cc73876dde026c09105c89531c1a9b8997292030", "ed2e0ea164bbeff78a5f26097f3c9d3e427c7436d2906fb8238e50ec5d72ac63", "ef6df044e789b19772c9e228b61301c99d57141ea426d562a6cba15c3582a7dc", "ef9dd0ab347dc788823815b6d92c8cbd5e81caa193a51fc508d67fa595f775e9",
    "f0de4b67a44db32f5d0568df3fe268121b1e3329efc4946cb9cc6214e397f6e6", "f1fc50d3fe9aed2bc1fb3e0a216d0bed52fb1a40838a0e1034587f7eff6cf3cd", "f4c3710029551f8eaca849f36b3837a471a314c2e9ab4a7e3352de83dcafda74", "f4e6dc13e41d02441c165a1e0cefe7875302fccf3b3a44a9174edf1dfd51b2e9",
    "f661b82520d407bd8eb8f09e6e55fdb597076cc97c27e6a6c03acdc282d33bf8", "f7830d51d49586cdc39d70c9d0e0605173e3f55689aad49b9157581e831a59f0", "f7b80f9fa1196f1eae81f3b50e2c4bc18da708f56a8532390abf3724184042b0", "f872674c69eb10290885fcbcb0dfeb343c7831f49111e9060d30f8d79a7b002a",
    "fae712a80f2db46cb238e952d300ee33a909ba6cbfa83c6ba30e22e4dcd68d74", "fd67e519a8a520fd97158574905c3fe4f42feebe45349475af4ba7ebf8b5e29b", "fdaf9d7393fddd3ae0d47659f54a9b8e10dfd26cf4ff4dd62d1a0a631be922b6", "fddad88d3f75ced0ac0f0e9341c0b434ee9741b56ec27653e08c5e4a716521e8",
})

# Inline lookup/membership/iteration and regex sites have no assignment symbol.
# Their exact path+function+operation AST+payload fingerprints provide the same
# fail-closed boundary without a function-wide waiver.
VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS = frozenset({
    # Identita tecniche chiuse del protocollo Birth.  Questi legami puntuali
    # sostituiscono le precedenti autorita estese all'intero file.
    "84de6e3ac477a53b54b0b502d50cdc531473b5d0aacfdc6f73f86104cf0796f4",
    "fd0529d429e040e925e65ab65c20e37919b2cbc9877c73fe02318f40534363e9",
    "fb14cd683cc96ed1a913b78a797d0f3068ac358bb450093e6d8670415c93deda",
    "edde6cf178e6aa796bbeeda4b4ea8929c66208ebf99c12020755f80307dd51db",
    "453efd09db8426dbd95fa74f1fc1f0929baa48c31c4a9d5876a8f4381c10f217",
    "6ee21b731eaa40c03a8cd767727a78db05472eaccf2120d672c7d60b246df11d",
    "4de757f30f0a864d59a5a431534a7bb53e8015dc4863e1f3e4a14f7c548302f9",
    "2049c8eed62121a18ef86431ea2684fa2e5dd7a2060eab05a927dc3b39bcdb89",
    "000281d9f1ed5322e36025633d43d4b671ebf05f58c640375c2d4057c32d71a3", "0019ba740ec27aa0285236d60713ffd04f86da3cb325db22c66a1f5ddbe554ed", "003e11a8c7357de5e8f099f965267a6be02366f8833a38ef91ca735bed5afa09", "0044544216d54598b6fa200c09e627ef72efa7dad345695c5de5409b6f371908",
    "0050dd4b8b471bab168fbbe9ff73dfec425b18994e3fabccf9a01ef9db6e268b", "005b0ac10a82c3bad13806eba8526536ed37d683c78e69e414396203999a4b34", "006447abf5a2338726bdf9f4c56d6b8382b628fe149284979a4e9db023171506", "00a28eec9404d5867626aba7bab1ad9a83c808cd8c21c713113092bf5ddcf900",
    "00de42c8d69a4766190e259c2b60646106db26a5c171197b70679b81f5827e4f", "00eaf6ce8d09b4127b579ed400ad7e766760f6462a068b4c1d249b0221145bcb", "00faba5296586359571b95e3ec45d376458c4d2b276117935c82275dd336e5df", "0116795a8cef104bdd1080b4e96d5f4607fc83b8c6146cb6f2fa1ae8fca38431",
    "012ffbc527eae42d1cbc1341b3a3d6f69b115c4ca45911c068e5a06fba25dfa7", "013ae6ca524aee1af72fad7f3a121b3594fc229b925cd6c27fda1aab422f0729", "015c56f868e14841bdfa0db8af2ba1c6a9bd93aa8965643806d2454b9993370c", "018dc1f16b55310cf3dba9ac16f5ebe7a47d1deda5423aeda4d8876cb146cde1",
    "0197c97d266563084aa00a83e7a5f9cc23d9ab1225b468d9abb2393c36258241", "01a08e8b29fd92d0ec21c223c3b62fb59edc16ab13a268ab55ac556e3931e23a", "0240bfb668f41b57e6a0ccc6b4747c05faf349aaa361bb88a111af2a39a98fea", "025799c038070a8fd018ead92c60b47a3c7796a06e1207d1ccfadbd418d0e318",
    "02661af4c208616792cf0cc0a6283e9eaaa2bb854d1c722a5031d43e911bc5ac", "02c995e55a39fce5e6029d1c51c638dfd61f4b91067230ba7ea1755f9d5a6140", "02f5749470e19bd42d895a61b45efef0d4ed72dd0805088094b210fa53c10858", "035eb17387a5fb40cf5ebb27b7bf20f5374216ab71b6751728a21f2b93e71859",
    "039dec28b5da824c2b167dc715954702d0c8ca61324910d24765c5e8b679f1a4", "03bbe72d5503123e29ac4645c43f7ffe70142287da0d1b5bc773c8e12053d318", "03e2a68d57f91c694d94498babc51ffef1be92f9696da55c2edb26f8e50a60d7", "045f53a0a1c862dd3f86f2ef2a07d72bf8cdcb119517b29267ca6fcbccaefc2c",
    "0494814263c1cc2f11ce7aedbd3293dea6b6526b41e94cc7dd65b050ed499114", "04a02ff155c3d8a8b2c8af0d23fc609807df6c24aa8781ac8df0764adb60510e", "04bcc8ef0a0abbd2126e5b213c1759afe5c06267fcd4a119d6adc7a571fa5ed9", "04dcfa72a217d69ccf3a33c1063898bd8421575fb8e6898e06c1fc6d6f8eb4eb",
    "0521ecd41bf0fe9322b81ef317da1edb18746a0f56db6e3820a7e5fc8eea192d", "053aedbaceefde2c26b827c3bf01a019c677ec846c2e5407ae58c84ec58e40de", "053d9c94606a57d32f7481ea988891dd597e0fb4ee87fdfd1cea32fb75f3808c", "055358d2636cb888491455b1a4dc6a2106f18b98284f4c478a43ccff5aa7c7a1",
    "05589c6db7eaef66ead20412124eb7c96301ff3a10cf51af8605a71fc88a04cf", "05797ded3ba1e3d99625d99eacaacd28bf42afd8f3e071419ca8c7aae7f2860d", "058fb5635300cacc84018587c90f5d75170d135e964bdc82096042b1634940a7", "059d28f97a8742cfe57fdf3a67d50ae2110fa20408de7509933d5b56141f8483",
    "05b7d5122701c95a7ad95581fe378cea908b63abe0b06fa215abf9c3f2e8be27", "05ca44fa8085c305b6e8b15061fdadd09b01d72dfa7bfa425d1a6be52c6b8cfb", "06083d047a2e9accde714329dfa36eab3333d4b1a375466695de85a4a499f911", "0615504eaab2f5bb23175ee50a8e9e0c99bbde626576bf6f147eb03d6bc0a004",
    "063c282d4a915feda2a073a6d5f95d239be3cc10db4440fe888481eef52cbfcd", "06691ebd72c0374ccf42511c32b7033c1e7a3040077e2fc52fe91107a34a5fe7", "066e7fda097f2da9e806377fb38b727fa9c756c7b17119390a1dac2f84b610cf", "0691e6659b385939c6d41a1d27658d108bffd72eff07d1091e1c00604d4f8651",
    "06a94b80d03d5274ea9fa1210bda66db4849e1e977377d341b3eb0c1670f6edd", "06ad1ddad7e5baf921ab882e93ba198439ed7b296877dcebe9a5083b83f69d9c", "06b8b4aef319fd5dc80d5ac448a4d1152fe8578c9a559bda306f2ee5e12eea27", "06e55e44f273f5eb74933b646b471adc22daa7940301f9d7c41ae61161f9667a",
    "06f3738c227f96dba4f7979c0106ee4a04e7dcdc3e6f0825fb004d7dc875561f", "078d2bdb64e7ef5d177b2a6cd980b83ad9731cea74822d21b52c3dbaec5fe160", "07ba493a9bcece340f8a56733f6a8737843b6965620909db12ec5c77010d7af5", "07be01e802fc2187dec5e11458dc636fb6dad727f9e14b300e3bd0a3ad121a37",
    "07cb4590ae27534946dbe6d9ff008e3b91b5e70ff1431f8ea19ffb1f1ade1d68", "07fbf20e2ad8f6125dfffa5446ce34845404420074206550f587c99cdda8cc32", "08188b62fa8a7cac7a7ae0ab6f96d4f5025b0c516b5c1649578071e67ff37456", "082ac71cee3535db11ee8cb61cdd39d2c81246a52b76ac211d659530e0b408d4",
    "08784cc3eabe14e0d6d2a1cd9295e132bb309354c23a80d91fa06d86ec8295ed", "08896dc9d8f8dfe7cfc431d0d795a08104411f59faae07d0073a10b4c08e253b", "089008e10ad7d807abde0bdf41cbd2105d65401b7e7f5cedf41da5f94eaead8c", "08b0ca9ea4ae0b75346092da3e61ffea87d35e46ac0b452a6918f51c388bcb48",
    "08cd7719b27ef8528a07d3bdf5f777c02a07b1c83bcee37f5cd7bf7e0d100fe3", "08db68fedb714fb1619c5124bb8d07f22743a6a7a54914a6b04c4cd6e523a070", "09449142f0fd68783bb6946c8c9df49222ad7a20fb10f4c4e95fd28ed30638f1", "0947ba6e541823c2d89163b1b5cdf72de093c5c13c4e0302482998df6a3781c8",
    "0953fc57055a8b9b53f02eedcb34b8eac5b8d291d93be1f817d5997f2991113e", "098947d469285e8159b703f4c580421c367a0e8b825822e9ff3f52a46ce8968f", "09b7d134c03ecd4498639cb735abd59c79e81a1282b04f546177e7f1f15ee43a", "09c9fd45dbd2e9f8aa6eb44474ab83c12f64ebdfc8bbdc85c9c4a8f7476bb009",
    "09f755eabc17cdc0f4f74c3746b31b0cb300ee38d56d7a5ce4d73b668eb545e7", "0a3af765dbab5ba3e050ffd071da0d97ca7e57c195633a5ac0d0db068a690941", "0a4e0040fb658f9094fb7c188bd99145cc8af12032023a4236b34ef7457b3866", "0a5542eedb50921e16fb9a5d65b9111f2f8ff801fd66c99ec16b1d122a8688c1",
    "0a7405037532b1dc3b9b742010fb1101b1b687eb2813f3721a1e7da1a175d78f", "0a782f4537a0dcb9effddeed23349df7364cc3e9f8e353aa658d4dfb464631da", "0af9205a02557d83e4ddd2760695c3cbd23dc4994d720072177db83f12953ed2", "0b1563ade2c0489ff597ed6cd70484f3a1a1489927333db103d89320810bcab1",
    "0b3600090e989d94fd3505670c2d4e70b1088272341f5799d0e888dc89fa3827", "0b3ca52975cab687114e8a1a8418e0385d0443c144b1001881c6c8d52baac531", "0b3f51b6c63180f57ce16cbfae0f07e0ffa1affd265688822d20ca51edc8b4c0", "0b4afed1734d3fe263a18b8d70ac28d5b745c5414fd4d673cc7787e765a6f29a",
    "0b4d331673bf6844492c8c5f45d9c1b89d4b68559b202667e4ba851ff306d36e", "0b56e12157abfcbbd5d6e6ad823362b31b9c664448a8c5e4201784ed7f6b0e8a", "0b6741d17aa07dda04e830518e9060911fd911f6bed44d25fa91013de3b06ee9", "0b71835bba1457f1a8d2bb224f8374dcf79c2c9ad52e1efb0d5111d5bb713b2f",
    "0b899d2679dbce39bf007924552a2dfd36491ef65afeddd85ddd87f7f7f72ae2", "0bdb16f9da11fc50344377cd0f4c481e9018a09fcee85dc71a1fcf8d56234fa4", "0c3d9c76d894c78e15fc9c37264c539d1635fba734266485b8efd91eee689978", "0c650357071898f473d607f3f965d7f476f96be1220797699d9833ccceca61c3",
    "0ca86c4620ca583ceeed5108b6c88a0b2ca232495efaf0296e5f2166fb94f08a", "0ce1b7aa3470b07d8edbc6f16a93580e87afec93dc409f9ec64228ef0e73bde4", "0d39ac019b8bce5406f3daa2f5059f61a0cf3ce9416645d2b3c42df0990e15c5", "0d60353bc44be2ef7dd4c2b76943e69b7ae72663926c651565104ec5f54600f1",
    "0d94dd9454588601454d811722fd75165fb411f8a0f7572eaf0cad4f73d8e05b", "0dcdd607e836fc4b222172c60344ac38133cef1b0533971133124997d2317d3d", "0ddfcbd3996b42ef8470cddb34e3570d3b4e331eceae0b5c0da7fdad0e29da9d", "0e0f9826e2c5d1ecf5856e26878722ece67ed414994f11c9f9b2cb8b78171a6a",
    "0e20a5bfee7437e1ec614ca20b953c2e5174f3f148504755a623e46fd05f0ec8", "0e531c7125f215c436ce4a5a2b69e177c3dd5ffb6484cdb9b30378210b606a1f", "0ed6438f3a5027bf0f75b82d2e2faea1ab4fc959e0211ac21368a544d78f8c55", "0f39ee94af7a7a8aad58d5a80dfdc18cc308c2a799f87e8154ce90438482932c",
    "0f4166564680af5df32637b748247b9c7a546fda4b6d3fcb97f335e780923eed", "0f42c425a50f3acedb84bc0e2e1daae827f59c0c1f1aa9748a7a81525043ea85", "0f69998798f14efecd5745b175cfe598c86a3b43d9e21f817e9d611afa7a757d", "0f8325e440d0080d0b34fb0ce77e221cb27bf3f2fd48387a208144060d117e23",
    "0fc0140cf22ba2cf42f33c3cc630f129890164a84bbee1461dd217021a394a0d", "0fc67937946f18eaa8dd92a1da9b2e21deb27c8c17eccb87d7404160c8aaa5ca", "10028d5daa2dcb7025b7ef8c11e55a8160eeafc3dc85eaf7dc5b8ee5eae68a93",
    "10244a8761bbca3e6cb433fb1ea9a9e5a081e6fe90fdae34377b351597f205ce", "106299de21c29d493d4d982b7c920cfee947fab66a0cf3e9636daf6cd4c04427", "10af86eaf67d8ab9dcb8c8bbb165225e74823a8560b7d7b53cb509fe0eac20ac", "10b0c30be9bba709d74790505b040dac49d0e833de7d990fd4282e6c0f84c497",
    "10cbf11db856a96c01b839814690a3f31cc16fd11f9a031d1daa8b8846bb0c65", "11180cf61ddfe450f745e9bc7c1d9df0081b382b34c6d1f7ce02ce3ced801dbe", "112261b4efce61b9d1270118d944db21a576d6f6ad2895081fa6e3a0731a6ea9",
    "11ba8247913643c738c84507c8e2ca30c871a3ba2a04732a1812350c218fac06", "11e104dec157af7d9fb29003de8d41e7f24f58c04fb36670cb20cd03e2967b0b", "1231f937e1a32f370f79d38c54020d7f63c4f263bfb418423dbf231d07c9e748", "123495ff4c079337bc03af429361ce62e4e8396d7501ed9b79a4f7faec76cc14",
    "12720f99fdeefce98c2bf97cce9610e95b6489739f1b6f55e09d249c51c41a90", "128eb0d191645fa4c627be1d8a9caa308190cd7e08d16aa32162f3a89fe55f48", "12b5cb6e57be0a882c9392345d979dc6729cb50b663cdc5ce8ace1f0551c8310", "12c7c462c4df33c7c372f7c14ba2cdcbde62ac2cb6ba698d4c65a9a36c8db027",
    "12e9e6c02cfe7b353dd98fa700194d4eab248f3fab5e71d98402e57634626783", "13127509ce73da42b996e3330f8b5fc2fa9f91b29fd230088a801718837b529b", "13a1f76d19c42af371273a5e1f32beb6cf86fb5f8e356ccb019702256e414f88", "13ba5b5dd4df405cad68be2520a634401c57e1e0d727b87233f4c80a989bb383",
    "13c36e2e240cda8301ececfe1c6f00cc7eda9a79e3517f2c00c1a3e1bdd76f18", "13e4fdeec17ed30d7503b4fc7d6d8c2626ea9b890c6ab66e449c765a2c0bc1e2", "13ead2cc1aecf5d7df0db463359f7f8440d7762f750fd88a0dadc669c04afd28", "13fb91c810df6104e8c80de6c5b7da836dade42e73fe06b0591d914ad10eecec",
    "1499aa60b609d62e542b82efa2fd8590eb9c5810a2c3bb1945cd4c71a290bb04", "14a11adb850cceb60a0c290de6bc8c99c4d49242cf70c509760f8566ad9307f7", "14b760197353054ec6ae4b8b432c76f11fcb3c41459f06c59aab8eac7dbc027d", "14d0fe8955f42dd42ebdb8c74cdf54cd773067ce351b8d12a6ee62dc698dff6e",
    "14efb42ec97fa13eee561eb5e43cb872415667f213e8842bbd27872a8e8b70bf", "15049eff6cbd04cf161e9ebd3042a5f7ebeaf14b6405e0a2f6e0ae1fb552124f", "1520bb0300dd54dcbd4d8b5093e9693a915a4288f84c2638a19627e235aa6313", "154a662cb41e9e826825ebd560ff1fb2d7121468807ae213a958691f5c8aa780",
    "154da650f26df2abfd202eb0905c3b5422b58dae2cfaae5cd5c9bc20caacb231", "1586e3a287561bc375125abf5a19abbdff373ef3343e943e9e625e205cb598d2", "158c76906f2058b19735b71c3f2c60946af0499dd7b80998dc409da3b553328d", "15b71c87351a4ba4719a6e88895c28d4ff9a221d0eadcc42e7c3aefe9256bc6e",
    "15ba47e36abfeab1eae61851f2db775754da3c13c7aa393a222976190d9abdbf", "15efcd091b56eab51062f8fec74879d859264b4cd35385c4f129686a34260823", "15f43911e1b6d17b2cc619593651c013e24d9b8b661bf6f8c0b59d6da51351f4", "163ca82bc971b6ffbeeff3e090164a69977cc2b2edac3202e25d15375c533081",
    "164f1a7a6c4547b9578a9103e375a66e13f4c28dbd964b640b4c687c737373cf", "1677e9ac15d8485b00132cefc0a2c240f8ea9a3774553df24c46a9c95335a6cf", "169931644103b62f68655d88080fbf112a6085103e8f368b3aeddb6fbfcf2151", "16ba2169f53735a2b35980d8a0edbc93bb1895427eb9723892e02c3f36024d32",
    "16d16ea19c31e1cc6c3ba25d6afa49cc5a43a01be0e0c54baa6d45e15b6b0eb5", "16f7f2770cc7a11d471e9b694ffa8207539dc5fb9ef942c5fe64f438a0fad519", "17131d1ab869604e2f3ef7d05a55b47722cc53ab69fccb90ce3f4b1965859923", "173ead6218902bd859f0256f7511a5e135d5e7aead2a5d78dbea5cb1f8e39ecd",
    "175169188d7226784b4a290b63f2c4a7e4b37d191380bcb2bb3cc5916beb257c", "17672e6cca7b43140ced4f1516655c607dd7944c8e7d60637a740b24d0b7559b", "1781577de86f55c57ae2dc383e6f17857c4203d20ff6e40238683e20361edcef", "17a2b4bfd219fcdf4e4f7e921bc21c11cf132e2d7a9e2ca790f737b30bc56d18",
    "17b12249a29cea8cd22d8406be244f1fca8c657b649958e99e39d0a55aa5a6c6", "17bd51487a62ed3e5ecf4ebe65630c615c59c809856a9229de46b30016761ced", "17bf7cc97a5be3ccc9767dc2f0d214bdcd935b6981313fadd865aeed8b2000df", "17ce1af3b0cf17a7c7366a65121a71ffac547b63f4551c3dd8920d5a96a3f084",
    "17d6b6b2209cf61d5c94d220b3de26430e0dcc5c4dd4ab591a5ab389c7587bf4", "17f4dfb620fdcc8109264c9b180d2b715228d2141bc258c35b506915ca22d71e", "180109c73055b550708fc6a35220c72213ee10f2e3870682521a9bc6d4a10507", "182edc91170f86a0ffe94b216632615f1b2faa0203d09644890a05503abb32cf",
    "187e685c70e95a4deb907e582d56b6ea559f2e7ce57aa826ee16514d7ec91da9", "18939295b5e6258ad82b7db7d183fbd56207d54e3e6adeb26490af33df3ca3e6", "18bcabb3271c8b98496c6da541f163f832babc4a4ce1408a51a9ab058e25bff4", "18f39867f05e4a035faa2fa8caefdd4cae36857b966732b63b30e05436434faf",
    "18fa555c6c0e3ea8237c8c51a6113df40aa38d4dd620217fa53248200b8ed7a8", "19124b79808f32c31060b7b8eedb200c077b5a0eeb0f7818c5361f4c7b0cf8dc", "194fb666eb7ce72bd44e76a1c947f75b81d0ce8d75cc2fa00546b3578f30a3aa", "19596df1bcc74b0580dc3fad13735f7b0340bde5c0640e925e0be8ddb5f9dd7b",
    "1965b71cff51a05144a9be36f0c057453d53840dd0ff5c63819ae5fbd4ad47e2", "19b4233f28eeafc4b7afc531acebe3f8c1068eeece390ce6bb51e2cf4aac6ef8", "19e66a5177cf50c123e1687cb66061935d2f84c2acccaa06c7978f4929fcbf99", "19eb0141c528167e8610ab4fb12928e1bd8f064e4277fc56bb82c0d49fe6d9ca",
    "1a0db8c202c2703df4bce5c715681bae594d16f483a6487704d64d97a3db1ded", "1a157c63e86300aaefdd680845a8c2d9b5fa2682dbd563b24e633b47a781d6dc", "1a77edc58a2bc961fce3c7a0d8e583aa2f7d62d7743be8118a3f9a68119096a9", "1aab6710a2a4dcd62f9c2c2284771f2fe3d5f3a7e3eb92f27508131f5a25aa0e",
    "1abd82ea5633581b9a04d624d315b63c4230ac63c736a4cd40cd94c88ece3d95", "1b19cbf7dcf1d6ed7cabb1aee8089d11cd1eff7b63ada9e65945657cac25b3ca", "1b46ad7d7ff6ac50256a212a3459a82ee02e8ff049f5c25acbd56fc7a10ef681", "1b58b24a4e478f81c79d81482bce978d0e4b1acc00960a15823e4c02498ac007",
    "1ba9e98ea1a5aab03d6a8604cc6aa4710af38387f235bb2052475899481fb117", "1be3f58fd1054ef313a821127acf097828ae4bbe89628736e8a5f5dcc1b31a36", "1be7644d85e09f42415d2f2a854a8152b14b943d477db023ae7d642aea9c1809", "1c1d54cbdb190ba9862e56f058658590b6e9bdb1f6785460cd618ffa888c56d8",
    "1c42af4d13f03f76c4ee9e2918c9ff9a44c25d715fdf9eaf0e8c21deadbdf570", "1c4bdae0ab7a9df167bead200f6b1cfff9116fa9e77f45a6a9f4fdaa31a10247", "1c50572efc3e9ea56da153d6e16c866de5962aaf40d7de6759bc09c937dae1cd", "1c6cd8193f60e8beb4070c44e56f9110b01ec1504ed2e8240da86c57afba943a",
    "1cbf9b69441407c1c2fe62296f4cf9e512c1a7d955ca79b82b1b3dcf2e2c7dbc", "1ccb9ff88df93b1608314c997e504bb680d90950641785f6946889dfd570472c", "1d0d1471f38484d5f9d9a39ac1e4d5d935c2f135abdd5baafb0cab87774cc5f4", "1d4bf0e54ded49c50b76f0ad2bd3027d37c3056e9b05aa13ffe57ae7f19b9b45",
    "1d60664bae6ce8f544dce2a8c7e503e374b17cab75294ccc0771ad15eebe0492", "1d678d922ddec61197365206214a4f8f354d69608995608bd1f45e1a8ff0cc5d", "1d6e44eda70492a70707003c3538307b68e3e6895a14a70bfd2a454e57b6113d", "1d75081efcec68eeb461f0bf24f6869cd1961ed58c2fcac4fb3ca1896373628a",
    "1d9911a97132ca38664fbb7453d2b075517983234b42deaac054dca670c6a0d3", "1da3f3788fcf3109c4cda8a35139dfa2027e333dae7038f679e06441fee5aaec", "1db804fcfc5b7826d170dc7f36bb8482d0e9ca1fe6489614b9c67ce50ce77a46", "1de81708d6e72a94848351694d087d8e282e47b156a5bca696becce8d9abe80a",
    "1e2cb517f07a2f99433c2132b61c0e0ed803cb83776cc5611b3f57e4a5efc0ba", "1e3af2f7b3ef21b1c343884837f96f7912e417f65b884cf207f1b0a629599792", "1e3ed480e295c629e83c67b265e6441cfc23a461a1d84efa0e73fecafbce05b3", "1e66a35ffcfe43a8d47147b6dc589a909e0b8f7297882015222852481f404ac1",
    "1e7122bddd53f577a9ccb869c0bbe98895bcd4d96178cca95ac83b036d71eeb4", "1e9bb00eca1dd0b55c171d9f98ef836764bfef7d915be4eb237b064a5eb04ad7", "1ebd052af7537d4bf8fae0cc76e9f18b850939c07d5de7fdca47e44d4a0a3fff", "1ebe77ca2cdd6e607f2a1c010d38428919a5685a28889537b9030c7426f063ae",
    "1ece2bbf51ec15bb794f564231e80c4331b308585fec93ed5968a6cd02766c56", "1ecfd117f7c3b2783d2bbd323c2a1e431bf03973195d811ff381cb058301b540", "1ef4a39f2cee6e4a4d8f440653084e4f5dfea487aa29cf5bdc1fd4580998b2c7", "1f66a49a7776b1602ca451afbdd5fde69e6e0ece3a905dbf1e25ae2307672792",
    "1fa244ebb0f30a9d95f2d699b6447293a164f040666865d9f988729297831568", "1fbe3368486cf3159a8e2d5e7e46f4b1ae90f1146bddfe9892a5ee8f8ead5125", "1fd39fc95a3673172bf38f9a23b57c42ce49aa0ca0249bd4788c708b64fb5c46", "1fe411c3fb5ba573fa7bd9103c0cd722a256b02811eaa6dc85d8fd72c83585b3",
    "200cbc597a217e38182a55a8ddbde8a79d0700040993a344c72be6a3960fc267", "2052f6edd9424eff6e10ab1a31a841128631dbedfcfe84fc0f37f857cf1d507a", "2065b7a43eeb3f261384cee88c888dfc97958c3233cb1aa8eab6453fcf96cc39", "207da854486117665445593ff0259489a2526e8d5e870cbadb09bc2d9d8e19ba",
    "2094bae66e63c55e5d7bcaf81b94a48768f09036b7c5ce04358dac91ae255d42", "20a2f1d5ef81d9623dd04031bba961c7f7911cc901cb3bf80fe489bff055ea17", "20b1cecdcb64873acbd8068a13f96f8e5372f8a225d07fe4dc8da74ad144a37a", "20bc09c9c9f11c799e623348edde641ddd52ac516cc74549dc035e1e1a2d19ff",
    "20e59c9c1db4b53df34f7e51f918ffe24f216807e7fb8a9f3ef21e48f60ff580", "21228a16df0c4973ad5036ca270d1625d538b4e36b73fd6707c52e8e6d09fb52", "2145e21fa3496f2a0f337b5ca2c787cdb8a2fce5678c9f69bdf0b4bc6a05e7e0", "215d992d90c97e02f59670962f7993c5c9d4273701f051df0f00931ae5acd4f3",
    "21822391d35b84e2895ec9a0ceb8a47afd8612783b67db7b750dc2b0c43317f8", "219eae142361d35d48875cd9b4fa0a585163383942609a7f0bfc27027e27b2d9", "21f85d92889b0a6c7d947dcaee2063d2050b0b6a580044759ec98df4e31821e5", "22a9362d3aa21abb14c8cb39ccd6b921c6c88d469d04134994d0fa8d735466e4",
    "22c07e31606470c61a968d6deb3c165d1e77b66e74f296cf298e2ba1605e2292", "23019bb1b24548bf109316573b7f6848cac1dd5e28d6216f74bf1cfcbf3e1837", "232aea42972c053ad71321dfd4fe5b9eb9db6a36c649cabddbbc457204748be2", "2350c96b9145155e33803a61e05d59df5563127cb9f72b9731fcbc698ff5e439",
    "23511ccd3713b701214f0d1922047238bbaf435e8bd105eae26cc5193309e2c7", "237c8a4147447f5bc0adb645523cce988c1e573ad018b2861c872c11e288d682", "237cbb720759ae50c18a49fb9aa35889540e4bc1118afb9bee2470aa7a6eadce", "237dc23f0494729f7aa324fc0f5219d63cdabe46fa48208df2f83923035e8c8d",
    "23930fb1dca11080fa2d67774c95dc9e3062e61ed5efed8346a1b0c842716671", "239bba95646a7f897f5e6195e5b9515a4d73b982c84a68b90a3229a6adbd14f4", "23aa8996bea65a7cb74aa050511b0cac1d0319ed057b9714706d4406939755c2", "23aeffa63ff42a793f64b0fbb58ec1953209d1d9b84ac3eef34c28d23f748b2d",
    "23e42440e1eda28bba93521d918e4beecefd221193186ff99633b96d392013f0", "24247bba75153d3620ec7b2c9cdf5fa519470a79b0d1df9974eda1d7ab644866", "2434a256527d68caefb87df6bfbf8d99ec608f77b26bd068c1603c365582c571", "244aee164aa077640fac2e1f7796d70259ad972d66db62f7f1311353987af141",
    "246a33c3653383e806fe9d7af5be1df781313be0bc58f8fd449622469d4208c4", "246c8e276fccc9ae571329a915f2b68fa19d95c07bea1a7885edc99ef7653678", "24756b48a6115bb78067c96200d4edeb59d6b5b17190c7cdde8ff5e66530bb98", "24b605b309a6072d79b6fc5f49072d1c288d6acd76d58d618baf6410080f4473",
    "24c33227de88064d864876c1e4697832f9354a950e2348605d417e8bbc40f81c", "24d246ef1c2877aed8737f3a4348f713845aaca71078521749b4475acd49caf2", "24e4122357d95c0be2e08c1c5c1591c2cbfa4aeed05cb00de930dcf0608f03fd", "24fa68670c990916927c43ee5b7ad1a33ab1c505f7579ebbd160951d28beb9fd",
    "252fe95ca98b0bc4d539ba40601bb05442e2e340ff336969fa0fb61298c9736e", "255043dec9638bd8eb0328cf90491830b685718f0549d6bda464bcf53ca0d8c7", "25839409b2ae86680b3dbf51099ecd9481e7d1a3dbc2e1d7e9141e36fec33894", "2593b7f68f56c1ed3f07741724e0c794907a4cb297b3abb1054e0894ceb24315",
    "25a39f615560278700199d9df6dca68fc3a336e05a1a1a6d2d8015b0b551eece", "25becba56e54280c337656bbf5eb64a9109ee20566f9e364b70edadf9fcefe5f", "25d757a1b9951a2776dfd95854302edc3cf49d87fed94d2d2c0802293b41933f", "25ee365ffac03cb23ccd376ea1079f793d41b39cfc27df861ac62ed59169ab0b",
    "25f02359b08d7d85eff81b01719d2af08365a304b9159e29ac38856eb0298201", "261dff06927a7b1edc73a5c3bae849342bcba561de6479efe2d2294db5096b07", "264b19828987340be6f7632284b40cfb26e914c8c0bb809c63e4fae7bfd1499a", "2666efd068138cb87cddd2f681f347b11882aad8203c2ab28767358fec05bb8e",
    "267854688ec7a4fdf209a73e47c98d563aba306da9c90a5e3733d7147cf1ca6e", "268415c47cb70a7ebe8a1922a2ca14680a8bcf4c6f7a5621a9008d2fc92a0ab6", "2698d8ba9c47af2336c7a944f0e62b6ef06bc8ca39e5ef598c81588b61916b6e", "26cf32e64b534cdf88df111a0dfe7f30befc04d931821d52c6084a6e4b59a97c",
    "274dc9211584778272f6c6dd512dce5a3d9d6c3fb78e61f30db9a5049d27731a", "27631656b86a1dd2741724793428da149600b190eb4eb0f50cbe823039c11f86", "2769443c6710c110356750db087642c1bdd9e2e15c261a71513eae3b802c2496", "278ab8c1b2551ace41963d9c95375228ca220c2c883bf692262c180d5611ebee",
    "281cf0558b24dd7623044d18652993d353f18441a933b1fafd0bd1f03501f8c8", "284a89b407223da80d7c27c1d360e281e022a772c745cf30f7a8574ecfe7e951", "284f529115de42d34fdf588ab4e8e0943806c73f7e5f6235ee95645078be5638", "2875691d0a0c9154736bed33428d64a7e0873a2ef85df85496cbc7b33af9fe15",
    "288b3e014588905a01786b57941e3fe34d8d3127d48767c07d189222f5e296e3", "28b9ceeb93a1ca9616ee6676aa94fde298fc62b3b2ab4ca86e63f39872ec45d1", "28bbd4b970983cfcfd65bd30ebf257ff3887699058df5efe9fbbce9793ef22f7", "29143d195685e61ad44cbdbf6612aad79f897ae09661471bc52e9824eca6ab41",
    "29280c59cc665fce4a638a9f9946e1522ae318255fccea875f28aef0edd9ac3c", "292e3174d3bd7f2abf725e424a4a90184bb674ef0c152c41bf0c0d0d345d9bf7", "2931c4a4920e5e0cfe33b9fcae3ba726191146ee9d42d7a151a436540604bcc7", "293e2f9cfae7f20aef28d31fa934eee6ac42392220fb70d38eaba57fc8537e09",
    "297fe421c2f49155cef1c77f452fdc7edacb94eddb4a59d33a5c16635a333498", "298c2e3a41d52496a86052bb611d43905b35ac68d5a676b34c998eb2e7a33b2e", "2997bf0e39e06b5b646edd4a036107aa82ac90514a6e30917127afdc62db7bd2", "299c8696bcdbbb5218481054df9c8ab41d3dc59036b7ae6288a9072788188240",
    "29a9211f81923c8df011a033aef63143b4d0ada82d2951ebe3609afb8f709662", "29a96f8fddb69e3672d199381bbd803293dae7a48d565603e5182c050165ed4c", "29ebdd829b627ee340af1ba36210c5641e09888b1bc434c7a3eb08f231353e7d", "29f80b8935d0354c2758fce9f7e86f3e46ec1ea45be877d47c1fb2587431fe6d",
    "2a17b6bb63da6bdf47c4e77a523a105717996c6e3479466cdc892b5de3e1a054", "2a30811345e9dff8a010777537c460c5e108c21c5c14f651482913b1bb5bbe1a", "2a89773205afa9157d8a1ddfc3675aadefa5f3cff45ed10c92a830e4ddfc457b", "2a96871f69f4bdf17876ce1520d326eb056b93bbcf2ed465f2be603907657e2a",
    "2aa46281de6243b42989de7ba52d6a1e1d182ce195956481258f95861e87c5ed", "2ae6f66d1b178b0cb46ad82135dc3bd2125e8c3c126b163bd86ccdade7c78b9b", "2afb21891f832db786642b505001546738cc868434fffc8dc1a13fda5ee6f2bb", "2b1613b95450dd17bcc6822bd10a6f144572946721b5fac56dae9e34cef97e17",
    "2b40a4432c955588a86102bd507e77f71e2c4bc7627976eba259e63054e089f9", "2b4879e22a481910997961357a014f4d912a8d3b413dda5ab69b02040f3c9ab3", "2bcb1a4c6aecc162ba3ef9fa7d8aa4b79879ad2498a0c98c108638d0508b4969", "2c2cdd1355c5877a28c081045268dd84fb19773fdb75ee041059a50ec51c0e49",
    "2c3b694b85c4e589f441ba9a2e30a24333b2eba7a2387e08c2f80b55095bec17", "2cc3661f5201181a87327294d8ddf2be78f49b5032c3d9b965163fadc7f4af45", "2cd2c8caa2e0514195d52f5f32308a8ae52781738b3eee918954a49fe96621de", "2cd2f4844e2afebeea8760bf94ce81b94ccbbd330fbe0db78bee260d023efc22",
    "2cdf94dd2a6a3d8671e769940024f06c4a822fabe87a4e4c9678de968dbe45e0", "2ce702835bd972ebe4ca5a6c4a50e82adfc9073d76d0e0b4055230b1979196e5", "2d061471a4b36c26c4bc93886867c5f17b124f7d8b8672a2dc2f456636eb5d00", "2d21da7ac94ec023f32dc81b99250fbf7cf464ec89a25fc7d2140b5252cfeefe",
    "2d2b2e84255b9d6afe9ed78499eaf58fbee82caba8b35458bfaf846a8ab3c586", "2d35f20448dde181d606cb876f884bebd73501e4f887ef58b5a17e5237129d20", "2d5584d3e43b034c4fedd1bc49a85e27bd22f1d1d25a4a3fd58c358e5422351c", "2db185a2c565ee12d1f9bbbe7701b64c99f244275875c558f56c845abddcf6e4",
    "2dc6289af50c1ad3203c9df9918279b0197aa9689476db98f672abae54d0b5d6", "2dc63d49da937dac422a194d8583b00e11b5cd8b84595c5ed9d1b72204bf284a", "2e144a5e94fbfe9ad7fa0246051c75233aabd67401123a31577aca4120cddefc",
    "2e15b67934740d1f2196802f53a4cc5f4e5ecff0b74a732e5c70a322ad6616fc", "2e1cdccb5ac3fa51804b65f5db2ecfa77e25531f67791067dcf88f0b135266a7", "2e22a862efeb92855be6a86b174635a344ce1408da092cd3787bea8c6e27e84e", "2e3fc4a537ac71217b99dd86668750de90d8181e70232537601449f3db9478e4",
    "2e480959f8f205c487980252ab14770f9144ab7dcda258fae2dad85fe0e64021", "2e99a1bb53ae5d1a721bba772b742faad9b982c1e3f2ec80de28bb65a8666de8", "2f00040830f8fcfc0391dc33a01f02cc3a019979170c985b61f6e075d2cad9d7", "2f13dd08c25e862a8eee4a8b23446511b14eaa5d16a374801ea6e8d5afbebfc1",
    "2f29712af08ed85fb8cddc17d1c14526451043d1523e25f41242b57da94447d6", "2f2d74fb9c8e38c6b0b93d758e30bcc0c6d93cbb8c4287697583e789a7d9453f", "2f34bb31912560aa404fd36d847c1ba7c2abf4682d3b1cdd6aeeb184f5ae021f", "2f386a90b0624aa993e157de249039f3c9e2c227d825e212afdc98ed59d83487",
    "2f3aaf778a737ea6faff7228714edc95ef6b02a1bb74aa14fbc6e54eb34d79cf", "2f54f4a8738cf09c014b092778950a144a4899711e70c6d620391a48aa251d68", "2f58dc81cd079cfe4bb65270a2df28fca14aa80c4c30bee76570f23c4ea0f52f", "2fa70568a4bbc0d53b3baa5234052e59fc593bd49e094fa7a2ca8a5396e34656",
    "2fb4deff486f6c7ccf1f7a03687723a20c3a2a23dd700b92aba6a10643d2084a", "2fb8f88d732be693bd5abb4603cf2eb7736fbee47d6d00888ad11353f6118df1", "2fd05963de0b9fd6bdba9c1cf5064c860d3e92f6946a94031ef894b411b7e255", "2fe1bda3934e2f407ed8a28c8f720ea12ad67dd65e0bb96cf64fd20a499c2cd3",
    "3054e1bf764649537ee2377ec9e09ff7c66a1aca300d18689ef9b6491e704e06", "305758f5e8d400f2b150ef74da5d04d4abe2693479dc7257305a6594f2b25b97", "3072aac6f30d8aea77c3099e9dc5827f84eaa3c3bed73aa14e189f6cc78822f5",
    "309c275f7484b8be475714550008469c3a836769a4ea441da72ec739e6f3bf43", "30b8ee75b1788c23a0f37e587ce15a576dc181855d0ff3037db878520443877a", "30bece4a1a8230c6ed43b235d077a38a0fcebd1c90d8f91cba6893b9e682bfdd", "30c66147da41d65267374244bfa9abf712701fc35ca2887cabe3ab50daee9b75",
    "30c701c5489fccbb76801c185eb1a4d764d5d26b52ac9d6acbfda4150454ec99", "30d7300ce5e22d62551d89236f84f28206237b3c4024bd1134e164a79dd80e6d", "30dc4d3bcd75cc019ddf6a0d8a3b12f51f8d56eff7dc83c2b0cd43f01f950521", "30fce5c348e04f73e1be1603cfba48efcb4a89cd9b946d00fe840e265d250bf0",
    "3104292708664f38b78c4f202d42790a87df156a1a2d6f059e0c7506e8285804", "3112a2366c31634859544eb9752d96072322c35423122a1327b36ab6ebedeb36", "314e7a355754a0e9f6724564960f813e2383c4afd7feaa9e35c95b764c6ac318", "3165554104ac78f583160124a98f0a6da88f3ba17ae43941c616e7ea92df5ce4",
    "318a108e2bf28c7a45a58092484cd5a5a328e6d74e5818ecca298b07fba928fc", "318c36f0db9660d65e8278f1a4149acceefb09836cbce1162f907143b8ac7fa0", "31a1b1ad4580c04a2b3fcf18547d989fc4e911506b4b75a905065d034ff7ab6b", "31af54db9f890f6803c454c946e48490ffa254aa115dc8201b3893f39ea8e017",
    "31d76adf5ec7e8afc54a50d2b51ac68a2fc65c1606061dab7b790c4f0e26c922", "320821c3255ac28b1e51ebf7c99d5520ce252114e6e17656ef22ce58d5c0a2c8", "323b2306827ce981ab0b13d0ec11de4bd71f92ab6d9f5b16f0caf20a74216520", "324e6243c0d9c7c2e13d53245dcc52f4d335edf46d62c01eb120e71874b7f49b",
    "3256ed3244e5c8847077ed960f0f5cae50c4d13785788de9d0a5f9035540450f", "3264635f747866ca8feac83e0e97d22bfc65f6ff73009b1fa272b5158a21ee12", "328dd435a090322204f80ad305b1a2a467a8fad6e4de06e55fe7a9a9d4739376", "32c99ab36eb26c7e1b33f69e1f4ae1842a88598d189cff419bf98198e766870d",
    "32cce8294575880ea347d50b49bf61294ee3e27cf032577c2a27a5c82b0625fc", "32d37938fa8caa779d95532ac4c5e802f826f62dcccce9a95b6659f2ade96413", "32f91d19a545f891714633c61141a1e57bb970cef51684078451b90cd5d530e2", "3301819575a428d2fe150af98c0819f39452d4c4aabae4865478d33f3e1260e6",
    "3321cca68e03efecc1571e8aa3a57b49f1f2a04ceb074837d05fd3a1d7172d7b", "33223693332b5443b792af2bb9a104b6a44fcabe09d21012361d59921b4510ab", "3325cb38449897532f2e1330d18c884429414a328edfc1ab587b7c200945255a", "337a793c284a852de03b077f78f8dff46e38ba3c97fc1434416ac3a0523b8ce9",
    "339c9f811ea182ee8348fc6864e5249155914000b10cee1e0afe3ced5a9a3392", "340dc696538dd5ac2c332130074beeb647fe478c835beb7de13c1c97ffb99d9c", "341e1ee9c43752fef69e06302cdf81245faed1d18ec2cff2de75de11bdf85fe7", "344b66307863676a02f22f49f28dff8262737129c0c245863263e6045cd6a00c",
    "344e2a8f18804f86ba114cb1f1df44c5bc888600251148e04b34fb1d26635c3b", "3498582815d5a7cab8233ded52af0216632b3cbc8fd92f4856163dede1cd6944", "34a292e69dff8a178b15a2b56407b61b0b10c53d0d96c02a41cd9df91a17a19f", "34ac38040cfbbc12fa0fd316427ab25be45a583034df82defa16ea098df28c64",
    "34d96fdf20819f8bcf1a18ecd650cf28af4e270682918c11ada7b7b6a390248a", "35280f40bbb09a14b847b49e993cfce9a224959f2ce2c4660f4948ce29d16787", "353b5501d49efe302b5323b90a2453a2737af45aa93551d012a2743952a0728d", "354cdb21f1286ce80eb6d2f3daf418241adfb68da9affd6bd28b15255468fdd0",
    "3566965088c6ef8ccffa62cfca10d134395b163d4305135effb6cc0ca6541f9b", "3575678deec2fe0aee4200323f98542f887a1ff12bea68a1606bc06b565fbf0a", "359a3b72b756c77295cd4760d53bbd3b2b5501ee728a7d55175b43f667245efd", "35c36c558e133acd36dc0f7dbe4bdf8acc03ad78112e9feafcbdaf57ed8813e7",
    "35c44581f3eda7c289a6c55e83e237b5ccdc6b66729556079e4987bfecf6a1ac", "35df9c9ea9769fc11992d9d30852c96ae29096f620d94cdbb657309aae8c56c6", "35e59941e6f1a2337f08352af2028b59a8fd08c85b1ffc173fbf7a56e264bef1", "3622cd6fa17825eb3f944743f83ba3bc7390c6e6cfed4ccaf07400d986c44bf1",
    "3654287a925a1bfab3739fa690204a95c94c44bc25b27dbe044fcbc51f209ed5", "36a8ace8a3f2a977f8cba6a39ff5cb91353f2c9ca37ebb111e4fc3b038434216", "36e26c805756ebcb91164efb547337cb412758642802eb104d31d2f49d6680c2", "36e9d8e39eb9e17664a39a0bfa7d5beb3279c797050c049145378df937452683",
    "3706320a299723c72dd0f41aac5d10588e831677f41df2f5ff645c1b51404046", "370ca986992e1bac0c95b313352592a1f8720337e79cddb1a488de87bc42eca7", "373bd2058132f41b8e6b830c019d41f08bddbdf79aeb6bc05f209af44894210f", "3784d73fdf0dd403481465c01f1ed279e0cfeaed6fe1698e294fed60a3a6cfca",
    "379c4f652e9ed1c0a367364b5aea3d46c6fad052fb465a7ee3c09e4f435d5976", "37be8065fd7a56ba91b0f7de483ced64c654b13d615e607b0b9627fcae7655e8", "37ca704a731939abf63905c0297ce247f1a049c18d04b441b7143e7af9c9051f", "37d1668460eb8b39f009ef72cb86a0c9c3e4200c3452103e065d40b04ca2a03e",
    "37d87a7c15af8218b4f10f2e0610ad7054585d6d03b5553f9ea5e7836e714040", "37fd79967c6800ab27925d77883b84de4f6a5e1c7b03bd21d0f78962b6f861aa", "380e5f3ce18b9c3dbc666b15fac5da2875a164a59bb1237d5a4bb7ede629dc09", "3826af0caca6e0ec448d8bca6d7ac443c2f717ae2939c004bb0a8774abd3ef7b",
    "383004cb68a591ecc246dbe3d3b7e53cbd2dbbebf7def6ff84acd982fc160dbf", "38a1facd4372f00010a2f2764fd111fd3331a21aadf9653b9283971ea6ab3444", "38c515e093342fba5942eb980996d18e18a0f035b6760a1b80656374b395355b", "38c5941e2acd6c1241100d0766f1eb7c75dbe6f71ae2393179e5ffe2e1b6306f",
    "38e22edc442ea88393373f20bab2321860ceb913c33b5a33006a764872989812", "38f2d94a284eab9da883ef2aff4587050dbfcfb6f2e9016b8df1a72baea3cc7f", "392149f840c1de8266d1436593d800353421dcea49e99209266de5379cef753e", "39247b873acb0aa9ab61e339d6077d055acc2fb51d45b57b09a33360fee72c72",
    "396695616b5b96877b389ef63f9f5f0b07728a72c9c9fbd5f533ebb763063839", "3971724dc6db67bee82aee5f34d1df333563e89f05e77ec2cb7a40af60c349b0", "397c93a292a8cb1e0661894ef57f82a39d48b2c4aee01f5cde9bae7432dfc54b", "399ccd36c50ede0abfffea0b4bb4d909fb323babdaa5924b483dc9ee6ff7c24c",
    "39a671b30fa20835759edffca7b22f37e91916b4ed02b2c384009091d8bc0ac5", "39d1ebdaf7ede87611e44a7b9c13297c3b936f556fe5644a258659c3ee13cb93", "39e71b0eb3c2eb6a9100635725c4450653c746973371cbde0d27351c47aaf425", "39f8502989badc44de7e49c4eb7f627e9255bc97c6fb2e4b858f52179f212490",
    "39fbbf7e48a7d1a6fdd8c16c3f7c6f82486fa7b8fd003a333f0153a9f5bf5e23", "3a1804e7469778091377dc66f1187a43cc187c612458da54430a8fe6352a2d32", "3a1ed59615c06c6e6e8f7d8498e411dc9ae33e404d810afb325b6488ab16b63b", "3a622c21ed5a06fb38571f326f7f5e5895d3e8d57f722388f471ac00e7f4ede0",
    "3a8897b626881d614c1f7f35298a86a5cd7308f1d47c37f57e0b2c065c48394a", "3a8d2d184e86137e43389f7bdefc2ab5d86581e43ccdc72561b8f20dc2b0215f", "3a90e2c199004cf7bd20dab193d19543811ecc9cda74b4fa518bb1d217f3f52a", "3ab9c1addb7c32e59693aaed12c117e63cbe4541b9f56a9eada0856534d58ce1",
    "3b13108e6983ea6d301b973d67d11a960e76613416fd6db5c6b239e896c2a6ad", "3b24f2e329a133b7e785fc883ee122a5d3c772d8231ed5ff9572f1b39bddc140", "3b3f5a01e404f5bb4c043510f0b90b9b9182c3f2d895f4d106ae334ad6ec6b7a", "3b4315c929a344e711fe54ad6eca8d489787361a7a2b52e9b549a746b6e2434f",
    "3b71fe1710a8af98921fb2cbecf1a77b03cdf3b2f8f780390c1584173c7d56da", "3b7419931e29cd89b4c24ca842d40f88b8c443cf6fcdfc4626862ab3155b1e4f", "3ba64296e711cb01e30e69621a89ff3df6501ef8d0da10feeeb2ae2dbe1202b0", "3c4c0650103d5408fe3ee908da333e89b70211b29e4bd5416eda22f0fa335d3a",
    "3c518fac4be7ebaf9b9a8cc73cc20043464db0232a86415617be7f12ff6cf33c", "3c55863fefc46ce5a6228e57954f46bd2e51c06b95a72f2bafb1d2eac558a51a", "3c60dd2c4a22eea511f60446aafd948f394b3ddb8fd99b848ee2edfc25cfc4e9", "3c90837f33d087eb1db747d4b6562c94d6800c26717d614d65659b69b5e29b42",
    "3caf6fdfe57505427e4a800007da6d5b5f30d2cb0221a3877c6c2d2286c0209c", "3d116e5d9fbd42569805e6e9ee2b8b3c851f844668487dcd7c3b7d14cca9ec0c", "3d795bc5365b378e8b94e47bbf1a6f94427b17d666356d6992a1962e518e014c", "3d79f60309ea5513416bd9e2d5eaec30e73626ccc095ff5a5232372d5ea3252d",
    "3d80f1064d592c8402959cf7b0df211c503c49ad572956dcf48ab06ba17ad57e", "3da4872d594928e57a2a95d942aabf7c14fd60f74992b3bb30d3f42ec2a5d631", "3df6bfb2682fb098d2999c1f5eebf409cf12a4b2db56ecf8c2a7a82d44d73840", "3e025931d0a31e287e950f66a0b5aecc6d32b7298ea9baf16bb9269110f916cb",
    "3e3a1f1c93dda967481d6cb60aeb9a6d35d400f9a86392a5192956ac4a3d7d01", "3e442f87afc8e118a76c731a212e6806fa8c82999014f672dcf98c142bdb434c", "3e77eedb3f1e9b6a92c8875464c251b97531b37c1e77d4bcfca09f342d9b4376", "3e86dbfe0139b8da90444264b3a10ea8282604666baffbf1923571cb85d4ff69",
    "3edbd4d763d963d8ac8c14d0616d3574023111fc99ac70111df8a663f385a309", "3ee8017c77bc4b6825e0e076b7a6ce27efab8f529886d3a1fe0e0eb186cc877d", "3efdadea8b54ccfd437ec7bc7da8285533c802b9b4150bb4bb0bd2f434836b50", "3f2367eaf7d99fe572d613317282adc817656b376393430de29a87525a7d511b",
    "3f4f6fccff0ce592f5bc9c777fb1fe96fcbcc972998562d3a56198b608da6d3f", "3f5a7f2bd02230d299396031baf81b0f49fafe9828c45a436ad8aa9e98dda1d7", "3f8074c557bffcd30b6a1f9fb82542cc6b4f0b5ab44596311df96d252264d0bb", "3f963bf7fb455b52ba055e846f68d6c32d0959021155b8862bb5abd4903df9c7",
    "3f9fc9ed6ce9f23eaa4cd58b35ada4082a41911d418bf4858fb2b19306f97951", "3fb10990905fc39f75408f4c23475ded45773078a8ac458e04bde2c57bd147d8", "3fb6f02855002789ec50d10c26039ce7e2b6a0658bc1a150babc2f1488b7f955", "3fb7ddf078a52d285cd4212b9edfb41a0324b7478a325cb10a6f00d0853f6ce4",
    "3fc36b2e58d4b731cf308c887ef425bd97d198c98da0dd48b38461e88747e47e", "3fde1bb5b27cf3da26456883362168f77ec106db8999c3bccc11d47cbe2d0527", "403ce9ac23df0cd7b7a66d54c9a93d72ff51ab2efd1e7109c45a69ff71a54aba", "4040906250b715d0c3c06bd987084fd9ab3fd7c086c3ec2974a005dbde0bf8ae",
    "405728c7a23192a9b93731c5686b8bcb12a01fc4d71117f09f12b6573290b921", "406e4b78f6ded6c8d2ce9164b16e1a674db6e66dbfbc8d6a2f4068eb75c9eb25", "407265c1395223ab0c850f2e1de0342d9db54eb08164767567006306564ab8a8", "408a9892a9c5c875699897a3c59c95064921ef86a6c0bf6afa31987bdacc1163",
    "40ad0a66b745d9087870b2c37b665118e45851936174f703995f16a51e615c78", "40de815f82cb51265feedc973cdc713785feb06cd27acfd033f421ac8a744a03", "40f7f4155395b1dcdcb36fcd4abf8ce0d40ff36f312989cfde5cfa3ed4a5f2ed", "4121d178183462fd2addd511f0adfca99385a9dcefe73d95a1417baf7236e81a",
    "41231d29fb35909f7fa19a1aa8c99c9849523802a721795e0738ea8d552652a3", "4123d9ac1045a99c9d972ab0bb65042133734d2a079abb640c0e24c54b9b58f4", "4148550208ba51f518b519cf19dc31d2dd9b562649b01e334c8be87ce65cfcdb", "4165ab442b6a8154834e43bd17f73ed77957722cd8d4269351868e4a65e8c7bb",
    "41a5f3743b9ae6901e9453c71843ad16c16bca731e76ca7671de29a269aa592b", "41c66483999d64f19c892770b1034e7e5fec63ee54736ff6ac6dccffd6bb3cc2", "41f22ff04bc8807ec20453bd51f0313edc63dcd52b5f1ab1d5b1c207a066e82c", "41f692802f133d3e6bdba0c498167c17d584bf448155ad56ac6c2d05b4113448",
    "421bd9c77f219da97dfb260a70a7e9f0ba00f061781ff69c75846762a5c35ff9", "42227c1f2f0c76e5f9b245b4cc2547c1209c5ce86b4e63e9914d486d17548ad2", "4249cfea55ca0be545ee640e73ea75b923c44513e39844d8fc7332d886fb2b85", "425be254fb47b0bb8dd762aea2efc1f486a3a81aaee0a922b94d24d183dbd944",
    "427111457ebf53e618d1d02a5408d3d884ab195b57da1e0a2584d1e758f59f80", "42718c67130cd1721999afa02cf480466a84d3e340d64e3f2305ce5d9784d151", "42ccd6c2a9ad26ccc6dfd3089bd4fe153cecfbed9900d29b3139df15d3ad1a87", "431cea4012bf1c66c0bc8e52e869487c70c55ecb8546b473fe7730180a0c4272",
    "4326db536f1f673c814ddade18587f4cde5b33776726f44da51acfb4bd06b649", "433e5b024a01cd4ff2cb7d2e9064c3026ec73934473b9b30f27312136d867f50", "43587bdec54fd56d19c6cb79ca9e776180a2a59fb28cdd49ba0582270a894235", "43c13fbf7c8e25157ca0371d8f1532ca35581035fbc57597696ff72f3c6d7418",
    "43c62c2994267512a022d846e4f8fb9e331278aae90b441c773f3c84040bfba3", "43cc0c45b9ef3f242724e90ed96439d0ab7df44c85eef8817c37f8e1a81a7c3c", "43e814921c498faa7785010c26894548a0a40570fb2299e4445bd7d5f4da7f0e", "44117d2a236e3283b33f6d65eaf757f007ad5c3c62e352ec28a875e22ade069b",
    "44200113489913f639e8124f66534620d6de1ae0ae7d8a59a2eba4a62584a55b", "4454474586d6b6e92237674bf28225b54c8387d5539012dc74c31ba69135dc16", "44da34c1ee3ab532c34cad8a1e9dc2ff76884753b9f3f4524967220557f963bc", "450dc87fc1242166fd96698b2b7716f3e6d10f653725f0189942d98b25c7cee4",
    "450f2877afdbbaeef014a015ce9735f931d0c0e6bd56d222ff166e94a5c9883e", "453f77e1e64fbb65d186b57b3a2c619d3c866869452c88d552bfde5b0ecc0746", "45636ad764a719d30b3c5e35dd593c61a9758feb2d28abf8b6a9a964d5efb16e", "45821fb344991dd4f554ba0cfd25664a148669e56d61a3c9414969950ae5f814",
    "45af12e3f186574fffa3f31ec88666e7fdaa4808dd15cce19c379aa2a1c084b7", "45b3138d649e08b29aa5c7883e22d6f0aef6dad575ca5dadf05403992182a0d4", "45c4455986e04e45d6de4269dae83bb2ddcfe93d332c9b1838dd7dfa0eac4c3d", "45c4cbdc25ea3dd1964f3bf19df398c730921e74a4c7ceeeb709505f02673dca",
    "45cf9c899c922dbb9cd84cee4401437030aeea73d210c0d6b8768e43202196b5", "45de51728755f59b0fe9ed7eaa81905be0a2176ce2e99bea0519176c45dfbebb", "46003b23a653f963ee411f42140011626f097c99f4b3c5f25dac17f0d5b8fe0f", "4624f48df1d327e3b808a49b0e3e808509a1d99aefe4ae80012d7fed98c24edd",
    "4655c2ec311c4a1aa57e4bb947d3024e416eb17aec177319c5117db5a5a6fe61", "465b6b959b854a5991c7e2e28fa7f86d89a9ea1d07a8d87cf82afb2b89947d7f", "467a98c59183b7c92d7b6818fbb6f7309e40c8c6a2e2c85a7b681a1bb159cb45", "46fb9e29d0a88951565c1cca63a2757450d8cc448ba86e9809ecf3c37aa74d9d",
    "46fcb29a48d9e78444fb5d071b2208f623fff3a97addbb7163e99f67be77373d", "4758bf3d58c219c7b58a3115eb0a77c6db3f7f7414a7169f4cc41e7c68ce0dc6", "476d47e81dd7cd92c1bf21f941124a193ee3cbaa30cb3ef6ab0dabb99055b7c0", "4798b6a2726debc43871e12e2ac8f5e7f4d607e650448ac2ec9baccae561c765",
    "47c0f0142e04e47299eb1372d6a040dc31483240bab09dac96a9b330ecd39849", "47d3b8091d792d5fff001b97bbe658aabbdc9a7d65e3545ba077414536264ebe", "47daccd4e6c5072569cebfe7ee042604fe6998eea99452e016f8ad97bdaa67a5", "47e601262aa4706fd1e558f61df95d542ff81fe0a24a9360be4c59565b38ec80",
    "48010a6036b40388497d7bbdbdb2bc60adca63d3a2c36e98ac7de7dbaed7db03", "480d6a3a7335fd1a3cefd036c7598927b15361205120ab33d11fc8ddc533f7ec", "484cf1b3e25f817005cf033ae12315e0a066559586d8bd20bf1fedea6518cb17", "486bf5c6af60a738e9037564315bf349528484acac7383e7f62896990408ad65",
    "4892ad4dad6a9e3d05ce4ead488f4ad2f34d9511933e28909baa24448a6d3aac", "48aa8c7f0d05702446da81f5bab19387e288474c9575784b96624f4167fff983", "48b1a18ba1c04e892caba17635d98f2f0ef6f2f7f4e6fa3e458795fd3ba55d59", "4921086bfb6eb1d4573313b6ee60b7eae5c1eef6fb80983c452bc2809855588f",
    "49252a4b4ac5cccd5e8577d1e789928c8b2ec5bd8d6bfc0c4358247742b9aeeb", "49381ad5d057d0c3fecbccd339b7e77cf1ee4e9a5e3604caf0bc1d4dfbce88d8", "49888a70d02de343eb6b567b21f4e67ad1ce698984a3af1832f6e3536c74ccec", "49934e30bd698b6084938ce43cb383c230c36abf72c077e376e02d90d56f26d9",
    "49a2b5267eba02c8926e8d30473658ca35a7c5104bf5e242c23881e3347a9110", "49b4d1ae35057b31b64833479afd978ffd884646b6bc0a0d9093818eac142812", "49bb7d096a3567543e73139a48cdf93040a6bd3777d6a8f73d5850ba525e4edc", "49fd62e4bbd41d9a650af7257cd2daba0c5f90e7c3bca819355575d386406009",
    "4a07f4f0baa73a26faee15ae51d90b36c6764c0e8122b470cf9a0704219545c0", "4a0d9d541e3df1b8cd2cf1f6b324797d1c631858e3cf5bed220f19ea0b0dfa3f", "4a1c75c568eb8c4e9835da3126ebfe6dd7cc3b5f71bd15f4d30a9c43a2603190", "4a4697897959c7605c0e6108025bcd383de129502d9e61f91d6d2343d4c63761",
    "4a507e00d611d6c71870929ef32656aafab31ffd66dc7dd86115bcb2d36f670f", "4a608f3135414e5e28e106bd0db8edc8e77992cbfb33b949a99108fd1bb6226a", "4b0240b5915f9344b88572af15b75f8c7857d98c26e4ca8d77fcb19982b74440", "4b0ff1a6a3eace5fde16a4c4f308af9f7a5a7ba7409886f929a8591ce09d4a43",
    "4b3d1bfa8d1ee17a8fa6bc11f52079078a118a98fde3e82b32b4fc61ce712944", "4b444112377d9b6ab6e6d1290acd9e7ca2a5bee50667d756919dc98772a684af", "4b487da2281ab5b28bcd15ade290bce350bcd9a182ae5356cea491016fb76a3e", "4b8dcaa5d7bb2a1c1e95f98b33417aa6837398cd26b3185ec0ccf82eb5b5ee7d",
    "4b9997496e218339e19e5dc1b18e4a83278087decb83a6b5fceb77d37fffaf18", "4ba7f9e4847f79681ef9e1a44bac076f6de64c1c6b64f275243108b7f37220d8", "4babc87043122e1ca55d9048e449cc05179e604cb2b4e4af0a332a8d8a6f5671", "4bbf984fa978b0c219b57062c8187bffdb5a60c4ad784ba78699e7f114e49966",
    "4bc63e3f719da29b063a15ea3cbca7402d4ebadd250d65548abd0591753e4692", "4bdda62c9b1e744a52a52c9b69398f3c36e221a35cfdb5b8c6c6923aa18f6cd3", "4c30d8d388583913824a6bec79b4e7b20a8639008d68901aa554c0902e81b22f", "4c613be40f3fd5acf4ead599b1ea77c81d7d0e399131a76c01e3fc6e0099922c",
    "4c7505b0d8836d2a27b9b946f012091f36d5f7ea4a2d68568838704edfd349c8", "4c8bd36c9a15027feb1dd1be3f2e1695914b1a16c2b0e06f8e986f2fef18f89b", "4c94bd49801524e1730a1c77ab31e576ee98d3a4440f9417b28b4577b707f565", "4c9872d238ab0f6449c1d37fdb62ff38546ed9d74263cef99bae63bc7ae83802",
    "4ca3069f21dc20c5ee57f95e13f8be21df09a81a74dc4bdd5f81fbfa95e8d399", "4cdec7320e61afc214afd82f7c192bb52e121b06559c5dd21da1fb921a69f9f8", "4d2f0b8a1451f8e46721f2d30c5954aadc8a36f7e0d43bcd8ec40fde1fe805c3", "4d4e756a40b357773ae73950b1cda43ff517d65fda0cf6f043cab26405dbd077",
    "4d55af412ff2c354df1c7cacc54bc576c3f76c3b16ce7cb9ad8202350dce4f4a", "4d574a30f95e5eff962a14e2b33a88edc3fd3eefb930e987af08975091deaf61", "4d59ee152486f6055ae0a479ce9a349a6ca082b8165a8d47a9f18d337ef2457d", "4d80588cd6edc7acde318454862c2dc5efd3c22e5e6f47431487d8589300b746",
    "4d8632e8a70376cfbb94933d4439fb3d53cd908dde9e830b20fee8d9cae3d687", "4da94a843996cf8bc67618762bea3a5a59a0c4329ecf7a3d8634f480f695e1c0", "4dd152ef26347f22556569e7f654ac1fb92c9931f54f132446c0e7abf9338163", "4dde4ca22f77860bd52385aa155e45e921dc4df5215f0f0309936a2213bc810c",
    "4deb513e6c3cb9368a84d40c08f9fd760a5eab788d3922d75e0183cf064fd23f", "4e02431322afb66f48443e743c8c991b3acf41b3dc1cceb75acfc3843a85379e", "4e1cd8625259e14783365894821c0b9ed6bfe0a36fac016142148cb58d8327e5", "4e2e83813f55b901a4bb57c54107a1899791fb70efc7200c5b93bce20140a2ac",
    "4e3b5c1eddd936bafbb489e100cb1722c9aa39cee5698d83c5c4df0161c905db", "4e4118989d5073dcc17fb2dd10303ecec9b43c5d7684728383e2669c9cd97abc", "4e5eacdc6d3d5c2025dabef5fa8c54726faf51cb2fda93054db77807f6f4c547", "4e8221a2c4fe3a437f609749095256bffa2a2d5884b14e63fe1918932d977ff9",
    "4e8a166428182b0a939ebb108d7201a0964f5bfa04470df036403c0ecb37fe68", "4ef267f3ed98eca059a2309d331bb0f859ee913448c30f3065c5608f599df54b", "4f02176d7d1bab2b102332655fe3e589048dc00108fc235a2066ceb4721451ee", "4f05fb987ba7ad29fdaf0caff816a4c80fc59aaa506155ed4fa52af5c45f1963",
    "4f2af94ace22d157af48b15505753439c12236af6c8ffe918726073f1979b8b2", "4f2f6d488379aa84e0a81f480601c877b3c0a0a0f5a60e22023c4cfb531e9abb", "4f33261ae35820f616b9464eac97a037c5bfd7b8a1315cfc6b9d10eb3b467ae4", "4f3a64416da527247638558213afbf49dc2451a2971d2d443bd1ee93ab4c3573",
    "4f57211c60dc93021feba0461ed81f7d02c142b14ff07f7e60c3aa307f0572d9", "4f8e5b85f2bd2806e1fb747a268d0a6bcbfa71cdf6641c819dc3201cbc868746", "4fa5a2a689f0867764119664627b154331c5b778849b64f02c1bbb6f00d9f485", "4faa87a817cd1855650cacf4573aa9be51a6b3af2cda082f06d9476875468967",
    "4fba023a8bc2789b10e207c64c488bfa54e9bdb2e79e02e20c411e6aff6bb248", "4fc2f35a9752e5265411847905af92a33be08df72e809e005661ad145395c3df", "4fd93ae2405ea9705b3980402e88ac37fe430f727e25c288d453999b42355e61", "4ff07f68e79b32ef016d051cf0974be003a7849f24eb08f779e26f374dcd0e72",
    "4ff457fe4db072f6cd91d2c01b2aab4b0d7c6c99e6497b86eae838bc0915f4a1", "5012a1c624b20b472e874786a967742e9ba84294843f88f36bc4be0fc5e2abbd", "50188898474be2bc8ff3e844be21d38bddd75b41e6d99d0666ad99021d5d3f70", "5061e24add443dae5d9b93db2ba85617111fda25b9e12482dd60e8897fb1d70a",
    "508d96d7c88b51fd3aef460dee3f594cfff499bfe56a71576ffae8b01a775447", "50e3e6ae733fabb665d1bcddc7f439fed3990e6a154e683ed840b9dad29324ff", "50ea72d4e53bc0bf8ce000089b9c05048db0e2a6c438c5007396561459224d6c", "50f13920fa776e878e4e2fdaf67e2f4ce24612b0f043206a8e73df3e82e5b2e1",
    "5144cdbc93cbfa4f665590ed93ee27f7d3a7ebadc64c686a35b2b1cf3ce11b99", "518e2305cb1f4e916f214a4726d265a97474eb0f45205924776f4a622a742ed6", "519589a7c264a7008c1c59d4ef245bebf376cedf9e1b0f5cd68598e2135a2185", "5198cbce090d783fefb052a51597b13314a974fed90fa6328a4ab350038e81b0",
    "51a040389b5e1c436ee83bd72ad4da8ec588feaa99f4115c8a82f08f5370af32", "51aa8a7e5d8a80ea092afd9e3e49165bdb912cb5f6456d760121f5e0b7346e61", "51deaa85b3e68422bc012e683237f7ab9a8162a54bcebc57f59d4f56faf05693", "51eb6925b9d9d23849f2c0430aa509b7cf720e908ec6e04352ee40a5e19bdf88",
    "520105fcef341ffccd9f385016e39c7e6eb675e7ca3bec96b87d62a7b538a348", "52064e15f467eeb5ae4a2256692cb1ffd43cf753ea32c5605d2783a819bc36e7", "523f9339d2d67e20637833727e95ddbe54fd868be98ff48675b3496f6a924b29", "52509cc74d338ce079328f0af4a6e7b3ba5dc881f19a0b5ac52b88e85db64317",
    "52586d36fa172f4d934bcd5849ca801350bd817b272164a5fc926aefa64bc910", "5262cac16ff33ac05834430970b03645ccea84be117104c1dd8dd04d0eb77843", "5297ed6ad3fada281724d5c7ec25fae7626aacdda859e9b3d0fecf8fee04b334", "529b16824826718765d169eac91156e6037626f45c20af60c7fec2e5e41fb421",
    "52b47d27f333c43a8949a7325aa6e2bdcfb232769145fb41c830edb6b35a99b0", "52c2779aaaeb2f7e54e647ada4f83157b4bdcf3698eeb332857c15be3d8690a6", "52e3df23ec3ead219c1cfdf2ee0edb0c28909a9cbf4534d7354d2c04d99e48f0", "53092294c0d48c3f9a43f04a22309eb5f9fd6f2ee682b01dba49bdb38ae03724",
    "530be802b350a07d06c0a12f2eae58865b26d8371c625cf3c168288842d24b8c", "531363243071cab7559b2e0ae73fc210ec999089d5334e6bdb4a6cdca42a0bfa", "53139934322c70d9a52fe27e9925f49fc56befc674d936d7ae1a7717be0fd5ca", "5352f36c28e6663feea3e15120eb376110eb4ac855802902f0b1e8bed1f78f47",
    "539a647f2b65152a5371d72e5fa6fa8d8b1aa584cae9ffa4b8130e1e3e133490", "53c6f595fe5810c46a04a15fac24ad7a5c1588a61c69250940ce14a96dc8388e", "53c72d5a2195a2f3eaec327331fbaa479fbabbda74a619d1c9aa7a394943a6f6", "53c7bc8d84db4d72a3286d267ef727e2ef803184b72e75ab68e400e912b258eb",
    "53eb0cc905b7e82fe10c026d1c2236a50afa6c177d4efc3763e0ff7fe7f76ce9", "53f80ffc5f4d942038188efb6e2f049010bed9dff7fd4ba87cf6601ca5613961", "5428cf459cc269bfea8bff02e7e967efdb1f1c00b68cd52e6e415b3646fd282f", "54723c837123db1657e0e7f389719006504d9802644c2676d0a07bc43b7cea7c",
    "54851467d4ff7b5388087ccae800c68aa13ca54774696bf2f614ef5a388a9c34", "54a9c8884bbc6c1a1884d4f1ea1c941535f983cb591fbd962d297a592636fff2", "54cfc934e7b18177145e49fcb5cd09f531aa0a28808132c85d5a1ea85be7e16a", "54e4f8376c6150a99d1bf098c65fbeca1e5253b6bcf7fd249b8858eab26d895b",
    "54fa874dbdda91c366ed61d5b88e69ad25fbd70b17a81abd6e0f9257fc93361a", "5510f2aa80a2cf806fb24c3b0209e7aca63ecd9c30653241b5766d0d0fa539e1", "5516914d2bc97e4210c99cfcb3d1a1a64bc7d34b9a2478a6fcc6e0d04c4f9b6c", "554a47e081e42b64b8848ec9054708cf92d4dcd254d64e31e2106b3b5a3fec7d",
    "556231eb5441a1ad7859f56aadb1b71591581ed1418107b644af5d99c27b9715", "5566ecd103953d8c5ee04eab75c8a8b46706140b820ab9875f75b6582f531dbf", "5579ea1509cd97ce3fbd68f7c9368e89ad6414f93e809f757ffd095672c4ccd6", "55ad64d675ff371fe2552150b0b6d43967c61d6eb9fb48f7d13fc04b522bb0a1",
    "55b94d7ce57cd48061468c113b731ffe85280e48a6ba2b55cd2a144a98face53", "55bcb951c864704bc5370a00e0fbafb38c3c27e21b98a41c78be583d7beef1d3", "55dece5cc51b2846f3de6a676cd89313199d6824c09c172fe8205772d86a2010", "55eff26ebb949e56ef5456c8c46fef3d7211b3ece3cfa98c10de408bca685702",
    "564ff4861e9b342040cca63a00911fbd3eff6226666de9f5d4ff96002805b01f", "568c3ffd4f0a2c1328fa82965bfcdc8891e200b25539f6af9591e3ebd92bc40f", "5692d01c8ba7aa3f1f95927e72a052a857a462e38363327d2e835647be87157b", "56cc90a787970b4aa8d5b36a87346979cb424e2340847ebf9e5eac7101e67179",
    "5716d12643ac8daf2386bd99b4eab67e7cee2712b0dbf429dabffec3184caabb", "571cc406e91ea3e9ad633b41671c9ab1d480f75b814f3ae978009d5e847d2cdd", "573649cc405df64f4cec334ecc71fff16a96ca6cfd1a7d58d2894e1de474bba7", "573abfca4014c11442f8f6ac5f69fb32c4e95a637cb93ec7044176986279aa89",
    "5774ba19a7cf2b8f25534a6779df7eaa07b2259aec5773f155ecc574412d8720", "57db0dbd1037b6b8567d50e4c2f023ea9a2eb2a468b55ac5383a100c0e9f11d7", "581e5f99806b71d3c6276c2308eb5fd7ea4477dd71605ed5508644ab50d4feb7",
    "58443c6a3799acea0e4e8c8ec1c7202a34133fbe387e27ae1041755885eeee45", "58f87b0796b691b1429deae616793314cf4d4efa051cdc64b7da70b900cfd577", "591ad015ae77b55b5ada0b7ec7a204feb72c7067a724e38cae16146e3b58933b", "59349eb91128586c3377e03def764dfa190b681b8aa264e5849eb801f857a78e",
    "59887580163de6a7f1eaf25bb7105efc83e01488653e406c0e60b5262e9e59ea", "59bd900b9ac73a81c65a7ff9ed6a868e342f7a9153ea4376b5c70a44807cfa5e", "59c8af112c708884388db74cfaeeae176cef57e7ac30ab2ba44590d2b0908e95", "5a0d162185101fee5fec714969308fa190b507a356f4bd98b626d6759cac75e7",
    "5a2d34b25f2f0e831b53ee84a5f8abc0b4ccb476422e6ba1d44a3cc50697fb0e", "5a31158e7d9c9c7417a583246cab56327aa8d53234f74612fbefd0b07f2c9443", "5a428aea598a3b4006aebd2f4bcb848c41f3bcdc1d81fb324b933d93b7660468", "5a50a2f01622c84ff191d47c3e7f6f29640d682d0c12d2eda334f2261935edfa",
    "5a5504384082f7b564886f33c66c0d847af32768d335321202e0755c08dd0d95", "5a96bd4e2fda817a0152dec4bdef120ed3080c81aefccf7e19d85fe586090d1a", "5adedb2090386674674961f06d248a684bfcef92b5ad452c02fca67d1b27925d", "5ae7e2f2915a5abd784d26c5748382f988d85db67ad4bcb155ca1f78c02b5a9e",
    "5b1b94372420ca3f106abdf236575316a7554be48ff2db803df222710eb5029c", "5b21606c4bd1185440fca8dafe8b5d97525322ee980d1a7216b066595e1ea0aa", "5b36c17c35ae27b03765c58e9dc3e5b432b3f1ff6dd656cee369a25c299ed665", "5b539f859e1db5a61bbab0773e177f545bed039bf1b9a1d450fd584717d8160c",
    "5b80007eafd19b0ca39b6f0cf7c40c568bd0ed1b29abd2394f4ba0ea179e68ae", "5b9d38d954418e089be7f9aa3bd2e00a71ede2981780e5a11b3c9bc83ccff31d", "5bfbc3f0caabf019e9fbf9c6870f5d96ea1d2ba1d10346b809f019a7dfef5e12", "5c0d8be74834effbf79ab6857cc7142a055d0b3efb485053e6c430b529e6c5f6",
    "5c173672e2595feb819b2f2936a952309f27455c70495c0385df6ac2a6050303", "5ca9a45eec999a91103e38e2a48ff8d079d31016ff30c8d91b3d27b62b4822f0", "5cbc3ef7349626c193cf27743263c8165b7a0af0fbea4baf02c7868e89bb95b0", "5cc28515d8bfb1821770f2e52a0e421db33379c0022cdce74bbe477435aedf2a",
    "5d1503c60556fe993e9028990e1aea62d3222b06259dd2cb587d19c571da8231", "5d2ef84cb705ecbc19e43597480479e1013c5689d6c1253385f78752839d8a58", "5d30234b23fcf7b5ea9dd74bc5eb9de5020babe5e14f92731eab8aab2a4f9984", "5d7ee07ae7f447a911bfd1d7521583eae418348311ae840a0bde2f00fcf93b03",
    "5d98aa85cbd7eca4707723a040e0b599b3f51776a53e5d4cffc42943eb134fb3", "5de797f46a6509192164a99546a52f7d2f2d3505c347add091c4f6d32dab5485", "5df3510c1b12b628dda416e64a88faf54c4854bb4627dcc094010afd9fdade9c", "5e177175a46c5af4183027f4e9cd17fb8155821286e526fb62e43faee6a38f65",
    "5e1d38a088601b0dfeadeacb17b04a2c1412720437a1f2ffb3eb623c1483632e", "5e4c4f8e98a9ecd8110ffe73e77fd931570f8d7deda27850b29f103319cadc37", "5e64a08e9838112087385de0725660da1fef4e96859e2f84bd77a4c9213b5970", "5e96d9d08165d25408859725c4cc06663eede5b4a71aba02774cbb52b920e589",
    "5ed90931775faf35e20f9123e3146f94d050644f3ed6252872a8271ddcc0ebd2", "5f2490931801afc838b354e05063dd3e32af1bb823491909e0a68c871052518c", "5f3ea656d1b0178ebfd82b690cc085bcd2087b7facdf361a0187169824ccb6c0",
    "5f492949bcc0ca4c7cfdb538378eb10cbb7e2b1182500b7572266851ca4a4c32", "5f82bbb0c0b127c07136e3f87f398fb8be5bebeb94ab272610903a2327187907", "5f9cfbfe290faae6e7fead370fb8d794e9ab535eb300aaf5f3b1a265138d5e05", "5f9dea26ae0f898c966db2fffe9aad2e54c725dd49b2813c3573abd658b111fc",
    "5f9e09f5de2c66245326bcd3c9df9965ffc4dad5c576a303b9e1731d336499d3", "5f9fa1a03f6f82e7f2cbc154eee4723b7e491174da1042216729af95ab379b7e", "5fa8979a0ca416eb3104bf1dbab14fbeb9e2c4630afce943067ab9cf4936acb2", "5fd7001c0185f82de3a7b63927a003cd891b4a969a73cbf5e6074119e45416b8",
    "5fe1469a9463a6fc5ade2554fc1b4a0369ac7dd1cc51e408497069aef04affd7", "5ffcb4992e49b3adb8026a98d3846034d86c2d1e71876732dabf86222d0fa11e", "6011f102aa2e73941ffbbf2aba9e5ad93457c08b99fe8f151e2d538cd0c0527c", "60502ca982eb49eb8ddd8979119c49f5aff36961741b6d4db01d3f8c4095a05c",
    "6084618d19fdb7c3bf45a2597063bf6e2834a6bda781c928a8da9e5110fdacd1", "60b5e4dc67d31d69be9e98e4ee3f57553b9b15e11a05023444a47aecff975eb1", "60b60d1f4e4a638263637da89d41af25360b597cf5622d3f8810ae9fbac556c5", "60c08e474a6d9764438043fd3c45297e16e2e066ab9fb6d81fb7d861f1cd7dc1",
    "6175d3dc561486c60c3f69e760c8c5132b84b81ce697ce06ea57f44b312747ac", "618be3a5034c70432a75c074139d5d06441a09222ce796c506365bf556ae3308", "619a0ff0fb77601a37621378d62cf5d61f5e966a64a0b9c7b5ba65e3288e4ed8", "61ab43d22ba18767642fbf0eb07eb7ccc883b34cb54b25c8be88da4115653b10",
    "61aba96df36a7d74880c9c803e8521730075340c65669142013ba0d67754d98d", "61d13de9afbc666989e76ea4a2ad3117a81a2311b0e4c95fbe610f94d5e6eb79", "61d59d71ae6768ae9b46861428da72539e5e9da090a714dc6c40e051e7b9dc65", "6218f6cd0498d20a15d2d81337c34bc32b9b320903596b504ac95372af56453e",
    "622c7d54c8108845f36966183fdecfd75e7155ef1516f23447ba06bdc85a2dcf", "623556b2950df174b2b21fe1cce6339b2114f692ffbfa9861fc74ffc1fa322a6", "627d5383081d8e936dc1200381ea4efe518fe2b3f7e9393efcbf7ab6c52c518e", "62d6427cd244d218795967ee9e7b7c0a7b7248e91762436a3b4ec01d19d7e530",
    "62dbfd5d4aab46504e7ac341bbe2652b886079b4f92ea8e474d8472544d33000", "62e478d8f2eaa62e7e0916849a7a7c47e3172ffce2f6a6ce3e59bdd332f9b1f7", "62ff44035101f58e1bbb0e3d741b30ddc2d243102c760139c72094ebf97cbf76", "6323f3c3197e629e2cbd7bd82869b3b3a33f8231c544113b2f7719c1b0c014a1",
    "639e5e79e2c15bcf3237e16df3722c798585ba601c7766b1613a42403d4e4b32", "63e02fad2800f573a1fa226c3048834a87366bbf21e29f77810770b441b187ca", "6417914199e7acea271a89c278a66c1d1d093882cd458fd762bfeb7594b062ee", "6436617a2776c4049ba2a6752a1947c5f774109392d22513ceb8f42035d2457f",
    "64603fb7c7bad2f93f1426508c17f9e3457e71ec07dc300cacab126d6552efbe", "647467540a8105c32b8f39bb8619bd6707215ad7658d8789a3b11e51060ebb7b", "6480990a3e6b3f6a971a618bbc5a73612a422636899510b973ea18815e3e75a4", "64d2269d4369fae84217213837c6621c2240c747e13186e4100620c9c24c3832",
    "64f504ef314fb982e90eadfd81046d5014bb5882cb4eca6e69fe507110329cab", "64f76438bbf3552c8b6e15ea6807527606ec276e65b4eb488f205d848579f735", "64fa6a37896a351d1bfbc1b3752a4ebba58fb38e874973015359c1125a6dc5ff", "65455dc6ec2c5a1549e0688d8d1fd3c74e63e75ef3cadae846bf83a53c9bb4a7",
    "655c8150302dde728cae3fd5be4f6a83e6597827ce351bd583919709d7497026", "6591bfaf559d584ed47fd44a54b3284f666d64895ed4998c7b1f5e834c12b8af", "65e06b509ae4c91066d3299a37d39187c5a2fa57c64824eca6ff4543db5af6c5", "65e69ef9a35dc3d55db1edc85271c7467908d67834e8c9ca8eeadd2e42d4a84d",
    "661bd75e8b0fdc4ed115a02879a7005bb3874fadbdafcd471440e40d8742a346", "6638dc8fd9a115652026449743fa76c8fc1793404bfdafe6d2bcb0f465efa273", "66432c0482a2fab58c0a2021276a396b6a7ab9c813d84604935ec9c3d9128904", "6645ca35c744c187239f5a90e3d4b726d5d6f61c220ae23c45bd66f258526263",
    "6650acb979f2986e0c1c0b90e7e77b6e0d8b3238ae7387c064470158c58fce29", "667da172a8bee5dc3275d9c3e026b8945d2a2caaabe9ec1ed102405d37d9fbdf", "667da88980b958c8ef6094b45712da358857031bf26fb65f572f2a13ed281e76", "66c9d39bf491afe18b4c7977b355135b69489ac84526c607ef45719f4cd98568",
    "67010ac45ee0264885dec79fc283c0aa05d3ec7a6e4c6b1ad72bd8690fa47182", "67104d6d2a9039a9cb0dfdd74884d1d0bab57bd3f988fa98967277bc73b42f7f", "6738d679756a9744658c88229acdd7e3c65eae3bde06a41c4377975421148af9", "67758c65d723d6c14b349a25e4a9ebcc898711bde7902d64d9a62a9e29f099f2",
    "6783aa3f2cc8357b9e584090c09ec1d3886044584c801fbe822993b4666084bb", "67856de89e0dae89a99199588cd1ce2ca715a7d3f4a4798fd6fe5134b4df094c", "67879584cd2e3251a55bd11170579c9c77c563e0a5a8647b2a8eec658fd77012", "679349bde6712634ac0c3e55e8d7da5e9bde51fccd3d6d409e9a4d81af0489b5",
    "679718d68113be1090e7bce25949491f43e4d18bd068cd0e5e532ad9e966d411", "679f9ebec694fd3074e3dc07de94761ade13d1e510feba714ac4127d2099d230", "67b7c10c84ab50d45d24fe386a4e243c5b05c971f0896f7c2c9f9484c74efe11", "67d568f2f55295c3c7e6714e3c7f9b1e85eee625945fa3dce641a06668b5d4cb",
    "67d8bd675c75076571ef7620473ea91cfcdc51af7d1c171ef677035e1d049706", "67f3047ea54864738ce0067ab42124cfbc34d3d8c477a7ed397d26a140403033", "6825a179be746a81f1cfeefeac69bc8385b220896a061eebfc640e10a81b040e", "68274bd1f8768be8f859641fd5a30a605a16bc473a31eed0e06abe4f063017b6",
    "687ac812daf27f04dfc9cb45e75b3e03c503a1bffcd4328d8d2dee72ad896b5d", "6880c71049c69a96954616ee6e906292400ff3cc4e963e0a2d851df8bc2a96a6", "688e35f0e526c99084fc6e6a815fceed1dbfec0855d8d656f5d823ec8d8fd562", "6895c35918106cad0c9d9756cba82cca23f12ec46a89bf1b69d3e2ffb5ad6645",
    "68a6fdc920b0dfe1aebc346f44ba999701c29dbab9a6164ae68b311c24bc177b", "68c75eae94042d9802527a3a03a9cc342358ad881c4a2ea71f4d35a9888cef65", "68fdddb30ed6bd50d1da2b670c33fa6180d9bcdcb5445194256364ab96189ae7", "69305a3601530589d0692f3eca123df91f7ad84107cfd5808be72f54f3887d18",
    "693a7db03ee80ca6f6e39aa3b4aafdbf6930f74bc5d0b219a12e56f2c4427933", "694224bebbe07efbc6f42f9365b8b02765a25ada5dbfba10782116a960f893d4", "698db2e2b79237f0b2edb5f493d32546d241d773a405a8e33e18c0e186b09a47", "69f13d1f09ea141dc4f4766758429cb5e6c8ac8257238bcc43b104d5d9a42286",
    "69fc552fb33012decf438426867bc0f5b3eb5aead80b9b2f1a95a40cf574aec2", "69fd21a983b014b66ebad322dfef6bc0ce3867fb9f114b8d6f0faa161592a99c", "69fff3b2066332c6de0107aae1a5a7b58972d4a750e8580ea9593d19473618de", "6a20efdfa5fabb89cf3ddea81a0626379172023856040c0e895e0959cb34a873",
    "6a7d120a352333b19acda2b68900b06e2e30ccd692bb78f3a8bea1972025fc6d", "6a8280dc1073d51a55f711f45de4159e6f986f0e9f118db1dc601f5a9c2a7bed", "6ac524fd7b1e28efc2571ee30b7af3d7fce894c510a56556e9925ec36f5603c2", "6ac76955c817191935dd99741bed6ee31810202dc535728fb982b03029909013",
    "6acad6eaf9cd70ebcbf32cc6dfc2267c6d1706911b3f59e55f81ccc166832300", "6ad660a59945805f0141f666cdc03c71c5bf1a15fb8f610a22ae824ffa4cdc47", "6aea996488b55274c658aa3781648df5e4947d9b0eaa33c4356d8f49ad12a366", "6af3909e7dcb5dd05f6fbd1ee6465af0d6eb1246dfebce0553ce447af5b70ff5",
    "6b0169591950f94a2b84b40d6e0f7e448bf9f95a38b4770f946443abe8205dce", "6b1bbb66f097d4d0e406396aa7531af220d98a162823ee8847f826a6e53836f7", "6b1f613314470571ed8aacb28d38806c746b662421a6dba0da0e20ac59f5619a", "6b241738a1c403d1acca6a357ebbb6de96ada1195623da984fdf2c62cddfce1b",
    "6b63a78e9a1a4b876ac522c25f62591acce93520f6202e7918f3801c465e613a", "6b8abb96597e517da8237e591a0b2d0c87ba7acdd4932808a1f55146cee1c5ba", "6bd157df19cfa921efc94f42767ba999bf678a6b65f6f8c383a8c7933907b301", "6bd2feff414f9cf7ff6d8c509461c6358419c00cf43e47202f051b7c35a22ff3",
    "6be617fab022617264beaa725ab50b35a9ea65c23c2ad39ebccf9b4567c518e0", "6c0e34ea7b26d998275280a3784ab4eae649b4f6a9c5c5ecf668bda1e8c438aa", "6c11b893f34854258b01f7e059d8a3b7b484e65e0b3692f0b1cdf807bc2b9ca2", "6c1fd30f866e98e746a70b83ffb5e5ee5361619d779552f0b4e54dc24eba4a19",
    "6c3ae4aa23b1416a1bbc65d2df687ca3e008b903b203d6ee8eed797a9fa5b836", "6c3dc6bbe3994efda0058493aa79b98ec562af17b32fa0aa5d77b3f8a2aaf2f6", "6c7ab2de69d40f7f28ff08a22d8879de2db770168dac6af4df65f8c6b0525baf", "6c7ac9bf3e784192401f10b46dfae3aee257e9102b877048c09d53a566f29dad",
    "6d05ee2e988f2e54f1fc12c8e495e492cb3e15facf6342668409745ace92020f", "6d0b950c8f37c111c0223003353a4ff0b625a41a2e18a173ce31fb103608fb1f", "6d2872d8c837a8e7cc3e0f4620438f1dc8182525c2e8573faa0758473ae256ba", "6d2a6ee5068cb1b19ad63c8c3a7d283788304c1af53d0cc3ffe2e87a19e8a913",
    "6d307b90f8372811923afa217cda6176dec19dfb05d58e3a2e6248cb24d691c0", "6d678d170b3308b623cc9e1875d35633045406b51c350253b227e050ac01ae1e", "6d7d7013db02e5f8a67fcc9e325db42f37905151815d5690c8a09c363f1a244a", "6d8c6b3f6f2c92cff8496444556a7126e258dbcbab89e85d35912c6f60b19d5f",
    "6d93dd90ac4fd1aac8f0d3ce9a121d86d008449944d34a245ad037dc4b7862c7", "6da8b8f11e6bb4d884e1364173d9cb83031cdb92405a6d4c35d95d34330402e0", "6dfe884afd73df0b2f2e231259ddc9eb07500920d6de1c1b64139ebe05812020", "6e4bc9988d4630d06a94658e5aa23b9687e0d75acf8c78c06fec5cea70bd13d3",
    "6e6e831b8ec7b209f9b2c41d0953a74da4613698b41eec1615fa1648355804da", "6e7080b7fb962af1e7c31f18d99da344d2ecf4fea972e89f408e34c969425db4", "6e7c9df242f8f7ef3080a5bf984fb19510300ea239960db3c36c561d43cb3711", "6ef2f66a3ceb00df13f33a5621cfdb95cdea638fedbd2d2ba1e429c4fe03e443",
    "6efaba271a98de1849b3f1427a9ce64888c999776c1bc6e9dc785e2f0d71f654", "6f00f75af56cf99616ee4ecf857f1e5b272bf44e75cc6f94714ec69711fa2452", "6f0fb3f96f3dfc02254b3ed86bbf92ec0c3c8890b5e47cc19682dedbe3cf3e30", "6f4198dc2c0f0ebbc59df78106a65e846351d895c05501f029f3864b59151a5d",
    "6f42b1f5726ecbaae3eb388fd9797b3bba42e57c49e87e4d9a1100592cde55f9", "6f46ef1c47f755125833c7aa03a63bc9826df9f84e7bc40c8bd3dd1157bdf258", "6f54875b3b29a4a8d42455561fd6a0d92ffec4574da2c80bcd0bdf7af8993d85", "6f63704a6e6d0f4e279bdeb7d1eba7bf3e1d4f701cd7e990fb42156636f1e09a",
    "6f6a109f40e17bc35e93d95b16540630908ec1405891418a96ef8de4782421f3", "6f7dc50370c23ceb3cef6f9b384597299c9bf3a764d08dcaa6e9315d599f31ec", "6f84f7e807f05f930d9ac9b5d9cf9b3cec72efb32c4b1f5b01b5370ac5e38941", "6f9a9eab0928b0ae2411525ac0692b05ac7ffdff194d7b42f005aff4ebcb0c29",
    "6fa524eb878259bf437be8ffcc371a5a591160b370d467e561604c8e70c43332", "6fa8bae1f748c3d12674629480f40b23213369ac831223908f01aa6690472d90", "6fb7c2adbbf54b9db1699d3a03b48c57be4c220494cad1d0513cf9598e16554e", "6fd82574ba903f10a3ff01614c26ddfbb84d11e29a7b70b641a4c602000e66ee",
    "6fe87c30361cec1f9808995ad49db98e72f2f3491a2806d9218573f547e0e30c", "701018ee2c5eae29688604dd076e2291f4629ec65d1b1454597d2b82d14dbbe6", "703ee529210b56db672286ea3d454be88c8b57f2da82527668c9da87c61d457d", "707f51178facf2a16fd32a2d3a3890e630d49f20c9dca0acd90795d42bea2a39",
    "709fecc822e085b3c3c23633d477bccc1bb3cbda0200d868f3e44feef4562473", "70b6690fb39790875669575f3be9165df38832bc21284869a026e4acdf16674b", "70f381728214bef809b467a5c354a4c02ab0023cfd23a6758655b55de11d1047", "71021f151ac30d5d54e9e745c908c4b2ebfc842cafb8edde2ce89e96e72e8960",
    "711122363176e8cee0f6155cddeb5d0dff26556ce0a25bce9e1653f87c42ac0f", "711d1cc3dd59180c92a24caf13ccba395eb249502b9d44dd82c18ecd999d0c60", "71399d85d675d5134efa7c3919b0d125f033b7356201420e0c86e1752ad6025c", "7172157e7964fdfd1e745159a10b884f82d8853d7f27302279096d4afd50e599",
    "71773fce2c83a49a399372e17103b92c6320d9fc651ec5136f1b52f66fcdc065", "718349e333edce98405d600d8103a99717ce64bad415791d8f083f404c5feb0d", "71954d324983589b4f1946626dccd192717aad36a303a9cfa952a34536c7bddc", "71ba11f62c21b7c3e2991976f17fed70798e6458bbe4948745de95862ab63bf0",
    "71c269463925f806bb576ecaadfa4f30f1fffc1182cab7f3317b6cdc27324729", "71c7954e652a90f7d5d213ee48cbaae4f934658badce07cb3ba06c57d2fed3d7", "71d0aa8f8374b213d52c321f985d9937fa195f9cebcc41638d6c3fd999fd781b", "720bb8bf47175f642fa4e9e836a9955daa873d7deb5c8057a6314e49abee4d0e",
    "72405a26c3401f68283af0955f503c2a3d60333d112ff3b05d9b97b5dce06aeb", "724d50ec1eacd76d637081eb83840914fceb19e44a71e230064dea1190856a2d", "7266180b82ae0b18c81567877ea505fc8181a5c4ade2972cb9297771e7504928", "728cbd6bdb44c6265ca7bb6ed03fdfa6e45693cfcd5283c6d97a9e42598e0ea4",
    "7295fbc00cf178b61f7be6b5957b5b6b876038debf17f8404ae3c0840f61a448", "72b3d88e5d57c85a69ef80cddb67698bd564aad7cfaf4db404fb74faaf4c01c8", "72bd5eae0a3738fa86ae80b01d45db4ec61cb1c214f24c13dc6ba53db48459a5", "72d5c1f543d9310b5aaeac10b3c67d92f1c6be14561c76c1c7c63dae802353bd",
    "72d67015de1d67488cbac369ba9b6e87b17c8112b764897017451fa6f6a7c496", "732615b3faa1494645e66fdf0c9e453ca4832d962a736fe39f576529f401e9eb", "73403850b882e5935d2715d20da31d2643be8e3eb6d2054753299094e9a1b6b2", "73534f913da887daf3c15c95a6e0a0e6df2cc528580b46b410abd04eb56d6513",
    "7365cc974b995c68510c7b63e7b000ac57b0670bc1c9f3681776208610827779", "7372301af6f8e61e1cf2886566dabb892a49df133831e4513026fb94f414ebea", "737c0e6dc8c54b0c939cd97cef4525b151cbfd45bcebc7d86f8aa646eba674a0", "73a6dadd07f429b5663ca90bec329f9fdaa37a03c4baad03cd55a9201aab6a0b",
    "73b1f7f29a2014b29a6a9cbf43f58711d045abf257947fab4143533baf30642f", "73c0c7c20ca5de42485e9d1d617882698fb25759ac8e2fbc9a40152b1867a013", "73ec384b992758d4b140393026976e642d4f67b1ccb61064bba8a515364c8972", "741459af3149c098c3c662992f950c98c609510565097803a4e0adee6498d7a1",
    "741eb2181f7ab57fbb69053ebf3f0123741f33c13544fea2490fecfdf6cc9055", "7463087f4682a4452e8bbe7d6c5db9e08b7bcf5eb6d6ba9d20561ee1f325745e", "74965b1dbd6e8d068ea00d02717b9b358c94df77c20f9682eeafa5a8155646d0", "749fa0b206403777672e3cd6504202980729c21419efc80cb157475bec5b056a",
    "74b7966512350087664b3ee31cfa97875dfcc016e0aa2df4e4d431636527412e", "74d847ac1259e1f3e9eecc2226da5da5ac418e4e7eaa3cb4c58dc0c6c213f4e4", "75015522af6fb2caa2353de3bddab6acb331b30f1e541384ccfb9bf62dbe7935",
    "750b222bb1c0deb99e8b2fc5d433c41e67cfe3b495c3bec36c50258ed00d294a", "750e50a47848a06ad19541e3243e0081ef2a2eaa854b4c36eeb850d95ec21d2a", "7513a32fb8c3d60575580cfedb08c07e3b6f2cf62c21f4b406e499b378b81c8d", "751b532d28a017b72f789577ef1666c1f8416c625b08717d0caab8c5d71d8a1e",
    "75513e0a23a6fa28ce810feb27d20650912a2328008ed293db93e5d65abd119a", "755900e2e045701650aec965c84c584be5c4791070f393bd79a58568e57a6d0a", "75a2478003903fbfbb9853c1a62a88b39f831854681580558054b4124349e9ff", "7614bd8579af28810d48c072996127402085a98f387d8cdebbcb9e91985d5449",
    "76700b1766c0ddff566ca310fc2573e165fb4c8e88cd0f7e3c9a8bd90a4f2ca5", "767eecff05575f87f941edca07b7adf4f5e3d4d14d519d5477c664467c7ba0b4", "77027748f7d683e572b595863d7d864b3346a6b12c1350cc786c740cf4deb19c", "7727e2057b7e95413f485eeee7952cac8d1ccb2eaf930df4d2ac930ef83a0fe6",
    "773cd2bcc98f310eb739c3b1c852f40921b3f6cd41bcf16d9f6f1c0997673d3d", "7740830c6c84b76496285d23a6a7132dcf309827c2464261c458fdc5939bd513", "774ca41771cfd07dbacda6aff2d6fc331c3611d4fd0423b53eb16158c6f2e685", "7754366272d2aaae8ced46e093e5331301ad686213fb1a17f38ae94ae5d480d3",
    "776cd2aff744e33620a67b73a498cb7fc51df26f9b26ddc0ab2a31fc490cd300", "7773893e46a0c996099ca38cbbfab50f22dd495c6c99af622add27ebe79a6434", "7776f0e56e036d3730da45d2c7d04376cb1628253b1aead65756478525fea551", "7794aec96fa88df43beeb98a2ccd35072e632ff5ebc85cd0094c011338c9e46c",
    "77cfa5c23672598b6edee42c93dedfb999778f349dd1ffa0801d5934308e974a", "77e7af88d6f4adc9f41cb50ea757b4f999e5f39e7eadfdafd1447421708414db", "77edb4271260eb63568867be1821323f8a12dd17c284c190e2048519179f23ee", "783c52410fe4932a6c0ec6284c13848148cd6add03ba1f0045e2cbe3dc1947a6",
    "784ed0a82c8d90d7ffa72d081be057b044375b6eb571a0a27ddf5d93edd50c93", "7853fe4af35319a3ddb161c57db6dc317b5e1d5d57fe04408e07887d9c9cf164", "786cb7d24e148d7c58e4e7930702c095fad293cf648991ff889bd9566ecc74b0", "786da67cd7909703c5ca4e8a623e9509e11b5b2a8f839a6717e9fb37bbe1460a",
    "7892d6ff5af93ef0bdc17079ee14c78168580d10a6e64ea3f647ad434bcf162f", "78971a06020b2ed7d0483f971fb2d4b51f1c9d5938eadc065f542ed49a8f1213", "789d55bdeaf1900699ffad59356c7d46bb21de2e2cf6e35228c5cef9af4cdc6f", "78a5176e509f1cf4af2464b09b9848342297ef3ba7e01eed5e4ec6a23533ace0",
    "78ab657dfaf5b1156914e8066301f0f11d28bcfe04dc26b9ec533fb8d877e102", "78acab3957dd8bf741047cc98b601ba5f4c94f92bd7c03faa5cbdc655a737dca", "78c44636b5c763c427e06613fbf8b4fe23c2abab593671c1aaa8d6c4100863fd", "79788eed1d468140dc29ff73dca1ac3f35d84f93f83c9b38afe8ea9ef5939e0a",
    "797d60dcb279c8c91603c8cb5ff0f95ea96ad470e62aba75e470b1b3b17284a9", "797eb460ee27f6c074b328424109c24958aa13092695f737d7520749c051ba52", "7983a60dd38dc5cc35d2d7d97c5a93ff59ef2f24bccc27877bf4245811f98397", "7984a28a2cb4385fa47592852fb658646ad3ebf90c6e2f6a7e1f59dc7a39d01d",
    "79d6250ed134ac5059ec31bc517e9e559f77322d81843c703c9ab8236092b316", "7a0d1e85c1994d902e3d79862d77d3e91716e2d3a6e60bb89aa47da43b5170a8", "7a2d3ad2698b944bdb572bd5e54f484a3ca386c2713c50673ae6051693e586b9", "7a583bc4c983d2959cc737b565c531186e767d84203b4f300da1713b83f384ef",
    "7a8377f73f73c13e9b87cc67dce307cdae78923ff268f05d2a3a209307cf7bdd", "7a8ed9f28d68f3039cd40d4fae26176f72722bcc50a1d2cfaf21cbe4ab066553", "7a97955be27aec6dab4092b770db299dd08f110a671e91f6a81a8187c492acd7", "7aae7fb787d6413ca16a07752a350ae8999927de2f95fdd524284394e43d02bf",
    "7acbf9c38d11cadf4adab848495ec23b0dc451a632e5d54bdda75808377a68a5", "7ad1493b67ecb45c61d90772137f53e2fcd5ccc3147a53d64a816da072ead06f", "7af0e64849992b55ddace6a69d06602ae923bfd27d940b789f7612c16ae85d60", "7b35472d96290ac346a0e73948868b4e268539ba113c43fce12425d799a92789",
    "7b4516a455d058252ce5aedf8c083b7b2fd845de39ac431e81aa118bacad77ea", "7b5fa6f0c20379c23a0c59523a5c6b92963d11ed64d50fb1e9998e2ad1e74819", "7b79b194545e2011d4c4bf00d701f3e593c279763298ffcef486126dca793a5d", "7ba3606b712cadd177904566704a0163373472699d503da5592a35116499ab16",
    "7bb96e496a18399e167c9599ef3133c6f16315b66979002e97309a4135f67f20", "7bc15b397492d7703dee19cf68f22567aaaaae2fdd1647dca983b2e47cc13818", "7bc37e8a6fd04a0bed6b35ae7167226f1a734a863ef4664f0a14fac7a9e0722c", "7bd696d08907848d0632be7231b4996d75c4a673e04c29023c5795b77667019d",
    "7bdeb185fbbe9d1618c08f95c430ff9c1f5605ba54a649573b5a6b2a38b55511", "7bf5e601f8f9577a9e7570c1a5e9bd5d5730e9f34f27561c8d02885453caa375", "7c247076512d7d93d17516b6b768115b1d74fb36eb93b4e5bd798028d2b58f03", "7c331adaebc8b424fb85e4fb156a4124ed0df7455688941af4f00f9e593c5dec",
    "7c4fc7467afd52c655df1bcd3fa073728272f45e001557dc3113f4cbf9402ccc", "7c7031b4b02802d1d2b758163edec0af9505afcfd81906a0028b4800c176a738", "7c8782b81dfdffff3bc194db168c50d482710e1062a4b7054e5fc4dffd7a94e0", "7c9efc835d67bfd61adc8b3cdea8c7911c9cc6467d4d3ac98c4dc9ab8b9ff217",
    "7ccf1f49df1cbadaf8cc5282ec3c493d630962173336b26d34007dded4d71c3a", "7cd082da200ce1cf026681e822d46a89efa7fa5100d7f8b618c5a44921e315dd", "7cd9630d5242c17e4fcfb6f274f5530efdabe65f57b7f21c2a49b8964688f334", "7cfe3dc0e114be839e9a23f700810a7984dfef9f377ab1f7b5e1b157b2654d8a",
    "7d55ab0c9715c0836962010399e9a2c4b8d8a8580025856e34e7a2df97870887", "7dacd2391dde6a14c42b59cc9159656236c93197fbc8355328fadc56b3937b69", "7de0f6dc515efd32e43e22ce1eb35f9e592726beee3f7b85e00aedca5465add0", "7e6da223cbc35da47da21997dc1a25de8ba17dc4bc5a1be17fa0214b170fe113",
    "7ea45959636bcd25eb8247cd65180d92e16e7251233bad72c9d14297de417ff9", "7ecacecb92aa0d74f6ca3bd9ecc2bb455e17af0e51839fa314d0fac2b281a3ec", "7f25cfdab5bc5a8b48a312a5f1ff3b39ca622230fbd5f15297807c6053e061f1", "7fbf2cc46280a60ca3543c7ce861e8c6b0233327e9adb2012688ebe2f218094c",
    "7fcdef8106807ec45ad93db1912a96915394caec4304b33e4a65a35c79431c1e", "801e5ff8c874451cb609ab4c639dd286da3422592d855531af57dfc644fba81b", "8023a64973c798fc17499068681156b502d4ccc11b49dd04f13e1eaa4c3eb74f", "803a807916504aee1a4a02c774f356bbc35f13408a4fdfd07b4260446333f578",
    "8070e7379c258baec3467cc8882b2f37f24c01f69c749f7f65cae6ee61a43d8a", "809dc5a5e121b1d5676a9df2c6cfa4830cec3b5a587f1d311bfd1354c71d9003", "80a7184b94bd408d0172e3a1b2ad0693909fea516d9452a51737d65ddcb1f1b7", "80bafa7580fa10d361c79ed0a11c29f85911b2c7866b8c67b8f49d6cc6fffd30",
    "80d2226e6c9c7b2fa64e389cfd438ec5b0b2344e546826cd65706731e4fef8e9", "80ee6c491c21362a787b2823cedc64978e196d07d705c932cb1fb1c6430ea1f9", "80f62b8a8cad2dd2c6872f7b8beb956f5f819ba020bfb05eff59774f18dc329c", "81361f1146358f03bb61fa937e8306a92b723e257c3e4a2bc069b0978d8736d5",
    "814178e1242aa7c86b2cb982234e50fd12ccc3f73f62394846fd7a817db32378", "81a287211bb3e31b4811e3fb5ef75843eb1a873511b210969bc260d9e0844598", "81cc1b177ec2c67a7a1bffbbafb1ba6a1e9276cc84f2e66a5a89378f90167924", "81d626e12fea691495f769833f5bc4a7117ab252349e399bfcf00f0f20b81ebb",
    "81ea300b103ba2d3e51159456bed394607d7d5c38e6419663807ccb23965d1e1", "81f5710115d0566cb58df44a04f6b270bdf2d5abd0e33812ec64e86f0f221b5e", "81f776ea4a0e2a96960e6e4ad348a08e5dab5973afa46cfbb777b64413555c53", "823a5778afe886b3d7ca65c7536575996a2175359660e2c153f2448418e5d9ef",
    "825698c80a18dc76551ff7efc854789054cd137130eb23b43ca22b0d195286df", "825f9ffde65eb674224f1df9b132369c44457e6de42fe96c925fdbf3de42c811", "82a1fc7a7acbc415978761f1a133681b22c5ec2f22f137ecb76e5f31d9d5e8ea", "82a498223c38567d6a75c47e5a719e1f2821f7895a83169c795a6619bf42afa2",
    "82f119f9d776c34e7e8d8d84243a85aafdf004d10e1b6b146c3ad48a9980cdfe", "82f7996d68ab16a4eba61e68ffb4b2bbb28dd04a537cf193be531e2f99347d77", "8333d483eb205d611773b24ca9412bc861a7634c80673c92f1cf27deafd5e7c0", "834ac5319defe4057a77b022c56cf376a906555cc6cd47025f4e5d780e3a6d6b",
    "83874cef21589279a408e33857ce55b066d790abdeccecb7cc49cd4ed81db72d", "83b9725b7a629fce9436ef817ca55fc144fb82ed7e596eb3d396caaf26d56572", "83e06f355e4298c50a9c5c628d7318d962e46331d4560155017a309e194d06ac", "83e7ea047b49161630d25d373870e4c514e6182c7ff0d4ac8fd08d090a3b13a4",
    "8431350ad6f3ff278b9ad29f001908a380d999b0b86d4af675c7d157bbcf9e21", "846372ffcd2d6776c3a492854bfe548b4dc006cbeccf81e8a9a4f287d8902482", "84946f2ccaa299f227890bacd3f63a747eb72a961825633cba05c00d004314c0", "84e57299a0d847035b1a05d6b8538bef3d0c0b935278c6eb9d0dbc0c8b3467a6",
    "8507c88f0fe1996311906cfda6d556ea2fd5fa0f3314b890d945443d2cb64a56", "850d9f44584f531b1264d44c04c9268df021eb4425f4a9579d09c9d0d29f3f49", "85143ccaab66a8d859efe4b815f37a15b84663c2c3223ab8a9face4774fa99da", "852aa39ba863d0939cec3cd6cf73dda116fc9ae84e3ae417ed4fd4be75d59df0",
    "8545b0a78cde86b3e856f6fbb350fddee0c07f0f247f5e26a2b7b3b0065c792a", "855726ca9efbdbd4f7a20c61051ed6dec5de7120f0e28e83c03a0eab5758f121", "855beba5e6ddf3955037cd675f705fa95d1d4c02a9963076ea35c734cd47ad27", "85612807d71e51bc796bfa41a88b6286e2b9a0597710bfb030f452f5c92f9039",
    "85703305ee6a97e68e3c9a9ce04e4b008aa8d28dfd68d183e41c4b8480692cbc", "8596ba04d615f6cb7a6ba7a7e211684765808e4c8eee3704688515d1c716faa1", "85a8a5889f512df7ba0603032b77137d5ad88c24481d6be529bd3505297cf17d", "85bb7a3506536a4637411ab11675338231df328710a1f8ae45ae4ac1ec5f8c49",
    "85c2b0eb7ad60690a65e00f39a2af98ce41ecf8de0dfa3cafb8f2843f2316c82", "85ee36573a9fd6598adffd12cd6e25303ce4f20e8b66b3ba2c3df6b7c66c0088", "85eec9e5e475056fe12e5036a3a6a22a18d9bba7783c266e3867f5ce057906e3", "85f533d832e183d6305196e846afb9d61396f840ddd81eef4be6cf863221d3d8",
    "85f7f5904c2b290c779811d5e17b69bcba5b65bb701641ff21b892cd4e404d63", "8616b244198f3189aac6beb90a2052ab0a82e7e7a47538ddbeb496876c0bfb78", "862c24454ed41a60908e0563a2aeb0f950821e1d0c02eb1f1c96def378089653", "864b50bd5d8b46c413a7cce53ad5f9f34b60df3512f167e1a5256139a4f74c95",
    "866ab4c23d375638e82983fa856af32f68f89fbcf7893f6d36fc7cd6bd3b20d2", "866e835f80fa3819999a493f9d4c8f1f8a64277caee9017b475104e929d60289", "86755d13ae956cf7e805f0bd679ed25d50291a7c1ab1afe42fec6796ac34af20", "867d3f5f596fb2ed6896cefbf3eb0c4d1ebb3e321dffa2ed3a715e98592b3961",
    "868679629eb56a47dd7087b64db8ff00b384bd273d23d321aaa1eb843d43cd0d", "869c581dfb8fa6336e6602825d3e0927dd1cb2f5ad4ee73245b7e4fbfefda1a2", "86c12710ec6f9fbb33ff25fc2365facb820904bb8df38e88d4254951e5edb2ee", "86e6d008b5b38fae2f62ea0f01375b37e123c9751ab1010a31b331717eb534ce",
    "871c295707cc1e3a221eef6c377427203ae8736c34dd17f84373ef4b2de3c5cf", "87391a6cab056205a0581c99b191af17edcbe47bd508586399ac765abd32dac8", "87545a8cd586d36cb7fa5dbaabad74d580198b3cc13a422a91780b0bd0610f13", "8765f2b6c881c386354b2d783eaa594cc7d872d5f6fc7f90cfa5ddb6f4d6d344",
    "876cc514b8cd6fa4b9f65cc2bbb20e3dc26347c1b7a384f74ffb93c2ffc0bb64", "879734ee96a0dee43937d0ce7273714aef1a841a3607a5d9bd0053f8baa7b9b1", "879f60bb562b4ce08163e37b8943a7b951404243b1eaec523cad4fcec5edad8f", "87c7b180fd8577ef09846a723b38847dff4385d321ddac3515860026c348fc19",
    "87d09830215537e11e69abde070130312f4df1465dfce514afc2bc7121be5e43", "87ea8cef642d93168e9148b20d2ecf198dd8e4d6c622ca5e8a693a1d5e2482d1", "881774704db3e27dddb7a24dc022c044a1c8cebfd82868038bef2c7ab805f003", "88275fae82da0f2954877e9092195861a698c48a82118fa47e8d543195915901",
    "88581b1ee2553a608d31f3b3b6670e94a59561c990db9df88fc860a24e2255e9", "88638d3240f803673d16a3005f6dc21556f34f7fcb1bf5f4c28ee201ec9315a3", "886bfa31529cc5f1cd395431412fc76a8d3b7b513cac7b1a02d8a01fea9952cf", "887ab4151678060e2fa52bbd7e60c225d227f2d831d494b793c6f2bb2a95535e",
    "887afadf189db52145f66a11959e6f15c7b8cdaa2aba2ef8496c0a3e08e987f2", "88eb9ec0e6d5bc0e7a8a556ed2d8e30ef399615d2fd01874d77c3c392764e979", "88fd03305de8f7f400ad2bb04c0ef3de27ac79df427eb343d7cebeaf167a3511", "8907f100e7420193bbf0807980db4ffdfa81010290c51741d7a3ac7c36cdb492",
    "890d5343e800c07085eb6e53542f3bbe45b87713c6df750a5fe5ba3eff167857", "89287d697da4383d2003862bb75cdaaf5fd4546ccef73abfea1fda4b90792b8a", "8963e43133a8dc3088c4f784f00e351b10456d48cbbfbf35abd0b0280408f559", "899d71279064036ab8794ff4316c8bce509ed8599026b7b8df4515594c218501",
    "899f6d7e16a109fad2e19d98676ed0d814b06d9f8d659b8912027eec5e0f39c4", "89b2335642000f72184c016ff943590c679adadcc3e4203f488ee0a090054f46", "89bb0a451d67b77c389dd8a8a46f10da947c4aa296417639b0e89a4d8c76f64d", "89d1cb2569532ce6e6a01b0627ac822f508df0e69f9d7c618b410f020e1bca6c",
    "89f3b9bfdef1dfd17e644722c9c3030f0eaa4c33fcac2ddd2fcad388b0885d14", "8a5abcdbcb6158c1925332da7c28d00e57fc25992cb97e6769e4a975d440ed7a", "8a5b7a05d3803e57f0ab6149a402695f8793fe4de3ec69a08c01c42cf4cad16b", "8a7160a67956e226b936f79bb93d6b1a4e43948dfd6852379728bacd3966b9c2",
    "8a8259afa73f4b25d4d52e0125c9bffd6ba3e2ca8aa236bd1fd920638ca74770", "8a8f1d85e29f119bc7947cad04b67132d3a3cd9453ae10e0b9154729377f3904", "8aa6a459a629a33f37386c1fdfbee2be6d9885da6c21f36ae797816a9cdd9985", "8aeb916184359c13ed829e9e2a5d3c0d81aa00e0535dcf85837af02f182be63f",
    "8aefacdda095a8f0c3f1573e656676f5bc844befe1c507e9c519976e4fd5f99d", "8b0e1c3f2f6cb22ad96095ec7affde762967dcfa2aaeea4758f5fcd47e8e439c", "8b1c1bad9c9a7a32d0a9bfe12bc1ad6dac09af130529585a87967899a83e5a0c", "8b1f1837bd248adb69e9ed9cca366f4c35ed07bf5f790007ac8d0b77d0dc9433",
    "8b2c3b144d47c297a1c5ac8decaf8aa2b37c4e0b3ab016c0c8abb6097eb12858", "8b38fa38c7527b416915990a276757481828ad278e74bc02654e4d0a17a3c082", "8b3ee59dbf7e787190bf7a078ece4148c8d34372d29e773a69e3b3438a1aac05", "8b4c86c42378656bb1debac0cf7cd5d52027e5828b8d5a4e376c4d81389f1d8c",
    "8b5acb3bd4d9b6b057223fe27293a07df7c35e47b15f8c1491a76e794ce0f5d7", "8b9208a8ea3623bcbd3498a5650a021e9430f99f025e8cddc63e335bfddb1a08", "8ba262c628d16b528ff4f48509946a1700fb5a5725ecb03819f28fd6b133786d", "8bd09c1f586af068adf4602e0ffb5647bce8c7fe91172371602824ad5437e12b",
    "8bd3607242aa9bbf686d862db56e48da695f861054ed30af80b3bd5c4869d430", "8bd38bd0da81c7069cf95280b9dc843e1c2c73d3302c288b01456797e60f57fa", "8bd63f27b90f02f3e75e40999ebcff1080ad9cf57dfa89f889aced7ad8af4f8a", "8bec0a306b7030da3add72ca7c8e4e6c69af141e85bdc6340da208a4f64e32b0",
    "8c170ad277ce3c82415d345aaec250239aa5c27dd97e23a193d9cb271354c04f", "8c4e605ddefb19b24217d11ed5dad111e0e9fd00db5764367721549aedd4b1f6", "8c77be2d0605b001913b82e5da786cfc9ff53b6ed7761adac4ad9dd910105f70", "8ca2d7e40afcfbe669c79e9b99f9f235b749e5dce3d56aa2711043383072062b",
    "8cbd632425e4cc8cb44039ee83588fa197caf7cce654b299b03ef3e8ba07ff40", "8ce09d246833d1687d0698f7aacadff85b0d2668bd8a0be55ab45dc0a00970ee", "8cfeb9378f45138664a020f9aa0cfdb70d075d3baa76979b7a73febba1916401", "8d0940e86f7d2d3c953d4ef9b13b66f37f2b63f9e926abd562f5a5569171f544",
    "8d0f4a74b434f3d456342fdbdc4faf2621ad4c27b58163aaaf5087a11a2d96af", "8d169c111ce176b6f3fb618a939b07a48a2b4f6acc7f108a3b2ee9fa89e083a0", "8d2db18aa87081c47659ce28efd072028aadf45401ac9a5b1e449963a498fc7a", "8d3b5b497f3804ba15ac39619166f36d38bd9b91ffe011e5930c5f5bd30c4a50",
    "8d7acea04c5b325350998b2a91cf88fe50cba0e916b7441bc6de660fbc9ae978", "8da62b060891e6fad8df0ecb722c7277e4a4c72fd55c719ef0e6b7a99c12c7e9", "8dad2342e5541ee6f40a5ae194edfc5f685746deb0cee412f4b76611dc7de4f1", "8dd4c289438c3ea8dc52727cedd1680f552644c9323f246f5bd30885d8e5294d",
    "8de68c798abca4b7a96670eba2bea2c84279ab29e9e94efb1382fd5dc952f167", "8e06ba59cbdaa121be6c349bf9ce56bc6d3873d6493aa3b10be10b9cddcf4ad5", "8e10a8690b743e23ba85248b180fc93600b15f4aeb3c5392f3bcf868dbf6504b", "8e25e7683cc02dd41e31ed7e0cd0f8851f69e37c4393143ce748beb50114bc5b",
    "8e281e6c42cb6cbbd60a789ec887cbf460b2564111733e769f9ecccedd4bf2bf", "8e2b62af9c63af83bc3f014425c0b9cc6d05f9e2bc15bb5c2797a5fb27f842f1", "8e423c07cc49f9e3271aa7d379b22970853ee546b665dcf39d5685430bff5d79", "8e5c8d133a8ecbe56be7abe226a0724e010d01d8c854d0c4a3e93fa619e533ba",
    "8e958120819898b3495f634cfc31a238351ec2d31d227b9f235f7e85bbe311e9", "8ea1bf9759a875917e1af7d0d0ae7a5881542be0768f1469c7ee515b406f8c19", "8eb028491ddfdf0e52b818c8afdc9a0f6b91c039199302828d6e8935514deb3f", "8ecccda7ca3c65bfaaab3c4964eba377a9f4c37767dca9964f60c6fdbcb919ad",
    "8efa20f0b411c31e27aad7fab3a8b7445b7988586c365cdc9c33596bd91b5d02", "8f2eb7919c37a70626a0cb36b7a98412195ef7215a48663087aa99ae12db776b", "8f32d3cddaaec3cc18b85cb0b17fb60c9a28328966fcd87022b4dc59f6924f68", "8f51fcf6ad7df3a9f24bc4140f3c7295df2a15a5c730cf1b37c7c7d553975a36",
    "8f73062d670ffaed2f7e9f8283236de3ed9ea205b806b5d1815c5bcb2d3db9e8", "8f9804643e1a59629d795f8f32ce21cd74c6e1dfb762fe3006e5203e3e011841", "8f9bfa94c495e234c438ee98ce46aa5c039db22cc45150e043d72462ad54fc49",
    "8fdee33f4c03420d86c3ae71d06abb0ce58e1339ccb35dfaf8865d3529704295", "8fec5bf0696940af3783f526c5ac66b0f35f83946e65dc10fb13de620e9fef62", "9002cdbfa83c763530c5573d272cca551e7063c69b61218deb8a0832f35bb1bf", "90c35853bd66bc51bffb2b146312a1319be9f287fa030d3bb0fcce58a6795168",
    "90d8767d4da7491ee0f6a7db6b4f31d1b412cd75e2aee48819611c4f36a19285", "90df6d8b4ce75e02be7cdc92c03a19341907a096d8c8742450691fc3e60d832c", "911c75786cdd7a3664617e9814d9fa50d284125659890e52cd99f52c139e2c0a", "912c26d300a063122d08ceda912c23fe0578d935dee09000d3c01fa2177ea902",
    "9134eda66e2c6b4b4833ed78fc69fba1bdc5a3925e29ebfdbcf34f0422c325af", "9182c8b7ebefea280d492ad2206106eba8a25bc8f2ee77651f75c1ec1d8631b2", "918e54e51f016cb0140cf1bdd5e5d2fe8e89988bb6232f4df19a9b35160efa1e", "9190c817f8e2effc33ffecba48861555c38a0805a24e62161a500623a518b9f9",
    "9195a2a853086fbe3e352ec68fe1a1144ee1608cf9094e7aae17eebf23850ddc", "91984b9bcb34e421957fd84474b62b402b5f2d19cb2ebdd5ae950154440e5230", "919b1cc3798f5f09e2332446a8f2ee312e00679991a38d99a616949befcfbd8a", "91c21a18354699057b7bf6140628422281e805f2dafe86f36d7607c95f0acff8",
    "91c29d36a353798b1761f7d3c0e5ab634919536d127b6da2a64d43631f485fca", "92477a0f1c66dc4a1fe343ff36a1ab01d051992ead4ea10b546f3e673795efb5", "9256b404970ce766ac602b692e8533d9ddce31f3759264ea1de3bd5f940f8fc1", "92594ea014e9cafd693bad66951cbbb8fc30788a9fba9e6635d4614577219c51",
    "926a9d108422ed606fa8c5de2099bc2723df8412c0f82752e5a04994a77a1a8e", "926d26da30d3c041d21e6c7ad3527ed70083eb0b77eb1628f506eb1c69f36cf5", "928bb9b7eaeb1bc0320dbe1341ca130c8dae048cad94c3c17d06160d8772b166", "929ca852b2f4686fd4d139f6d69926dcc94290a1766620604846f5c34e762cab",
    "92c57f33c9b60994f7ae5f62d59afac5965cea202e74bbd3612d183b9af67c81", "92cf0193e4e1aa736d77ca1b27d86ec4e8e1ab3e233bf43f3fce44c57a12ffcc", "92d6ef3332a0e8aa1f3370d648e3b0039fd4b6983ac2e38d1d18abaa0770e510", "9302135d4ed1b05a8eb2a167acb44c2b6bdba637a9155554ac574b5b888c2eb0",
    "930215a11c8c954f194cb76eba381f3cb0fefd8b9f5f7459e4f6eab3749cfc42", "931eda2edb7d35ba1ef7a9e24dbc5399a2db81bd941c461d8fe4e3ea8b6ba637", "934a34b9791e1cd200474f17c9f8c9d250cf817209296d19c93d7aced014a5f2", "934b8a975880edc26abb243741630c46c12a9a2c8d0db3dc2d39f59e6fb69fb4",
    "93585bd74964fecd04971c42a80f893df893111f9a73fac147e2f602d7586787", "9381d87be485fa1a34096ce832f2ac2665297fbc5bc9ec1ffd0ca4a3185bd8c0", "939acde103e54da96f2a3f391430b4805ce0179a3d101cefabd93ac04d4247c1", "93a63893bb7f3fd7fa3ad7299f6c6a6bbe7e5da3e318eca3b32febcc00ac2081",
    "943271840e45e0c67cadd1cf4ff1cdbf0c290d0b45bf54b46c1eb664ea18b5c2", "943aa7f9a3879840d7e4e024a85f7d3911ea78722c28da9c1742f0dc7636ab07", "9441ef27c5c8c6228cd43ae78c29cfa9d1152ddaf83e6c96434654e834896628", "94bb730276a272601ac664880588459dd96a6dfa576bd2fb5216e583c8a9f07a",
    "94becda61425eb47937f8718312ba46771c8e7b767ae882364319d513fe45c9a", "94edae90e240809c19585965b7b1f2a45da927809dcef3e77fbbf21ee66ce7a8", "94f744696722155ec7af9ce0efad5c7f24249422a07d3042b8de2b624b6fc775", "950027efdb0948e81f6525324fa936d26bf5fb621bdee173dc331e36e049dc33",
    "955fd3123c31432d80a97286a5d1b1dd0cbb9a7c73a1e7909a320c8328a67108", "957a1538dd53dc3ef3d476472f05a447c12edfdc95551cee9fa5b1111e1ac572", "959169bccfb839c1a913114d20a65d67312588fca4a496ececbad5aa145517c4", "9591b28d57895cbdc37e24cbbe55733df7b0875b850e9db778d7aca13920f339",
    "959598bd7820acca2e2770dd5d0f56bea8caae8b48795d1dd05b57928f88c6c4", "95f5d7e0902160da63a1546908dd4b55c25b2fb3e46552dc2cd189e15d29dce7", "966816785890e2be14acad4fd776985e93ffcb48f89bb8785db258a3a8294f2a", "9676fb97917e3c796a5fa1222b5ddd6b86be90f7ab2ffe338516774f66e700e8",
    "96a08aeb992a0f7e9629698593777594bbeb8fd586fa764deac99b92fc8e3df0", "96a2100b7039cb272fb9f9c07d3718e706fbd4aad14f092b9ab5624b2ec29759", "96a31af0c0b79b11b7d10863ac713b8c4482aaa4dfbd90b1336b307028faf0f6", "96a989c0cbda403761133a32fee11597c9d196e772d442881a963c0991389fcc",
    "96b1e0645fb331ba7df7b0d81af627085967b32a521006db947f25dedb32b53e", "96b3d2cb8e2374daf184a383893bb713e7c78165f7ec9ca6408ee6259812a7c9", "96bd3cb794f189d32e042041833dd797e82753bb968e5a650ae3cf4344c4e3cc", "96ca2f2e228c82781a7a1272579aec10c737c4039d43ea48ea9373f54861dc72",
    "96f02e837f11ca04212a0d2b6642f6785d8ae67b28b287896963e162c1adce4f", "96f3ca29c0e3b709cf49f24350b15e4ba62660e50d1b6b31040650596df3046f", "96ff495280d649226415946bcb1e0e82f3e82a006c8a8dd94b031ae2a90d2d6c", "9701fc19fbd17af1326351e8fd12c8fd73ac4cc1342beb5ec1709c2ca069cf1c",
    "9709282ba238e64252225aad372b3e6f03daf89fafc881d4b0315ac194921e78", "972cb2c48867c4edf313c0aa7c7d314daa761b238988e3f6502cdec1ddf9b1e5", "972d92cef64503a4e373de00eb01d4296cfd571a5ba05b98b56f53d2aa8917d4", "975577cd23e75b3c1e6ffc0acec9736e9ae61abe17ba75a7f6bb31441b6b8736",
    "9758cd4c7067c9175509dc2697ee50776aa49ed509464f798a1b1eef6c963a39", "9763295550fd3d8e237bed0a6741374053bb7bf15ec586f2b4763ad0d841bb36", "976e34b7e99adae3dc9b7bc9e43f119e11f257870c261d8a2637c77a1552e5b2", "97b714beb722dd8053b1e983e3ae0d0ab3ebcda7c29009cad40aa887ca73b594",
    "97c5f8c23b2c3eb905f49d0a93083c15fbb4d3eaf494801bb9bbe7a048cbea00", "97cc98e5e6572bc306d55b616064b757feccf3db30b9079165875ca26e8bfa34", "97d7cb4a999f94213714ee0447d2f11ade2a842201d4dd24ae534bb538118338", "97eccc87566da9fe4c0abc823c71b899051107978e5ca63a18d759edcbad3645",
    "97ed833261adefe6738a7e8d30929054de0b06200b76e21fb18951c27ce19324", "9801ad9def3f3dad4082e674639c36992ae3d570c7cb3f962d6765b9f9019a1d", "980992997459c167a85ebdd8f26190a4a3109070807d8dd04c414e9f563eea6f", "982c74997890cb37e198c96c8866a4b58378b992b725d0545745736c34ebf8da",
    "987a270a764bb8bd3e21644a7d668cb59f20246d280bddc93718ca5f76f6adf8", "987d2d9f2b54348d322176d707417f034b031edd7de9364cbb27d4ff768f0018", "98839549f83fbda88685363b9dc2cb2b29d11a7135ff08b68d6532b271dbb6fb", "98cf67c5e45e8949c5b0c20275d1f32c27a6995872d453dc2222d9283acf82a4",
    "98db1a8c5a9542ced551da343c2ec663e90d2e946a2f2f6687fb1ed8084d0436", "98e04f3ab6479ce62ab6bf2598c11a7023a97912937ac589b3f51284896ef49b", "9915cd9a789ff468694ae24d12451d3be134d417cc9abe24e4c64181bb4b8e43", "9923bfaa3c53d23dff6a13e4378c6a75c5803db95c3f12fb23ee94f78d202a41",
    "9925b0c3ed746bbe36386a0eb7a814b877c806adf610e6c7cba06b9ff8a22399", "993da130901a9f26df6efce930a8cbda78b8c69761dee3e13801f2403dacc09b", "994014a098062f8e0019ce255fdd72b89000bcdb89ed73ce88dd60943a5fb6fd", "994b348835a3684a005cc170c37266ec46a13d9723701d2c873c6aa8d9f62c54",
    "996ca630fe99833f0be970244fd0d7e268183bc9cdd5750e645d9af3c8dde727", "9973c242010090ae40386b723586c49aedd7e068ea7d5b4279345313d0591fb9", "99d41cf2bd648e8fe53026862d44994a999a46dc8ef9fbc12cb8036e6bdcd803", "99fab851353e6903c430b81b9865180878437f4fc472aa6a85435c223dd667c5",
    "9a07f6158c19bc1ba5262ad78b812ac4ef78af082f85676ace014264edc21cc5", "9a0cab466436540b3b86423f222586cd8bbe5c33bb27df5deefdff7c5b56d889", "9a28f5a8a7f3345491f7e8f18bf70a9c045f555b68de33fd5eea8fc77a0d81fe", "9a2fe5490be320c77ee18fa005ce64b914d3d336a21777cc094a0e949058de4e",
    "9a39e6da59315231efda2bada3fa7cbf8b7ad6c6d673c3d67561292f924e8d26", "9a42e00584274bd0b2bdd185b2d80e1ea0b9145eb12781751030cf388021aa00", "9a5bb4be372737a38a25cd62ad2b308a2b01261c43e71f124ef101723bbc36a4", "9adcc3ac8c76310879a724972e18f4d92b11e2fe763e774c4077d6c10a8e9f18",
    "9af30a0e6b50900a799622504eeaf8daf8a95916883ffba39da54a3ef725ac25", "9b52fc9c7d3a129dcae4fb277e32e292d98e295d4bd2fae7151f8f58892ef28c", "9bea52ec65d852252dee6e21dc77541115b9af33b7c0f278c86d14194fa22107", "9c1278fdcda5da253b2470ee75ed8fc3aeafc16c171cb79a70f2b33d35f3d755",
    "9c5182e069332158e0e81bbd8683d3df60221773e4d0fc5e179979d41c366f02", "9c6f87cefb51f9f9d388549cfd276f3dc43489ac12b2e3fbe1b90d8ceb1f7407", "9cac6cd4b97e6d101da1903a7eb2c01cf7700abe06d5a7b6f2603179136ebca0", "9cce15b56520452cafd32343e9fc79cc55dede548a6a318422414dd0d87244da",
    "9cd7a386f7930135ef939006726e15a4516d472741227d6fcdd3b4bde0f1fdee", "9cdc6083e659a7eb9098c88c616f1b03ba39e3d1edce38edaaff6f958018736a", "9ce38892b0cbc141ed5c497a1356d3251c7e1757b3ef1d45d256d546f6f34e47", "9ce781aa3202a38ce57e732809db873f594358a4e48959c6410bd6c0a7efb8ce",
    "9cf572021cbbda43650469f6b9fd609953e71e6f2786d845db3c86d154a5d724", "9d13d347de20d368cb79d0e652e05f1491a8688fb96fe947a0538ced7bfc1ec1", "9d27b06782fdb7ae0c0c877e2d56ea96060c427fc1b20e49f732c59fc12c8ad5", "9d3053e86b46d6ac3405a9466b7825036c6b6e27c99d5a4ed619f34083a16df6",
    "9d5dda909858359b1b12868ab5eba7efe32d7a138b173ed1492ffcb10a1ce923", "9d820164e92b9e8c112274adc57d4c3e90f184a5815d0e206eb3fff0dbf91fe3", "9d9a8b35a9b64812b3acfcf868cfd8b33ab29b031857493704cf47c23079c091", "9dbbba65f5aab3675e3c85554d00b3fae6471436126a5ce869abb4b05d1fc4b7",
    "9dd02b2a9418071908cf99a084ef3a3718123066109fa5b82418eafe0c32ee19", "9ddf8dfe36201412f12490854db8f4f30b440a8d3d7c3504ec97bea82a7d58f5", "9dfb81fb40e953094bbd4d3c46dbd37831c2f84464a830d1f457ec5fdea09afb", "9e0a4434cdbb42d1922ee5d104aa5c456881acf898ba31a7b3f2b34cb857aaa0",
    "9e0fdf5da9237b8ef332162bde616a0cde5880cfe89eb93da53aa891e3f538c0", "9e114802e3f03568034fe936f47fde160afc384209e624b7d8479033836611ee", "9e13e1408b316dc241cf98746aaa75d5db2db84b915fddfe25824708c5be4c54", "9e180b26ab5a0c7c61d67895ee8f70808539c825e66903bdf7b2ec9b615499a8",
    "9e31679c197b0afa74d4a65bfed96875f417d4f99bdd14f81a21d3333e575a4c", "9e7e421d6e9b4073b82fa69df4c1cfcf474331a63ea593c2ebac411ac3fcf211", "9e9d3f45bc63aa4a1350c5d6d42c872a954188435b8af85dd54b2a5059e1fc2a", "9eb72fd74a69a8d16abf2e0dbb26987c6026b6e6ebfe6ff31879002ee2f577a3",
    "9ee828f4393d7ea2ee7965a73c23f4fafb145684cef2a344f7056e3a5dfb1df7", "9ef783d0ba8f5b116e9bd4e6a4836a6977bc08619efb715c239c3d53ad624537", "9ef7cd3ef352e502e7e46fdb6bdfe314ff6da123f3262314f54a38df0a749e05", "9efcaf9047ecac57ab86c84096b9a2d497bdc09e51c8ec589f4bcd3d63f614cc",
    "9effe20d2566a84b12b70d46a7541e67c8c88fd60a6b93a536b5372abbb9681f", "9f132af3e400345bfb8b682543afe35069cdc1ae8fbe16ede289ee55c2866501", "9f3eed6d3765408a1b7a822c900def0b718578d2b5470a880b5f4abf20858724", "9f419c194fd63117a2120f724bd73f904d8644690a0407904e54e5d8c3fe5c50",
    "9f56bd5c406a9f30b53358c805a643e969dbf4f99ffae765d75a4802fd17553f", "9f61b529521cb86611e7f7e4ddef8f984a0461b5be6e79d741a615dbdf485b76", "9f62d0b8fd87564e855bad77020bb04320f19255e339e4d43717e4bdc94c6006", "9fc524e3d19d4c44d0e692d6dab0e400e2d9a4bfae0ad1d14fe15741877f6bc1",
    "9fe6ba6ee26cfd10613b4c9a8f8226205c55c6b5aabc9a6e5f0d061deddc3549", "9fe9042d59b1efaf371dfc351f73045485eb94eb5e6c5ce37ad2dd5f2b3baa83", "9fef413bce5a062e22a2fda8041b5132f88829a8bda7d29852bcca2e69b9ad6c", "a009432c6c15149d8b6a40595be37384b506acd0f28e1a31cf681cb773530831",
    "a025d67cfab247e5de43bbed17940cab65ba52c41eee6fc4762d61a977237368", "a05ac94cdf9de4a6efffb9de048d7fac683d50f01ff8bda586d40e56ea898ef3", "a06d2fce9bb9f7dfa01f84dca0a37d32ff6a62787473f2fc677c774ceed8542b", "a0c56f8b84fc850e8d8c0b2a78128ae91480ac98b1c4cc796cdb1021a501b0ca",
    "a0d299a55aae78ad7de0f8d42d1040b76f6511099b7058e9a0d467aa8d4cf602", "a0d35a0f6a861b86fbe81c713b4d8d46adb3a5f7490c9a14bb67e992b39cc105", "a0ec42ab03e934e4764914b219e3684f70350ff8cce3fb003772faaf0a8e3a57", "a0fed4c0a5d03869560da3eb486bef911b056c3583ec65c0f5f3e3d2b3bdaaa8",
    "a15d295fec268b4e638413e785eca98dcb3325105aec658abaa1796b7dba9922", "a15f108fa5892d05b5933264871f4b4d8b6ee010daa64470ff8b82f4d2fd373f", "a1636ad29ecd92b1bc3a103b7e40e68471621d685d88328a049f65b1e366bef1", "a1752872b5e403a16da9b4b4e1266acfe0506748f3cb368cf8607a26ed8c6e33",
    "a1888a6d919c50310eb217e730cc95ae8e34402c2b87b1660a3ebe032465ba04", "a198d0028ce83427519cf094c839e9d0a7bc800489a9e4c8389b0f52eb2ad145", "a1b17f36276757416b248b2795a45daa62f42ecb8f319eee65ff48809d5eddf9", "a1bb733aa334c18550f9d55154122d7a6ced7316a3de3e26998ddcc1ba23359f",
    "a1e7b5411d04fc46690d95ccf1b9a60809108b2c44d2ab1efd25e5c796d51999", "a264c8e127f12a4cfa1120ecb3854ddc0e81cc11d8905b20f5b62cb7621aed1a", "a27206c6a9411e98ef9c5e320062f6def662bece54ffa8b694e9d42b97a07219", "a293a3057b2324b2a1c823c431de0ce4ca35d439d0fa2bb50ce9377cd587c5f3",
    "a296e754dd2d8dd01443d8330ebd56ffe66355caffccc071e29c3addbb2ace1f", "a299cdb84dee7e22663c47e0c4a52309de86b7f7e1ebac27b6ca3500dc151525", "a2d99badd2c29badbb4cf4662c7bdec05696b7af18254e6a71d643dae2168f82", "a2e46a9b97d7f28c813f20edc7d419b5ab8b330c903347df0e1b4674eb3de94b",
    "a3038ba074e1b9614aca1487fba065d396b12c6ea9f03d016808bff5405128b1", "a348996febffbac8ef86fef9ba973803f29e50170d8e4e4ba50a79ab004b5f0e", "a35995815a765a8a1605dbc0b5bf6b510919521345d9cb17e2f8adacdaee1d38",
    "a37228d541b216abbf282b6128bd5810e92268d4dc38049f8138d85026f2d3d6", "a3a44bec0fd421ae81d47994fd6078d7be50b28cfbd8ec3e66f103178c78920e", "a3ac406c590a9a30a838144d1f0ebdf36c3297dee6e93ec1903151b641ee886f", "a3d7f4e437fba44a0d40132a8caa98d2282618f811fe959b654fa5f3fdac6e1d",
    "a4074c80062119ad1842453e04a09ab86f6caeaee4f04139e0d98fa8bc4198b7", "a44f83dd0eae705d6a63a3e4946afbce15a7889c26f1b45abaa43fcc1191a681", "a4521053b5650dd6f346a92e1eb1e4a564eaa8f30ccc6730a3195e111d8f2b80", "a45d21448a7b8d29c771f75d1523f8d9b8698c605d80ded46d195dd2a7526d89",
    "a465c7e6dca78a07bfea4c1f9dadb701c5eeb920b79da186ace1acfe60757a55", "a4687abe2f26f7a09a87c10f6c3da6a075c7f9408d4769aad3408fdfcf955cf1", "a48028114502f4c135645983b864490f6c41a4e758e312c8a4ed0fb13d6c11ca", "a48b9243e3a76b583b603e04c4596b0bc2160096d1fcdaaffb83ad4f60f29db0",
    "a4ab02a3325b3eedfb3a5bcc45d64dad64dc5594224dc3b91c3d94827e1b8cf7", "a4b3bddc6dafc7c305ffd13214f72872287922ce51980f9ba8249d800a14a80f", "a4c97607f9311c5ff562a77a25dfc1e0e43e6bed384384944c8973de1cb8f154", "a4db533fe5e36cb4731a12990506b6fbe2162c11c859737b4a28370e114b5897",
    "a4def10afb2e61d8d9dcb8a5a22cb00cf0e8995189366f04edd0c36580b58e44", "a4edd36e17239d916ea44757be8630fedc7b8cd033aca83b24ca1cfc02c41ecb", "a4f8788cb0a2ade83dd286e4d185b3effd2d4546f1589689eb1ad4b3cedb8567", "a4fa4f8c69c019cf9b2031ce0756d9db5e84b5f643ff32a511148101b2cfe490",
    "a53cd6ef625e8146985aced255820b572e2224b42e6c4b2f5a93918916fe83ec", "a55ec4592b8376cb6582e6e8c62aca3341fa27265a5c81d47157ebdef686439a", "a57262737ed9ffc720af5cfeb3d4df9104a3b7758fe46fb2faea1b1e5b74402b", "a581478485c241158f49b4945e9e0061b9cda5c1326f92a6af065d45364b3fd5",
    "a5b50632eb1cb61e7a9f70fd58988c5501e532a5b0c1a803b0498791fb847428", "a5b8fcef25f46a4abc2a6c28192fdb9e40b9bf78aa36293fe63ecc7bcd91a2e9", "a5db0df96641e83ce2df8e3b25ceba6c0fde908dbb8e712edf81d459a610f726", "a615217481ee73d0b99e4946143332cb881da82400cc3fc50314facce842133c",
    "a67b03ce95ff450d6feff34202881e2c77572101a45e062a2c21663cfe9779a4", "a71e556aaf869c9cc6732860d82c9e1cf6c1c144f49077a76cd402de832449ec", "a734ee8ff3a92c315466d5e54572b8ff4fec8cafbabeb75d907c50992c18a1e2", "a748463f8d2e6a63b08ebffe5f637e5cf751bc2e8c0d01b24402be716188e06c",
    "a76a2f4273f4c25fe6c7a9a6c5826620a13ef28430c1f837b33c702f5118d2d7", "a79ac5e60db68b235e4bba0ce464e9a69fa1b2a7d3ac223cef265c2b239a5012", "a7a056f25f01b5e70a59a71080aec05f5ee715df227fa255cc60d5867286bb8d", "a7f9fbd83fcce48514e166993695a607c713e81e114ca3c3e737d5d0113e5158",
    "a868cebe7c1688ec31ee3ada8efaf981a2c1a69566d513d4105233b870fba9cd", "a871577e7716136262a50e8dd71d4c4387c304cf2a931f9f4e111da2a605c9b0", "a88b28e5d5b9a199183c7b2de894a264ac7f6af0bb917c4824308593a5fa65c2", "a890d1a16bb057a81c3d52ca20cabfdeb5319c374c16ae9b4770a54c5da96231",
    "a891872c31fe1b0fd622122490fc42c29cee54562b8beb24e671d4c147c86a5e", "a8a4c08454749493cbcf9d778d78d6eaa8b695774f33bdf7728c39df96434180", "a8db20be16bace8acc8dca49631a3853b50900b058b56c3bcc067bed9e16a716", "a8e628126b28fd72646ec2a53b956293b4c32787cf406f9e6b774f7f991ea8f6",
    "a8f3b94154d1d53c8fd17be431d34cfa3c3e64a064b61075a86bf9150189ab30", "a92655e9e315affb83094f89eea0779b372914e6f4ec6a98a7d4b44023d24b71", "a947de8907a69ae21eb497bc7c0bf1076b4fecdb6b094f0943f5b14025a192cd", "a95b57986ca05dd2f223dba9edb61c15c557f821f93264ba4bf36b98f1809e70",
    "a96ed5adacc85a60ac1c7ae159ec30d506b357690483ac446ea4364a67c68582", "a97c82f0ae1ce647625a11aba7b72f562e46f8b0d038a83442ff500a6ab012dd", "a98da493d7e11e003c1043ef845253abf46235dbd1620f858e342788bad6dcba", "a9af6ead9d017572ba906a01b449d678b1f0b72f7850046417192980bd742a40",
    "a9c76af59ac5e190ce129cb2589b1068cab5d019a1aa9b8a71dd6576b2f7fd27", "a9d8c74c6ca271ca2968733cf7eb6b1148f2fe48ec799455fa99a7edae28a642", "a9e79072feeb0be7f7ca338975b0ce432c31d746b40a9e31efe599cb83a691a7", "a9e7a2d1876adb83535c8019824f5e4efc83b0d973fe6c2d64855950a5bee234",
    "a9eb9ebd60fe089f8d98b04ecec0af3c5025bb018348cef50483600e8b593204", "aa17f74e0bdb85037a778200fd470e1d755237205a14b009e6afe0fbf37c2d12", "aa211eec7b4e5d4c440d7be11f1b6b8b49a55c39fbb6552a1f6b42f2e1af3649", "aa73da28374b9459098a4118ae7db934f001515ec50f2601f6a3605ec4f0e498",
    "aa93500bfadd59b68c02885f1ade96406a9f6cff65d1f1ae8e3d1854cd4c4f2f", "aaa02b2ddeef8a0b8c418568f7d4c1d7cef43d90eb901eb786719f17d3f61869", "ab1c3a616ed26ff90655ba98417a99cee3191a398f478bcb76bcb45003fa8b30", "ab378a737760deb4fed18a5e2b817ad3d935c3d38c685fce2101fbd18b89173f",
    "ab3b3dbd3c88ad5dc4a0e9ff88634b43c3a0bd2b715fe79156a5d2d728ceeb37", "ab5c86b7e0ac738792874cbf6b198e0c00d23f308eb23fb72219fbe7e9d2a47b", "ab7c8c93a8f913381c6daf2002671ab595dd1b0929486e77061e72115c5cb8cf", "ab98ad3b1f0653512c73ce39965310dfe3ff31a45e102544948ae4500ea5376d",
    "aba587f0e5b9a3792151cbd5ec3ec44c8355b24df7fef32cb8d16664958ade6f", "abc269da4ed827de2d2463d26677b197857f444a1397f10498b62cb20f3067b5", "ac067aa5bac9cc639a05511c7b8df14daf50750cde65d087dd447d1d3486231e", "ac2f2e140c4fbca028f7a993a99ac1cbe56126f117e3a9710f0cd057ede6665b",
    "ac383ad0a4be0f6e776319a5521c0eb0058c5d11e3669ff7c79017163c7d90a4", "ac49166159fff4396671805663ef1d6514c26f01738f002e85a6fc06ed1a254f", "ac6e2d7e7f4e1b28f798622c8cf2ecd75b9881319ed116c71e1ef8f23a96c9de", "ac859982aca0690c205ba9e10060db193c89decd5b27223444cdf55d4dcbf69d",
    "ac9124aa9794ae5c115b21f46030715bc481440a6dd75809547b24d6f4e9e361", "aca8e6fabd78044097a11cbccf9dab3cbb34dab335e094770bf67714e8476d04", "ace4d7c5dde822a530ec444de3002f3bc257ea3feb2e8a8f8f8e16472a8f9f62", "acf734d28d951768a53932411d54c36bf4d49546100007e20770756163264f7b",
    "ad0c6fc3abd3648624e334110923398832651f4364ca54385c946b0712e4809a", "ad11d967befa4e550fd350ad0cd392df803d4b571510986938af3e5c7eb602d3", "ad30df233d2a9415bf5a7283804a5663daa367f8c369c27a36d0686493a03ded", "ad5200f89669816de2ab7a89f0f53c4677a13cf13e15daa192a3de20e7d74ba2",
    "ad6d354eada78024c5682dbc2f213d9490d6a52d838f0529537d4f8bf19d5a4d", "ad6da052c812953365a93fabbd81882aa58c0b25d9079f9f4a6d3de50722b245", "ad71d08f426ed2a117e32b48ff59ba715a0f2dec25b62fa9c6154d8cbdf0ebbd", "ad743a8714dae9120d0a3a32503a45b8bba81a7845f4808270c710085e9d2bae",
    "ad7902a46a54804f529a54da787b194650ec089caa9f36e6d06249d9f68a7458", "adbfddb603e6a6207a84c842ac226f28c79bac348695fb7062e9a460635df048", "ae6cf974765c711f0b22be79977ac59085a0435eba6b4b2cccacb2bbc1c11a87", "ae86972071898e6958ebed1e2aa7e47e5e8d89436fea0c7dfa5e10599ad27629",
    "ae88fcfc4de7923b509a3b918b2e2a8ff3f8d7f4459bc7d123c3d289b7f0992e", "aeac70b945b9ec89a3b7e096aabda49e1483ba494434cc273ece9e533f23af6e", "aec25abf12d81a58fb9965ce74367612d5978c86f0518c9f59333a436569ac3d", "aeccd0762729b50a4f5d4bbd37632e1ddd7423897a20c409336b97ae8e94df87",
    "aefa76f50829f854a7d6fb378b0ae48510e144ee846ab16a2a833836d3fbfc9e", "af2de167ed19bee2a8331b77beb8280766cdc3a801b636d36d4f4b377b4801b4", "af4210e41ea18464527fe291e9b080f3c5b9752e1b0dcddbcccd6e09be6e57cc", "af5e36fec17ef403277ddb0e9eddca8d42dcebf3b5c515274f6e504cb6a86038",
    "af602b750f75ec665d174c056d1413188c8249fcc130d02f4f938474fc59732a", "af8c0cc9c26f1a1c5fb20e31a935a71217e85701818bb81bf7f6318bd2d4acc0", "af9c2eeb35ee682812b9f9763dbdfe58e8b7775d48e7d083da66666eba98bad8", "afa062dbfe2c7a3d84e8e7aa85683bacc30f7d3934b2b4030ad575bc36509737",
    "afb2dd32578e77a362d4b5076f0cbdad3332ca226c470d998530a045c54e87be", "affe666ee751151eeabaea64423c401ea122ff99f1dceeef8838d6dca08869f3", "b02421d310deb128faf0611a96d826d17d8e56cf3f0f80136958aee6d0e1fe3b", "b0489108d4123900ac38f42ceb0afe41b883c809a186101b26f051bcce0615de",
    "b095a8b197740d6b85626d91a5c15f49a2a4ff39b9a70108f23c047ae5c38b5e", "b0e99e95e7afb22a46eeffa64b1438d87895ffb65133f0f22c228507a791b2fe", "b0fe824f8e0bd9287ad934dfdeb73002d84391220b87660ace793a894206803d", "b1123fbb9f1a6756202c657993ee8e942161275a004058e3fc084567ab1b86f1",
    "b12e4e3796addd5a13d6c8d43a460b68cf315c394599dd84b4bbdbb2a3aa8b85", "b13dc899edd05cfb2c5bf97b98a5c7f81906a4e413f86ee6aaf1199d3d902498", "b1697e72e3fc687ff34b92d6dd8ad775cef0b50483aa523a7ffb641da75480d5", "b175f75246889de7f2dc15e4aeb60ad0d2e7137f9ce6e537c7c3645872bbb840",
    "b1bc060051a1ef110026f685710428f1f409c9b644f982d07babb439fade5cc4", "b1d20f8c72f6eb24943ec865099e13e64e538c596f9795be18669a7e7f510476", "b202e8dcd85ae3729d8cc1e5f18c7434cc652e261bb10997e22ad07654f234dc", "b22ef44579fe91de977dc4fb6e63850d996ed08be781e800e2bb670e8354a74a",
    "b23920dc164e427a34a1ba80d0be2d0e25b55761131d89408bfa49017096dd33", "b2786f5b483f95ab822b35928004aaea691b056812d544e906c5a71d5aa2603c", "b29089f22403ea319aca064e06c5b88387d07c2351a45ed670e7a84b14bd4c14", "b2a23d0ec00e8b5fa714a4a583fdb0778a5376b8b1ac72218614d64f709226fb",
    "b2de0cd30e664d22af1d5718317b691dee1914056876218e4f7fa68c40410a9e", "b3257ba5254e82d3f5760e55e36eb42c047025ecc6dce1e3bdff4be5a6142d50", "b35384a27ca2366c1feb05711f1997baa42d5c9a6c743b37a2203b29eafafb52", "b3605476bca1bad0494de243a6c9bd58d5cfef8c71ba885fff5dd09135a31e90",
    "b389311cd8da8e48ac5cb1eb64b9ede5c4a5723698cf3e5fc6dba7d432912fcf", "b3a3e2b3c8719a4a66129cca300fa7cf33cd5bf17507ec20898eea8203f78cc5", "b3d2a0d11736df5be6deb8232470373b84fbe8575bdbb1f76a7e0313e0d3218d", "b3ebe461a8c94c45b42b9c62aafe94dda20a3ada603bbc8feb5a77228d97c6b8",
    "b442c410b7fc88e4b1c172d09a1b47404fc32737a6c811c3c57697132d0d8e24", "b4701760ff0353871e8570e421e5f0ad93b2abbbdf62951ed3f82726ef208e4e", "b48ffbede51c25b3b3ee147481a87ce0ccaa8fff3462a684da48044d8e1f5f09", "b4907c7e780b83839bbf89b9fc3c2521603026c650d5dc8e3239c115a9af8f2b",
    "b49887cb4cce696ffdf51a3ff661d964a9830dbef45bbffe8fd2a8c8552c5f19", "b4a2a73e03614b393d12ded394996f1b260312b9f3b5cb5afe1cdcacc9c63add", "b4bdfefe951be32942ff818226c6c7f79098bbe846b19b766a437044f6f2c1c3", "b4cf9014c98110f31000459af825c449548a9aae703d601280518d20a7da2e38",
    "b517965852ef42e64509015aa2fd9321efc6a7e00c3e1ae16077d4ae2b1c81c1", "b55e783b1f2f7d831cce0da4610ecda9c456d1f66bb683e35705ba6f773e74b7", "b5610a60e8c133bba2cb03bc922e8599b13e02f8db642b12ae385df641f4d359", "b5b82ba6ab50727764cd63adcea0adc7ed7690febd5fbd8316651b209b190108",
    "b5e2d0d3cff58648f9dc90c91918a515589f65b12bf2da2a1acfc042488a2b5d", "b5f8d479d88017bb922fc1b7e461895f8aab4b088288c9fb496744f009fba934", "b6380236094dac47d407d0787d929777cbb7400626c24f864eca99f077c593a9", "b6518b90f4af4ec88be941fb865df60c51a94d1cf0cc8f67f2ad6b6e867ef3f1",
    "b6c4be31c78fbe0f116cac81bea5a0a2abd598e72f35498f96b16ee13c6b1d31", "b6c9ef5b8161bb736c8d0d7d16d768843607dfe430f087460d891489396573dd", "b6d9ae0a6f6b53669488b9daa920cb259d26615746a9fe8e6c01b842fc57a9bc", "b7020f7b6131d046e845fd09ec52f5ba09258f5de920e7639a990c9d8dc26c3e",
    "b71980a03a2aef6e3cf4d55c4cf39cbdf5eb421c8d3d684f13835ca43d387e8e", "b74f098d195de4d220b47e2ebfbd330c21c945167c12be32a18a4efcf0a09854", "b77c469754d3ec48fce5c467b7c57ecfd5b5272a559db73dc71a15d4c422df64", "b77f492671df12ee7934d653874edea4a4f60bbd8e0c090e736a303d774d46d4",
    "b787b884575fc0f24ab3da8b9a6c9f17336e2a6a34a6857341e1736f199a42c1", "b78b3fc45e3e01dfd7587781eba4b8cccd98b53043df79c24c04b8af40b1ebdd", "b797f48dddc1cc77e97fc73211e3191cce5d803447383e0f18d7288566710af0", "b7a387b6f2984d8deb744935b07516bd3b320dfdb686726ee1563141be255b9f",
    "b7ad8bad8df45ada2c66de8ce26a57c1b0ca0b9d8b359634bf058a605d7d2e12", "b7b4f6679f462db31ce5bce3040a9bb335a6b64374e4be149904832afdaf56f2", "b7b59597504ef8629043fe8d32d9d32020cb26e99dc7d7192f998a83426df2dc", "b7bc583032122711990a03fa599f86ba6b246e096376a62951bfae3e6aa4e663",
    "b7fbd782237851f094c06570a0544de26e43ea7728b17332d32a7c91881844a3", "b8072b9a0ce2f8f341987c512eaba940a474d16e19282ab1a5d51e2f4ca71e3f", "b8461987c73b569c93bcd49a4b1b55d5a492ddb92a1c6447ed923a9ab6dfd405", "b846ea13ea7bc1c6d2261b4a900ca1b0b48847f9a366d915325999b5c0a541aa",
    "b8502c16874f65445c6ad5578a0a8a5b38054d185824ab9cfe8ca96ca7721ddf", "b855f6c82463dfbf401917254f9229c07e99df11b4b777dac064107a3b0f9eea", "b86ad5c15f18bf13e6c1067d6d30fa7fdfe6287079581c97760b5c60cc8e8150", "b8c3d32a3dc1ff038b5b1c2f77691b532c437631d94d21121c4655aaadb5eb6e",
    "b8cb07efc43977d1fa1cbb60141b492f2927da0374bc01e4b00fb13524f3f8a3", "b8e10ac9744ffd718c48465bb80c87882d7a821d77c938473b278a4eae8828bd", "b8edcd645e779123c3acfd8199d8e52dca7e8ae2966e83df9d68c3b27d64a83d", "b919cbff4e489820afeaaaa1f0d64f73a7ea547c00c87584f101847fc87811bb",
    "b95c9ec7526db6c00c78334aac0f3a7555e5b52f93093c74a7d36a62d0edb825", "b97d7be095454a0f056876878c1fb278efbb5994cb5d6ce25e4c2be69f4c398e", "b98f6ccc48c85a652efcce17b6e897dd3d00eb131b892d281efd0b6150e5d4cd", "b9909b77842b1fff7df8165f4551f139f54bb9416ab1310e6b0f29b98e784c15",
    "b99656cdd635fd52044cc208c85ff1191a79f517ec7919ef22c41e4fcc9f884d", "b9b8d16b16bbf292bcb1eb3f18bd8784c1865dd36e4c0664652b2858094fe2ec", "b9ba469715adce81cb1ccbc59a1b6fa052085df35952756e9d838017a97a0876", "b9d331a7827bfb47432e309fdfb9d331d113b9cdeaeb5127184c7c53ed58052b",
    "b9de857c01695390ea1c81eff3afb8b358a3496110a027316ae3b9edc8541a76", "b9dfcef6fc2f79edcbc3fd1b88b6ab52494660010f261c54060c22105e5c662e", "ba47e1b75445bad0077a190bcbd8c1aaae41fab855c47a51710ff098da37c7cf", "ba54b973b439457481daa305b86347cbc524a51d6c065cd80198844d78d0d67c",
    "ba6b57d161bfaacf6489b4ec21925c6ec25a15bb305c3afd8466ae3d95af06ba", "baaa99154501eb404c71d0e00126b0a72c46e499ce46f7ea70f7166caf1f4468", "bab1f5c70c830ebda92398d337b3b37a0271da75846de6ccf3cd21ed405d917e", "bace8a46a6a1f6ae963fd3410c996ef4e3668c7baf87654f4e357f0908fd5c9b",
    "bad4dc8354c1d0afb297cc6aeea21a877ba060272821d86dcc00666ca422382e", "baed77954bca1e496097e5469258d61e563b3df70da8870bc22c5454bfeaf6ac", "bb01aa45f1cb27e888315b0a699736fa4bf48c74e2b074be8c4fc63a8d9a2421", "bb293d293ffcfab47fc80cd4945b66c729ab68adf514f8a334945f1c359ca521",
    "bb2f2c881893ab351e66fe62acadb79e72948797186835dda78cdc4e381c9f95", "bb33ae1fbb3e5d787e75a41ac31f76102c77392483436c3926f826c327cc9eeb", "bb3b584502fe9651363809439c93986b787df859cfc1eb5dee55c67f345e8d5f", "bb3c2881c4a0c9a368f7e3735180af381b9ce552f7b84f95f459a5a29d749500",
    "bb66546dbe239c09047a0f09e9c3f122ad649b25bd3a6a8b780734b3debb8e33", "bb68e95beb7f925c9ded7028ebfeeb33cccced4901c3e89b5e68cb3c946eda77", "bb8da9dd597d02dcbb789dd34359538afb38693d65e344c30976f8b423189078", "bb96ae39779b961664793b93c307ff709840d4c3a32784889c3241d53cbf7610",
    "bbc60c91bca8b46b977bcbae4779a9397e9f9f8b309ad43b89582502ed4c60bb", "bbe2d2252b473d3ba126691695be8320d7119bc3ed96a0781d9dd835a6abb7ae", "bbe6d6836e0319fa2029c0ca97f767619720f59b52a2387c5f708d2f3f75d67d", "bbec83c83f0caf33f685874654b83be3e89a9cfd9f975203efb4448ecc83a795",
    "bc1364e3f7586358ccf541f54962027f96437ccdb28da81b9f4ba00f22a1ec73", "bc1f84176368646858eb345633628ea6cdb6c12d1ce61fdb6b49ba9d11f461d0", "bc35e1abcf04e9ac95b4686f7dafb74118a605e84836a5ccd070721a16940fa1", "bc6385e65b430f5bcf0be8a20464a3fa528abf0c7081a16295262b92816fd82a",
    "bc9adccc2395a861d243ce4d46b2975758adb7075a4cd8ec2a52fd704f382d8e", "bca1ded2ad5761d34b0641f73651658cc9397f445bd5ea07828f5fa1a54d582d", "bca35813c673c5bffaa04c304d5e898703a923b76e4f7351e0bb9329a2ea5112", "bcc79323e4942a88f2936cc60130f49797189422c9bae7531a91542904c052db",
    "bcc861eb8275b88f3ba62e6d93c8821f2cf6b91d7ba701268509a04b1175502f", "bd311b6d5148a04e8bd75e5b7e74ead61a5f2a4e5665746f22746841ba3b300d", "bd3c7697ab7b4ec2e1f6687a56b9c7dd244fb628e1bddde172edc307c1acaf85", "bd810f6e7daf893b3708a9ddf3c87d28db40158ba38c737786df7a4c5abefe2b",
    "bd89b82c91d76fe21740daecaa42123c9c2a8800b32214ac383d5d70c8991446", "bdbd5cd55cfae122fc82a7617a0580c5ebad4a9f2d1d3873f1f0e31c1926fad4", "bdde66ca77d4ebf009115765fe81c2e9adea08126b26d4004fec79e9e28e64d6", "bdf6fa8e64a3c37330992abb9e040073f23b448c7fb888c16a13281ccab2158e",
    "be2055a0cbbbeeb8223ae4e8b36aba8e847bcef8d1ec5b510d48798a9da356d1", "be2d3c0fe4f724fb93dc6a951a92354783c6b758cbb6542995fb14f9bc86a0f8", "be826c1edd31879183ef34b7ea6d27c5e54b8ffd2477b353cae00587b49ad5eb", "be9ec652d65e9277ff4235f68ba58d67388385886e0c5afd2408f25d15e26c0e",
    "bea9076b3983bcea3708c6bedb11a3b804385acc010c34aeefb517f4c46a6c0c", "beda0db3215a1bde82b68693048f12167a2cfec078ac2fe09d9cf0e45d0a66dd", "beef6b9ce7fa9e4d556d4e6a297674fb99409d11ef46350d7fdff26826aa4c5e", "bef96808fa65b7f5d25b49cccfab9e0bc992924afa3d58a63d18efca9306e16e",
    "befab784a594b1a0339e03e2bb08e1ace2189e1fc1877afc992677ed929931d6", "bf15474f14b10cfc4b42dc04c491b99851eb849617dd7cd76aa4086bf69de834", "bf193c18e1c1699495555bcc18d27ade4ba880174bd86d036866232ea45b22bc", "bf228b3e4eec862c875eff185d1efebe7b118c7e1743af76684859dd6fed3e8e",
    "bf9dbde0ceeecf717080b802745a1bc468fb49c41994bb6ec1af8ef386ade9c5", "bfb83fc8ec1a55c3a33302e9be864e5583f36a215b899b865147e3bafaa8a16e", "bfcf1eadc1ae0c2c285ee091a2f8fd1cd110d34bb6372ddad6e478c82a5eafef", "bfd2b27b0597bcc400f6e6d2aecb4b1436204f61b4a717a67c3eb81116be9a60",
    "bfe88f2734445094dad2e8a4e1264b908cb57c960baba563f70a76220023f9ba", "bffef3caa990df1f8300f22b00718458757eee149fa40b6a07aa90eb8c493678", "c00ed88b326f39acc37ecd9ee5763a704583ad102a7268f60609534e2ddead0d", "c04c4c6be4ef20b19096726cca964160845a11a0ca194bfeef7be5c4bae49f56",
    "c0717779b4c0ddd7b31814ff6d71f6e08aee16a7ed8812cc4c20204d928b1a08", "c07eacf87cc936ceb005deadfa21e284e98f71311f51925382c8fee14653fbd7", "c09fee6afce564ae6a78b84d0362e3fe7102f1293aa68020e35f9d117658bf43", "c0cf093579bc9258ff3ba034c753c762040d5fee3752f4b3954be4b170ba8c9c",
    "c10a93bca6f39fbdd104002cff21c1399355208a74c6055d25c950f57fb73166", "c12d4d5a11f9323224168f2d9e1e987477c1e73b91a102c9302c396df009db9a", "c140a524e900dac5e2166fe199d2012814802c6b3182ec350bad0c98c41cb36b", "c14788a1b53ca1bb2c74ba33e53031dc978ff8cdcdefb52e146a54983a0ebfd5",
    "c16e59ca195a0532ca6d7b905c91489db9e89de06f972dfc969e2accc4ceaca3", "c20320ad910f7b056adc36960582938bd79b058fe5d6689f708f9f4ee2715ff5", "c22107b93ddd388e4cfa16e65f7d7d7fff220ed76aebfaf9376de191a06c9a5b", "c24a546dc0db7a40a898a789449098b0ba9786f3e41953fe786121c113f25230",
    "c2acac57dbdb4135bf3731a7995f6aafb84b26b4a5c107a9a7cdcc4788a9384f", "c2b762c7fbca54786fca27ab9e1117d1b6da952be86b4f80df838ac6cd9c351c", "c307265921cf5379ab386af4cb4de717743155b9ab7aa7a6b8092f3b1c378748", "c30f98644b1a442173c4af8587b78ff37765594b7630f47b2522d092d67135ec",
    "c311247349b21c13b93ded2151f86b89037c51a17b896327d828104207c9c6fc", "c3144a071de27101ba03f5f3b95a2a7f4b92a9af2ee3a27b1d94c7e994c4492c", "c320f0452a8627af1c84c14edceaf31d91330a1bfa4b95d9fcae422715fd699c", "c348b494abf71d580ccd7802dab971987430447a7fac11ee91481fe8cc022a88",
    "c3650a7da84671adfd038bc1fa488c9fcefdb233df4a45eb0d883334e4909544", "c3889d49b86c37063b4d784334e3a8ce6afe38232b6e08ba606c4eff143f2847", "c3cab47e2fe22b1e75558aa45ebddad3f175528a8a993038d42b79e08c1f2ffb", "c40be717a353e1572c5d669d67130860c6e889b60b14dea33ca05cafbb3ed882",
    "c413241e6dd8274483872c04cdf0e348d62a541e906ed67ea1eba300df1e94ed", "c4186e189d2421a2809a2ea0989d855dc8262817295a5b0564c9e34df8374b1e", "c43c1f037b3761389126830ff7b56154d88b43e2fb843fdbe66039f2b2c38e68", "c450f383e33f494e978156724b6b3ea68325dd856dd56679b4203a67016e85f3",
    "c4961d2b51a57646aba989202b073427bea2ff2783456e7798c77b77e7834242", "c4c58ca49b15902422e20a9bbe84e2d0d2914dcbfe0cb2225f854bd0cdd43433", "c4c8aa5440339193a76e31487194d80b45725450f4c63589b4310c2452d6b9d3", "c4e38792b8ab32270fca5c48ca4d39a297bab162d5ecca01a80b7a7c8bd721f0",
    "c4ea160d3c4f6939d0205e4ed0778b246fa02bc12c9b639cda58ddf59382453b", "c50e3bc8cc6521884f03af6f36b226b41a878c7b67a788647a457d2566c3c9a5", "c5183da2a40cd55fdd15fded114843027ab8fd860fa9cae12d43db5f5ebba397", "c51eac213560adebe3aba5a07ff6dbe0df0784b842470cc15201a036beef8e30",
    "c561b9fec0e90bd545ca28737a49a3b19093be92fbca4fe766fe3078d32a5fcc", "c57e3d9e0aa9fb80798b5505f99d762970bef7024b602ee0ac267625a502b80a", "c592313abc5b2afff98c4aa8a535a18663b15509a8381951ec5c5cbcb9b3986b", "c5a26a51c23fba0d9229e4e46901355156184816f557bf889a2078c7d38b6894",
    "c5b0b3e919d5afa6d6f4e39bdcc7750696dd17be0b72df1e1fb247598e42e0ea", "c5e4e53b40fc4486f846c09bcc396b86453a38e663e29bd1b20e6870a13a1211", "c602954db68df919a851582656d865023e07a113384a5449ece4cea58432549f", "c60343d68b6e5a3a1a0fb51ad2ef4d5d3fc3540203c6d6923df56ab42c509d31",
    "c63c5553690563826d8b26a6ba2a2c918b87a8a3f5173ccedac53ef4c837f743", "c648bebecc7cacaa746d12d4b533ba5d9f93475d175137978113564e22496a19", "c65d28eb5915ce76871879bedb98a005cb5aa7db5ebea5c02ef4bdfbe098e5df", "c661767605cbf4bb78bbdc2b8ce1559569e3252c89589f74cf441dddf06be4ff",
    "c6b95ebb4b6420db26a75089d55c98273e273341e1d9837496356ae27951fe51", "c6c42beb84152da229e0ce875e75d99ed703b33759b35009f38e2facb0427c6f", "c6c5a1057e5f1e843eed3f82b8bc670f6e93f2ee80bbb11bdc4f4080359cbcab", "c6ef430dfb2d9b18fc9b44ffbf22765bf7c5cae972f9a2adca7260eb7a3a26b4",
    "c6fe5fc4efa807eea8a3d353545ac23a0393338e4fdc6d1e1a1d97809e6e1656", "c716124b70e3dedb53adb9341610ddb185b1574e018fe2157a9172e2cdc956cd", "c74e4cc095a81fdc3f3373962b3d8073e6b8f04a909b372f2e3402970c2ffdd7", "c75e782e0804a12871ee6d6e5dae87fb4902946bc8502ce3a2e7ac0b8e3151e6",
    "c7624d5f7d8be3dcb07a8e1fe848719094f45bbd1ea8a4ae11e1b5c3b7979f4f", "c76d75018716b542cd5ff6630fce7121d6a1474042ee83bce9bddf153aecc64a", "c7ac11464706f1e9ceccad02f88a3ae3b08fd06b32d2a5279145952b69587694", "c7ba69aedd1e68583fc3685564b80e4776daadc65b5a2c200154605209fa9d5b",
    "c7cd59804382b51c97a112c61c660a54220dd8199496ff0df883933b955d4d3c", "c7d4012e2ead6b37b3f16894dde1d3de69991e91f8906d22e30f7b2d5f027e31", "c7db7c59e8569454f03939435b06eb702fc6cd7ffdda1a7e4dff251210a43f5f", "c7e67568df2fc19f64cf3634858f121a5e060599d24ec184c141ccc44dcaf884",
    "c7f85da2087aca2365e97c7a7a1b3c9cfd70b60a42caa780bc1a05ab576e12fa", "c81b11414e9750642faf5132d7aa99e9b202fff2f0300301500fd0c42f8ff01a", "c81d06c45281e0447952510d5d331520ed83a613b616e477ef206a4bdfedc288", "c824fc617277b976cca0762a586d7534ac18e714f9e083f5f796cd82f22f174e",
    "c829dd391123c66c31ad69703f890343df5ae269c504eacee64b5e78d381a51a", "c87a6972fa465138b78f7d3eb0dbe4e9ced75b2884f6df55388a6f372ebafc12", "c8d16d4dc959ad99daf36f139f269fb391c9016e3d8c01069f8d14c0ee1a5df6", "c8e2428cb567c6f320886ab77f0caa7e06fc3357e57a4b27885fda520c7a98f1",
    "c9144e27bab40bf9ccdd942db2e37bb6f95c5283e5ce624fa19ba78bf4729e20", "c917bb8baaf6ec1c60c5446aad425b87baa494f19a8c1155c2a36ad27b98d2b2", "c91d3b709c5cc31d3ecd207466fcecca415e7a341c7f5025204344b4185fffa1", "c9238b2dc5a3b810979bced7cb99459beea8bd3a851aa2cbd06afe34e9b1eec5",
    "c92b86f4308bc95bd46448486b11621b80296c63a5aa6c51b0fcd89649f69775", "c94bad145bd646e0baeaf3200ddd2423abade2636e34ab343975b9c81fbf6c09", "c961a4af1b032ec3beb246b7fc34e08842610a6e69cd4135995df3c10435e8a9", "c99b1ebc47360b07b4fef8dbf5c4cc09726b3a6db56a09df3870833b22e847aa",
    "c9d613540d0183d00a656c3c6591b1e47941f9b819d2fc148d72f27b4a763589", "c9fa62c7c362f8d9fba6799e3b7036c4bc786d0116e20d2852085b1dcfa1098f", "ca4d6e5f8eccfba9b5f73101bfbee19a34415ee40583f5dff0467c68c7e3ff8c", "ca69016e2767543e40c22b04a807cd2d0c0520f1f4b3068bb22776813a20fa07",
    "ca7f82e140af6b28fa37fff30f3146e0bb4d018d0124c6443cc5c4d5d35c90b7", "caf39629ff9cd85fffa29e38cc93289493e563f0be43d9f85ad8f51427b47ef2", "cb039e8583dd4a554d0578909fabcc52adee9887b23c9498689c72b1b73a84ba", "cb051aa912687c63408177c2313c65d7c7f076e7db0fb67a2c4eb0691d06143d",
    "cb2878029e0ee33ab1f8354f1db4df033787059339e6535c9251619fc75d50b0", "cb36362ccb77479ae33ffe2d985dbfd41313a6d8f885d55102099a8fd425f063", "cb4b759023913e414af471e6c6936f6efadf7254ec8911e56655a1089dc26817", "cb56cc21d8d58d0170d7b89d78811d3fc5fc767040a9fbadfa3c3f75db0b50c9",
    "cb63e8123855d73acdce753e365fc17e3c96f3e3f9d662890bd7d48eb2efe49d", "cb8d66d5ceefc06626bf2365e95bd83e854da2f6446119c658c96d4f5a6b5b67", "cbada444164dc22f0adb4d0ebd417a379a6e87886724246f18000bb9f6fce27e", "cbc7c53aae641cfc603fd13cc0b979cbdda8b481840a8161963531e13f46ad7f",
    "cbd7f8f168bb7ecd6fe98bab361a37e81a28cdfd204b3c8c8c239951e505ea37", "cbdbdac36a18610881fa90357a0cea7f5384ef045f345e1b085940f60948df54", "cbec473227323a198d84d8f0ae51af79e6377ef13f42876e0fb601d10ee31e59", "cc140321ab83b09148428295d35f061ee6268f71e781580e05604e61264e3ad2",
    "cc48ac492751f548c970cb3ec618c1a33d61ca8359ebcc191469c817a37289f4", "ccbc517a72b57db42db78f9c2485f0e07763b2401d589de3a035c3c6a0844d35", "cccbfd458eb6155d60dca220c96fec6a2382b045a1e32517d827db68d67a0bc7", "cce3de7030fe3dfe2bc801aba763d5b073ef0337a756bcfa2e0fd07ef9d4b27b",
    "cce9349713522ed118fb01131271446581d8c05b4f8b570bc0dfac37b76691e8", "cd6b16e7ca174b454bf8c88ed2204e42bdf2601195127987cf3084f90430e22f", "cdadce21d459f6f09365cfc04fdf0b1de20c172081ebf257d2b62cb62e217eca",
    "cdcc8f2c9e74fb7e6dc0bbefc0acd8367386769b5f32afcf8ad48dc90c39fef3", "cdcd9565f04bb817475ba6dc12a620ebbe6585bc63350cc2ce013f535d43352c", "cdd2cbaead1897c88261e11a3b8c43c091da6ac37d8631c7dcff9d635bd47146", "cde8ad8a19dbcf68bb098e314bc256ea3ea4a440382da539e952439bf032ff7b",
    "ce3cf178a227086169998f55f4cc30735f19ee496ce43057f43f35828278ddbe", "ce55f78de833cfd1a7bac4643fcde0e201330d09a5ece6c748c81bc27e409f7f", "ce7fa32a7daa9c09fe0fb38402d0740da86d454e443f3f696eaaf4bc86f1d1f8", "cee4b660dd95f3cbcbce42cc196845dee4d9653a1af2260656b8faf70de2b86e",
    "cef2fd62a129aea1405e30a28b66dd016ecbf5735ba28ceda4ce6bb5cf73d688", "cf0c9f75f872a8e3cb762cad0eb45b519b5a998ae7fb3a3e714f806c3a10aa13", "cf21ab38ad57a72b73bd6bca72532c073a16ca20ef449853c1579e4b5f7e531f", "cf3bdc1724e587ade9871ca23d4557bfe4a42a45803620c7e1e9fc668fb3bbaa",
    "cf3f37b99e930e71f5d4196cdd99bfc97c1bdd096c8ba556eed464227c06408d", "cf64c9e99e8b10aaa08c093017aac177648489c0aa14ecad90ebce9fd571daa3", "cf74d96256db8228e06cdabc9e94a36421e11e242daacbbc8c03e3061a04b731",
    "cf8aea73d70b0438b0886a7ca390890c4ded5e2c4328ebc3e932f85a68678693", "cfafce63b3efa47b81b650693324786053317f46ad8e69b73bf0c59d35c46f13", "cfb9df535fae63144e44890f9ccb884de60f5e5ad2124e26d99bfc03699f0da1", "cfee87fc053dff2f383fee3d79cddfe33e98ea29d6268c716103e627213c7633",
    "d03a3f2141a64f4cae75559184045408e4dab773ca07007e37dd44b9623ff687", "d06b144b3ba5ef0a9e862b493f44b2a86982ce049f4662ef106bd736800c0a6d", "d08a7be89c6ce56c4e749db050f4d1b11f83f0cf662512fa58334594fc22ba56", "d0a7f25f9436fa1a9016019ac7e5802a1ee52ec0e42284d86ce4fd4aa7223957",
    "d0c57909fdae18c43e9fc2333c73cd6f7b9dd653ecdcf1aad1ccee5559745f84", "d0d3efa09f3edebfc4aaf088fe4f4d2c942b95967e6f4d13b7d80f4eed31ed62", "d0e6429e97b5cd170a903131db8e5d23f0fe53d9d441b718f43ee25f6637dec9", "d0f3cc8cde00136a4e3be1a5049feffdabd7163c3f6a5326cf427db3264b1e7d",
    "d12bd1f15c2882cac5ecea835d2eb7ed6bb2b57ca179a3e2f050b995f62da2ac", "d181df97eaa5a52d278561fa9787ce9061b805a2f50859ab273536f0ca783f4c", "d1a88ffce4e02f547824436d002d1f0ac25e850891d3cf5b66c6d2183c766aed", "d1ed432c723bd3d2a2d57d4116b65ba7e6b797d21b8303fc2be34e99f211aa36",
    "d226e0c355b82d6366b66f199363a9e58760b0bba5915d829ec454e92d27f509", "d246abb7f61bed7ef681c1eff779edb3a257985de9cb28a0fd4518a13749956a", "d251681af5d3f8245ac59da7596d021b38fc790d5d186e3834391e8f131b21c8",
    "d287183c5e18477bbb77cd9e1a044bcad5524f4592bfd0547f0f37e68dc48566", "d28da879895dc3aea82f4164cb62a097ff49266d65c55d4d189aea8ca146f7c2", "d29241ae52220113d78ca5f67ecb64e46acf7e5a24fe3c4fe8b4c1caf55e5ab1", "d29291ad5bf5e4de623621793e355fe624fca50778c612016d47243ec3c198b7",
    "d29b44bae85b4592e116da81e0cd05fcaf6630a2d0828de793223ef077886ac7", "d2dd186c3a86303f9e5f0ef7e12490e98ade8bb12a42992c3369e52cc542527e", "d2ef5dfe3d6d302f033aa22ccc62d6c67e9bffb8ca7ac5563c509521dab8f5b6", "d327b0be74fa072d8ab410ff88691a3865e1abac2e1ef7e48a221e8a649bc4f9",
    "d33abf1472f9149eb9d8d9ee9ec7dc5b2b7e172810393d4068adc3c8b37def2f", "d34fe054ddaa20ac27f19cad1192de00b959e6af747f13c39664c93b50bb8d62", "d3c1887c8ced9e8a31dc5fc10d10425cb555a522fd7861edf190b0c3ed11e18e", "d3e3e453d9be47d1a2dca7440b3a30c1b634f034656539cf490c57e3676a2fb3",
    "d3e6be2d9363d962daaa94acd165f8fa40743ef866d2a49de5b44660541313b6", "d3e8e01a067829b11eac50b2bf6190419108da065e1b486556567958b858fd0a", "d3f8d65f3e132ba132656bf16c71cdf5ca70ec302251ccc1cc41af1123eaed58", "d4607866a6dc58fcc78c6b2f64ecff5664617d281c152157075f4e2bf77dd32a",
    "d476cb48515d84e1e891c841d0c916a285421b72eadb53feb6dc6b5f8c87d050", "d48c5f8da51f202e599dcb0523254d6c33859e1ea3ebe1c04e89c7cf8969c3be", "d49494a4f7fdb081d99801e5ed862c681671fce9929c9c13ebdeb07f67cbf989", "d49d48872363a4e2a5cc6332db8cd6baa707db828d6cf60f0654780cac6be1a6",
    "d49dac14ff0e5c71f670d5a45a9c0c6c517963bf12d6556e73ae968a8af4209c", "d4afe55e1e95f31fed8bc6b585680dc7f0e022cceb54cd959fcc6cdefe42924b", "d51b9efe96a23db8d8e87c91d2fc691f906661e3263ada7fd95eaf45a3ce6f8d", "d51deebd6bf347a4a7edcce8ed3ff8cf1bb80192fee7b19392e78bc045829243",
    "d53a630b396a33502d824d16cafbd9f4146f9da46a435b96c972f2ed4f5d19f7", "d556dcc72aca178529a19b265874c1740291361ce5408a52f338f7b091e33bb9", "d55797120e302c5394a60743cc5e47a13051c1cf0dec8d6ed2ca83a56606be3b", "d56db5f348a73a6eeb27e46fc5c53c763fecf98fe2fce24676404e26ec6ef88b",
    "d57b7cc9a7db0918cd9bffeabce31eb71f55dfde7f5a7368cde057ab6f582b8d", "d57eafe07a44c390bd28787db844f1cedd7cc3af7d0b8045c1d0572fa6de0921", "d589e57f9b47cef80417b3de3f00e7d3d10ec3323c587c721e001a1e6e6cc775", "d59cffe4d7a29b20934dd71b982c5e026ee1e089b2ab04ffcd9c44aa23ef4947",
    "d5a6ffa3a4b98fe93cbe8c645da49ea408ddc4c058ad4b7a1ee433a50a583885", "d5ba06e3ce531e8e56c670816d4d565fd613343ba3de4061dac04dbc2a22be80", "d5d14f8d43ac6a90e529c39cc2b25da0afa64bd7e8be3c3474f3366d24e67424", "d60456d0879b07478e3cf66241a49704849d549e03567f7b8945a9aff8291a9b",
    "d606b3ed350dc1da20b7c0b77a335fc7e9031d413cb5e668fd2b33df3dc2ed01", "d61231c4a2a6c4d00f0783a7406a341296a51fec18f884a1fff4a3af225c37bb", "d649bbc179c26d6fe75f1272a71ffbe4a7dc8a1aec494add140dccf8eeb58cd4", "d64cd4f7c8a9ace0be306c2c2fbe9ef99bd0ad965c8ed60151cdb014faee306d",
    "d6744d6f0af36b58fad293420f98c2b761d46d2681251340b2dc507989ab7f75", "d6807f00a250aeb27cafc9de8b577c4b06c2513690b2e102613af31f4597ed6d", "d69dbf540fed3585ab467d1e7aeb479086d3eccf0cef7ab4527f7a7612fe2b7e", "d6ed1974a2c517e2ada416c1454ade9675a513f5fa287d78b26daa7a56e0d314",
    "d6f8aead65425778e4843f2bb754370940644bbf8eb61e5e76383a32d40d25e0", "d7092f308d43c2d36a9b30700822fa824f6225e5249b2a780c29c471e90d0750", "d70a18f0c3168ad79e2e22c1bc87d370016b35ce924575f7632a4838409e09fb", "d78ad0b3cd414ece90a92915ce0a2f20bcd2de17dd866f852f37414371948105",
    "d7ab0d41c0625ff44de06bc702a1da9ad4b8f76828953679396be6e70df78296", "d7d69baa1c363ec8ecb9707b1b2cf5490da919a3c1733aba2cd25284499d8bf3", "d7e075674ded3eae4ea7157155a42c7fe7a0a30861c56709f6f02ec2d145783d", "d8118257c03dda87e357186db1da732d0fc56fc68d9334dae9671e2f07f52745",
    "d81d169cd361660672063e1899a250f41216c9b7f1458f3635f0adeef8da9a84", "d89e635f92d153b95f73b08ee8bc7a92c9967e46a0066f705f6e9bc50c835d0d", "d8a22b8c3775096a118795598cce38468c15810785f5d8a6a6abba325dc5e3ac", "d8f0ac30d2adeb500409d392b51d3a22a0cf38d976a551ce0773305e82e54a3c",
    "d934514dfff8949fc5dacce38861d31500482f8671e5764486715b0e296c5acb", "d9715fe05193c9566c239abeb1a49e946e582bbbe35ff0945560914f3ea4a123", "d9766fe64d4f092fa5b39396b32df202ab560ce5d67b08e0d93c2dfa9f8a6ace", "d97fba6eade67a1c43d5a5675299e0c14ed9fc39b838f04e6755a92c55c8fa8f",
    "d99f4e203b035e8614c304bdce1acb23215091fb4bd8247f521ad488ba0832b0", "d9a056f38a6dfc735da467c23ab775f16a521ff3b3597fc11afd52a6d15672c6", "d9c0ffd024151cc0cd0e3a1c32fbbcd8e54c4f4cb9d9ccf9c75908b3cbeafec2", "d9d2a9ee2c33b5fa026f398ca7973f637c9ce8d25b42ee75c93dd308641902b6",
    "d9d56ac848d35a9f986ecab95fd94586428b467f5f95d17cb488afcdf4ded0da", "d9e38e85f9cb90562879aa4ba6faf19d89abe8f1a7b041f386fa94d8761d7a58", "da14bb0109aca5b33cb37f2390c729538fc8f6f92856357bedf2bbb475a47e94", "da160afad483f12e0a4a7aaa2df05df526a1f9be23a7ebe8ddce9dfdcd2696cc",
    "da252af140c594e6c975c26b3b43530c50ddf3d811d0792dcbe534a64c527b78", "da27f9eaa962ad9e5c5676502ded16e2305432df8510b393117ce7141419910a", "da3975a7d87fd831e428539cf7ecdf36d0e12977c3ff875e64a71c0692faffb8", "da56d48b652eddc4afc09da6f882320b91c5e68f4f6d3c756112a7585281f15d",
    "da5f7ffbddef702488c0da25d97de3f778a71b48fc981aa0dc6d2388b28459f1", "da663857cacf908a9f030c819ad986189b201b3c1165e6b4f0c732269a330870", "daabdd774a113f10a42c3e51ba8eb0670807f96a60e19ac857d7c07fc36018c4", "dab70bf32f80930fb8316078f1afe08c4865b55fcb1c6b935f5b9de6b2942b94",
    "dad32bd02581f0ee0997917110713c1720eff962496089480dd8860cbe7db132", "dad889ec8e905914416e8ab87b7f8bb229419d6e7457e0ccb06b74e7c778565c", "db40b1b6b4bee37bb6986257607fb26f348a0d07339f50794e3350c7900106dd", "db4945d5236a392dbde1b245b81aa22434f03483a00363d40de5f7b33492cbd8",
    "db4fac2e5e64db6779653ec59cfe08f2a9527133fb59fa7a7f4b07c2f550c4ee", "db94e5e698413ee82c6286abf4df649b0d1db59f02d8031e575bc99e744462ff", "db9c57b693b7a2b3ba212f6d396e71bf5b14907eda4d688665fa1b415fa33b56", "dba7d28b8b10a87fac855a4279dc65f8d39dc6e32fe4f45432b1e0040f311368",
    "dbdbd7d0554eac1e5d56827cc8698330ade02bd97ff05e0406ef1c8e8a5e1774", "dbe6e7b1ac610d314272e6f955a1346223af5a470b75250135fa540a54cc8d09", "dbf2522424a42e8f59dfe4dec4636daf04d2a7cb59dbcb3b04ea79c552b0384e", "dc14f39211bf3b38bae903927c6543188b9dce6566a394c9c80ec98c2f05d10d",
    "dc26335fea693efc99d7b29fdcb82af7d0fd34cdcae06b19e28111e47c425958", "dc359c24ee752e71aa2d22f5824b92d6fb44e236766e9dc4f10922556ae25ee7", "dc3f61025faaa37d4dfcde0d0c855a210807ceb2f64870c2aa1644c5f817234d", "dc5d27edcd36f9776df81ed22b78749f33390919ba3b528f728034da0bd8a394",
    "dcdfb391c05a84f756bf1b4da82d3a962ca78d2550658973b0b8fe62811554d9", "dd1b87aafee466654d2dc4d43ac7e67a7ed05ddbf69373d2ac7efa25cbc6e3ec", "dd1ee3c29d40eb9b9d109ddded66d40a15815faa2c6e4351c3cddd9493c32177", "dd39fd961447c547a8635a48f81cc98b7ac8420950caedb98723279a185c51c8",
    "dd3ddd24abb959598457b70003f2eca5f451af59d1bb1a63c90983860563a5b9", "ddb54a55db9b8633377ec2d011a00926c824a5e94ec03275119a7920c7b92f64", "ddc1495cf84c65ebf6bb7061046ad37d3a4a52f777c1f7d6877fbd606af8b00a", "ddcc07041365f38ccf02f036a28115af2ee4024128d739a727481eae102e770c",
    "ddcc312ba8bef6e87d9aba9b589f2016aefa282748dbffbda8633de0a384ad66", "ddd49ce4dd7e4a1d3269230648c7fcbf3543fc6bce16c14e0ac84f0b25766ef6", "dde96cb3611369ef04261ecc2268e23836815b6887e69646d79f2d7838c30046", "ddfa94199e57500c9cba74e685e2af33dac05331362499575f029c3ed3cd58b3",
    "de15d41a14badfca8de3193d54e5f92b68deb21e9bf3f06eb2945c5b5aba67c5", "de25f45744b45b13dee34502555415d42247db97771214d85bc151b12c6deecc", "de28ef1b7de425b7d7da5fc4ac0598f57bf66c64ce28e7449cfc9cd8cefb0186", "de312dbbb1c739d510576405c25bd6f218abd6b8efab835a20c8b61ddca59e66",
    "de340af064c04a0b7300f46d60e738570d5b13f86ee04ce91bf72eabd6eed578", "deb78880fc4377cf6a0b47c3c64c49c3880be73667ba266531dc53c514a897c7", "dee295849ed5e1dfff4576539d04284b346c5aad724809447673165254f5ff82", "def3b5295eaeca31dfdc1ae66a55d844029820ce358bcdfc2357f920d72c25c0",
    "def6f47f158187848894a6c8d46dfe24e2182177822fa2ccc7bfa74ea03ad734", "def709d25ac9e5afecce198c3fd883e3e61bec5eeee75af869a7fdf04308f9c8", "df26a32e238059e16ea160c71ebb7915f6e52342bde638f9a1865e0230d40ce1", "df485df1acac7a693895a05bc9dd24d8a5e4e376043b3cdacb70fa61021e17e5",
    "df7d160dc33397761f0568c5ef41f2e6563144f5c14a346e56dbc97995403aac", "df8ef477fdba2aa86569c64da440bdc1785d3df3b1b111ea967eb8a395b09a60", "df9857c652ef31602e30e5ed2df8cc393968306431e08b75dc286977bc978453", "df9b5937434d65fdc641cc26733af4369291f5e0a86c0989816b23648566019e",
    "dfe313b98f572f640af3bf364f79887df0de6288a4f35a60b2aad076ff28ed29", "dffcac4658ed4e71d422b25d2ea581a1e7737b05a92313403bf8ae533a5d889f", "e023b2d074b367e366175acfecfc39d8268a3afb2bbd2af395c9a8f95c6f7864", "e0338ab44f66cb7f56abe2ee7c36b84b3963022940313a42d0bed07d337bbd9f",
    "e0694bbd9544a8a04ac61d0e3eb861c51d9da71c593c6cef252615af413560c8", "e076234e9992110edf57c68e77f6859c5c511a0c4feceeac45865664a943d527", "e086326ece557126e739212dfd29e27e4b102ed4e71a779df961730742e8048e", "e0ebfeba871fbcc06f66049572dd85489c5c89ffc8b416912ccdde3d2fd25f19",
    "e10d865225ccd88ede3ec95cae67ef0ced06aaa169e3afa9390239c6e369af38", "e1399b527152e8e40541ecb3d9800210b330247bfe8b43ef83af2b8ce9f7e0b8", "e16dc3ea014632acc72b33433e8ba6a3d99af0c98b6d023678eab7c3c311af44", "e183fb7ed6c1d43d9d859c10cc69803278a0e32b0888caf673d04551bb8a31ab",
    "e1a8a29b6cb2516908d098a883a9f0522b0a30b5c0713a1ee31e68e88298fbf4", "e1ad9f4b6dd311f43ce80cb8b6d9fd52ba07437cb8c35582a38a629300620d2a", "e1b4296dcdd3b0bc846300ff6685bc5050e9db4f430588f5acb3f15ebe78eb4a", "e1c1ae232d2c8905dcaad5b2093131024988beff4fd97f64930ab976d31f0977",
    "e1c92f870dba47829903fa0ecc9cf8e3a6e254ea08d2ce96029e029cd67459e5", "e1dfeab310d9be2b3061b3d09c718bd040890b08f48fb397c42970bb3d696793", "e21f4f192e40c4d1424cad9f43a2b34095b271d90064c0f3f7849e162528bc31", "e2222dfa38cda3b09d28c224219f5ea78a688fd5c2ba05b606ecefdecf96cea9",
    "e24b1788d74dcda19a39d3f99c397c07d73d18cd855be4a77ee8a16b4dfd1544", "e24f4bf54f922d03b4ed14beafd32c7c6bdb2b66ed5676b5a0e1e45e396900dd", "e26bac3c001eab540ff782d65a0de66bca22cb86746e9ed2d57b2160ca845e9e", "e2888fe4f467689fb9f3df43fe333997a53309f37f1092a536c70358bf157b10",
    "e28db5fcaa5f725f974f46a00e8f1bc80775befca9cda51c235f4239685f8af0", "e2ca1c9f7499aa02eb4eeced48788915340d747e474bb574c9bfb769a892d837", "e2d35fe07da05265cff5a5cd87ea90e96e94647b1d1009f370ddc8e978bfa125", "e2e5400af8670a91afb929daefca942908a22758d8a94ffc32c00281dd2e3176",
    "e310852388d513448fb781aa780b7a0c46d9d8cade17c130fccd5643f1d460ed", "e31e6f4484302fa673184f82613a6b05fd1a181c30e4799ec536f9221a19e397", "e328067b696fffd3850e16c36cbb4543fbfcee108991272c38bfcf1017b57ad2", "e330f81a3d0e10bdf33fb95341d8a2ca08a77bbed236a898ac49a25c7c1230bd",
    "e3a34e5586b824b7be64e9bed08743d0bbccab46fd61c0dc53ebcc5e1f7c0c9d", "e3f0c4c1b2e1bcba16ad3342b76caf50a9eb6dcafcea632d8779a1b8029520de", "e3f63c978757f7b8584776c355c2e1a9aa778f615d6de3be551a92fba1b7c05a", "e4009980163b7d75426a89733c64f5de5dfbec7677be636aa005571e8f0e6cea",
    "e409a8afb33e530e22d27aa4856811c54d0ed3ef4a9f9370314e1ce2ea057cdb", "e417fc0ed9fc2528136f83a32f63ef9a7b8f471c20afe86f467c939f9c82d87b", "e44d0950db22cf97be568547283f9ab7530851b54a4ef62c6ac774d4c4ee54af", "e467ac9a3a8fcd0750e51bcb6bb08a2710cc75cf0ad1061e05ebb285d93479ea",
    "e47928354527ca30fc0631b2e535e17ea3043584521d98fca7c829b1de1e29ed", "e4b16b6c5443ad77bba2b170529ef1c022517237e2a0e465c239f0a99ff63b0e", "e4ef57b02ccc770baebcadebe1a58b068fee0eb490778045736395fb2e05b41e",
    "e50d74b3e2ecc712097b79aa0da2e36b4ea46ff048dbea7a99bc2ef11e5597ef", "e544114a687d21add99cc49abc9014ae24d64446fd23f557b9cb6cd33a5361cb", "e550de6e02f177e64b5ff07046ca3dacccac54327f6809b7d9f615af2bbd588f", "e576d6507410998cef38569e8013891ecb48573ecacb067eb53c9629cfb94211",
    "e59a4cead6fe73f2e8e160523257a7cfefb905475562e2eb5a92ad05e9be6c21", "e5ad049557e14cfba9ec9f06026987004061802e0bb2e875bff67af7df85ac88", "e5af59973ba1259dab032086316978420a712fe59293858dd5e3da97acc36c38", "e5b2be168b0894fb83a87db7418e995e3e36679a1c0491ac8d9050be4668e505",
    "e5f112f96e08c9d10711832817f62af1c1e8647482bb7ed71a58c801bc8976c3", "e6090708cf3d9ba041f9d3615c7fd55f75e12c4da003f90673cff3e64c92b55a", "e6122126085cd97a6a0e78fcff0f600edffe9c70ffcc939c776f1893ba006def", "e6a51546e92653c9318f58feff5d736e51c70be606e68de5b88f0bc20c76886b",
    "e6bc1bc5606a770a642aa95c2bdd5ae8c39eb4f21ff6e2b8a540b71047ba1a77", "e6c9f8802c6594b7e5bd57b1bbf645f96160d331dfd7399295b083a3d182ada6", "e6dd9d8c369249bdd40f2b3bfdec0f6bf33f0e87a9d1f1786b205fda5ce377c5", "e71f40b3b7d1035e6f6f01d9b11436ceeaf6287d7fc31d7308423eac351ca6c3",
    "e724a8610f9879823dc47466f1bfafd7e3f62c3b4512d055c98bee82de7e01a3", "e747bc7bed1025631be4eff30462421c204db56892e5c1b3e59072c3af43e7f9", "e7c0c21e306029fdc41a4d8b5053742a58d5fca539aee3eb26f2fb9b31f963a6", "e7d07853a694016dd9a28bdee5b4698ead6374725b0be4779a959209630cdb69",
    "e7f9737c9bbc461b8b3918be28fbdc7dfe16c7203ec53df0b713d9614f0af253", "e7fad03621b6be29908de780acd2f20b4a63bad788643170aa8d5e61e540f1ab", "e85b62c0ebcf5f59ef2c831fb4a9282bfa7032a23b865134d9ddfe26cef82558", "e87894aed5fb5b2aa88a2a784c59cecb9323bd69cfebeb5a8c4dce05ca3ce313",
    "e87e379468aa3cfc15d733cfcde3674988f4172cc32d005e10c6d648bebe42f1", "e8a7c13bc9000db6386ea3d1745bb6af9c706efc96d8837de2b3858d581da6e7", "e8b7da9e16c4d890da4f156131e8d97bfc85b9d6546992f0d5c2d7c96de6c58a",
    "e8ce3e269c3edeff118bdb11d0b8133090cff0b681a628bce47df4cebe1f461a", "e8f6a06bd6c4cd09c29aef208966d8039996f6616a92097da4b90cb0ef9d755f", "e8fcd893a915f12277269b7fd3d204df390f40f357a2e69e279d8f6eee12902b", "e911b24b8f1dbfbd83da1b1716b84d6be5318554f6a778971ad4336d3a57e4b5",
    "e92e5f2244b6ea0a6b31558f77b0d9b11c61e4251eebeddd6b06380fba89ff3d", "e93fa6d302088d58d9b87b66b289a4e6ae2f710936a6245655882e4e91694c47", "e9878d27acdb90caa858da94e67552f6bbbc5759ad1075aa89d25f365e67d589", "e9b8a61f01cd8570109713c97fed1cb7179f745dde7801b200f71eb877e2358f",
    "ea04d55cd6f26029f6bb527ed76dc61139d4431dbc6565d83cba3bb6620d0eaf", "ea1b5bdd77de4beabab0e9961d4187d7110834ddaada9ea596ca94f1f23cb14c", "ea6e2ca08f919d0ba8a6b5ab06276d5a369e36d3d28a2824e962d34467c0c59a", "eabe653da0d14347d793f2d5b8f660a2c7260b73b237f9de12a1816ff17a1541",
    "eadf7df7bec078a5bf5eb3f475c008ea2bdbe409a1f066184be481ce20d23a40", "eb10a06d74de17c842cdf61e3b8c5cdc245abf0a69063ceab01c90aa1a71c6cf", "eb5675b94de70f7b7e400f9f4e342be755c9c9e7a534d6ea6621027ef383e8a7", "eb5f1e1798056bbe4d5f502d20d0aa8f718f1e8b089694524bb994d8569e0f6b",
    "ebb43a408c1839a9ca64b4b1fa1bfe2a57bbd17780c059b39e6a636e3d01324c", "ebd22f13401ec46603f2993bc6d88195fe8446985ef6bdcd246ad840c596477b", "ebeb918a40f5a78a56955b43b3c6769543d93d84bd8b9069b41982939b0366e3", "ebf7d33f4306b86c92f044f30ed36f2ed0514981933782ac28dea55815f18e86",
    "ebfcb392ed2108b51aaaf0a743b4b4461824792ad851ad047a508b8a99894379", "ec5e3115dd21e97e1ee628f708c32f6dc8fa2398221cd882e0294bbdc03d6650", "ec946c4a6b00fc6ab019c07abcd7a3bda4e86da9c245d8ada8a08f8cd99e9558", "ec97173f8a9775e9e716bcf8f0ca3219ef2a1d5f9907337f376178f9b9d18cfa",
    "ec9f14862b26e8a5497c87000f65b50f28e905570662320a65d3d017014e9317", "ecce6163f9f24c1b5c3ed79c91440f2a03758505087961ae02cee6ac9f154858", "ecd83a92783bcc43433875756823331ba584dba983a960dbce46659aa5e1d3d6", "ed0777c80073118e25fc32948c46cf2c616bf01084978855c04b2a150a4e763d",
    "ed337c3da0cc2fb0ce429be031fcdedcf186124c7e35c9677ee04af40dd146c0", "ed7058dd0f175ab34266394a045861aed8331c1766cf7d38b6723d4088ed70fa", "ed880b4007e4cf929187c1d224a1190061bd4dc818b4d1e1d215f1517b30ea8b", "ed930b59c811c70da066784853058b5d28fdfb871926c52de1258c9bb5dc1c55",
    "edb48a97b6d8ffe7566c5a5f872247d5dea4bc300b61e25fb5c5d324604708b5", "edd3a75ed137865e80fe63f376ea4bd8506e48decaf12cc29f2b6b345ca30fb6", "ede31fc034f5f9a1c9bff16efcc8d8cdebf03d2b7c7db9223d7d1aae221847c8", "ee070beaf931e92f70c8aee47fae0a4ad44d050bdbf92e05274541f0aad1a128",
    "ee41adab5c008a95457ae7d2624a6c46fd0df34fe8672037cb856047767deb5b", "ee5be8a4c5b8d5722598467f95e38459bd9fd84a8d8f3830fd209a73faaab476", "ee74e062aa33232247987805f04ff3038236df049b1e71f4000d695b8bae17af", "ee84215f9f731e9cae5653d53b45bd412228388fc4465b6b19d571ea6c7fac52",
    "eeb371958c1aaf236aac507b2397ee42269b5b531b75beb9c7175461a69ef774", "ef069b6d623b4e0a52e6b2252f635fffd448f693111e333d6446713987cde094", "ef53d35ac51d8e7e880d82addd75aa08213eb199895f8aa982d95185af731bbb", "ef6df2775a651cb2d7577efc0d56d655a88fdf7d0fe381165bffc80aaad2a070",
    "ef8d2cd01a5855dd180f5e791d05ea47d46af22dc7c2d7db256ff59bee246864", "ef8dea7a992b2c5d3988b60ce1a66d00b7b90150823dceaf6e20e5f7038c8de9", "ef99cc6bde1147f16c67ad28aa71e74ada9d326bb15dcd25bf11dc0cb92a5516", "efa38d617023282117ecb490e1a38ed9a7f97b1d6a2848c8a799d68e93736386",
    "efb72417dd3789d582d783f2619e42f7f237f346ddef36c2da9747ec31c781ce", "efdd7f24210823487a6c3fd7dec4f68b25714b87cc3c9d55232a3b5bf0eb4d76", "efe223a642db1a30e029d2f6b0a2057faa473e0fbf3b7ac005dcfbb34a405104", "efed2d746ec8651f16d3d00e7a0498c12593851023f64c9d827697e7a0dea88d",
    "eff315ccf7b2ea94ab380b5ed4a2c77cf7fed9085c1a6952e8826661b983f5f7", "f04029440e381a645b239e9e7a4d436c031dbe0e7a2e389096f2b4f212066008", "f05ccce39b2db38236ca083bf0463bc5b909a9a1e0dad3c40a219cf4f8873cea", "f0b53cdb99f2eb28941e5c9264fbe6eb559d3584bcd3885ebdf1c2e4dd04c801",
    "f0c5a0b93e32a2f05b9ec216c7c05c99015dcd09ba306fb13b350bc82fb024e0", "f0cfaa299ae77c92b6b4f388bea51afdcb5b31e607ca439c9781ad9e656a0e99", "f0d47c28de11f2d1a53c7009ed88911218f772bd7ad3de8b91786332c3b1e144", "f15bea62836ceb8c9b584ae2f955e288444e591d55b2d3098029a5427ac0418e",
    "f165ebc0d3243638650feaa1d221d5903d46950a158db47cc3ed579e39efb4bb", "f1662fe3d46db1fe9cb2a9e8f26dfbb8df3f53b9b971db359e5faf2c88f9556b", "f18b6a03d86ed8fe36fb5e6d4031c23a93e0b337ce227090d7b2defebfa53165", "f1995a4fd816ba6f67ca02f72df012e1c027bdb7291adc62693425049ed79067",
    "f1a8dd68af4080f1160403cde6a683c7f4fa0cd92dad818d0b48fdc816e8d068", "f1a91df7f964b9d2c8d1072a648f48489f2d6bc53c1e5a79dc3e6a07280390d9", "f1c8a705d2ea60baab74c071cbc03af5f9aafe8aafeb6de774e80df5cdb00ede", "f1cc5382a5b5f3b0a63c730d93e88fe246556731f3267de17e766f30bf182131",
    "f1eefa83d0fe6e124dfbfe8ff50e6a8f0baca199b4d95e45313c3146d8a53dec", "f268d1349b72877bb89757aed43d54795f6064cddd4d5245a71a3652d842bf8c", "f28be1caee2eb521ac3b2b15a1276ed9695ef28e538f833038138705f58ff0de",
    "f2cff1ca4a02ba613901ef4eae4b4a23da0bfabe45422344adcab3dbf3be3596", "f2da9a449cf9c0157df7c280459cdf5350743438bf27675cec34c2401981f204", "f2f037b42d4892d85856a71a71922dbb52433d00a3d64c1bf64f15ced106033e", "f348292624105034d64a1674b99c8d3ec9c9eb76a7f20f2c4a2b18e4568a0af6",
    "f3497468afdf60a9f2aced35f9f4d1564c17e3fcd2da23947e2e6748bab03608", "f355ba82e3a80f4be85e846f89b8671dada9b391087a518866a5334817426736", "f367d53d51a62df6e9920cb414cb6555b0eb9d805cea3834903b177d5a396c64", "f371ffddd3f1ff4d062648752f012f4580c4bf752c9b3a093265ed1ba6d4f610",
    "f37a1f4d0620c5d6cc65839b6b876e687dcb2bc86761ffaa499c80d67378e2d7", "f38665d604472e83cc1ec6a1356b485b13a86eec7b954a559f09f91a81b00e33", "f3cc5b0602555635dee17f2cb9b45b72a9016fc0e3fc58a3d823e04020830b99", "f3ee83226e0dd07b2d2f57d19119d2bb224d064b06c1ce75ac237166f7a894e5",
    "f4078963191cb43990eae94a93f6b0f9b146080ee54b91d1252397378fe0da6a", "f41ed30e39678ee5d195cd345b698f34d7286ba3def707e69688481bed9d9e70", "f421ace968d8fa78327cb1fee25d88c0b7f75f218c6fcdc4f36f28151ff7ee4a", "f45a7afbb7ca0f0273e3f101ab1f7a0eb160b840dcb2a8b0404e483b5f9ea491",
    "f4625e08a450ba582abc249944701cb819d90a94078c6273faf25f2d922029e9", "f488ffefdbd6cd8cca566415ef8111cb86164a29c1e8d8fb58bf8e1d7803eaab", "f492fc9d1a498905bbbf7a8fb46fdbef6c205d84df9473bde17299feef305d47", "f4ca7b44969ed409a78bd00449677ebbed30a6315b45aeefc1e7c635c9b09351",
    "f4f9fd2c4cbbf248756edbf6b0b2992674ee78b3aaf70033dac942f970bad2d5", "f4fab2399058baf4bbbc2f25d10b2273b787e91383fe01cc7ae29879925cab07", "f4fe383480a27748ed0c43cabbc5a3cee6def4f083638cc88cb95ef7994b1f1c", "f5366b519f5b5b6f2a2686ca809414fe0869b60c969c874dc3a935bc6d320d29",
    "f53c94defbb1d374861e904673f71418ddd0fa63360d0491f65695e05aa05acb", "f578100f63e20ed9ca605ef6e0285a2a6e8d8ec8ef6c36e76f8655cfd9de8150", "f5badb57e9f4e8ebe05dbce21c0a7987ab6c4f780af0380df19e30517bede733", "f5c4d7eb5572d825b1a5e314308b1fd9f7fccb01d4decd4cbd1e7b4f6826964d",
    "f5cc6b71daed61143b62b51ee14749bedd311827cc2d09291fa098004c2699c9", "f5dafe608891c1ba97b06065b8283c06a958ca747d4aa8c87065faa54024fd76", "f5e7ea1450c48a5848222bbf69a8b193a1aa3e606d354d2202ab241d1e9e77b1", "f5f8a8a06bba64e389aaadbc2aa0f7397d682ed414a30339f3507bfd0fec9aa7",
    "f6589c17b6d259ee55e5f200f3f1eb02ddb2924d501a62565b0401ce56cecdb2", "f698713ccec2c8f98a9c8ce8a06debb55deb2f276703018bae930236d6027709", "f69c8023ed102f8965f43ccd4adfc0556dd9a1d47409f8e7c36a76c3fb39c5d8", "f6b4550104983fd8f984314a018a03fad08f9fa3c6a6e0bbb99a37badd9daa9b",
    "f6b990df2e5b311735bd3762c48766ab4b991f76d4c8e32322a3fbdce529dbcd", "f6cb364573bd0ab74c3cac923705be0a48a604d5f8d9b172168c9f15d2d36ef2", "f6d3397f74e514a2651cc063ca410b256390727795c922bef27d5595caeb587d", "f72754edc3b9a669d61f0e6c485760c46cdca66e7b2fb76ca1737441c41ddd1d",
    "f7402b19e1250b91a3bf3ee0ff5f2740aa643e11d40344ca10fb428fe8f7ce04", "f740780969a728264f093569c5f0169a2594e51fd9de8bbac2ef8a926bc07f7f", "f742202d40e0460fdd0fdc121e8b9cf467a6a3e0914f5009bc35a7bad4cdd721", "f78198c854aea595e559b0c7f374cb80f9dee7c22050e00c0322788d2245bd0a",
    "f7c97693a626ce3605382e7ee7dee9772e74d9dcd4de2fe72fff8b84fa6d476e", "f7f69b0ed4e24071dfaa9335dd262efde09429409df40164b579a2a9413e128e", "f7f9102f09b0cf917cb25de2e70692cf9eeef26c927e8923f442adb925f5595e", "f7fcb4fc802eb145e2b4e92e8c8ef494b3de1dc5aa94672993a292df65a5ce40",
    "f8242b8b67b31fef623dd2f660f52ee8a344d99f19c7dac3c92bc91cb2e09e00", "f83033cacb8b398ac7b74b778330b0f02cbd35d349ef664509952d25b47e76a9", "f8400b6d041dfa606d8fb4c34ba388a2015dfa064fe07147e24e37334d8fe7df", "f8430366406aceef71200650af39578f247b0abe468508b5782b72fdb4e1f40e",
    "f847813beca482500849fe59cf322363c483445393dbbec6f8cb72bfacd81581", "f862d53b41e44945007948126e68f1db2937b8207c8066a96dc8e0f43497446a", "f88e3838fde25f6d7f75a5f332d30dc9d150deb209e5ec718756189f158b5689", "f8a1bafa476b14ef249edb81ae52e337a68f76711af2afa6d11d60d005995c47",
    "f8eca02b3d4672b2f695b4b41cdb02f51030bb39a4efc673649fd35d471a5c4d", "f95010e84cfc540005bca45b93a0c434dd0602ce0c7e61b0825f4330904e5062", "f953d211f652ed0b44c5d6d80ca0cf40ffcebefa74fc3cf62ae6bdf4c9e07462", "f981aaf54ffa9b754b530ae31449a16b441a4b38e4d92f30cdccdc30b2d18f51",
    "f9a012c1e65af98e98f33d959ca91989c9943d07758dd514c31b836857ecd063", "f9ca11704101e731ac588956b4441d05a0dd536933bb974399844c00be591847", "f9ece8c085cbf6dfbace1b7d3b1bcec0ed555fb6ad0d10eecaac47f4ace2c3aa", "f9fb6055add52b782b2641e248eddfd458ab4b739c475f565060c552eabb3e1d",
    "fa03b430e5ced94bfcab15110e1b62a877a1f88b153f9e3688aad3b2f2117c5f", "fa27f189f48c6d4f205031569c4966f66afd9422c81bf52a844ba187bf760ea9", "fa3d8364f8c91cecdee1344b6f6677cfe5793cc00415563ccfdfffc8b7476275", "fa3dc35c71da4e96aedfb6b496505341bd30758ecaa578876d4613beb858c75c",
    "fa96891e09eef05853f18ee1a51ceee644b697b8ca8410033cb52c5b507b3c15", "fab8ff407a3bcb0ad71de466b4478d879dbf16d92bfcb5927f84fa9fa6805e78", "fb0d1473fabcd47d143bdcb22c78c734c82f3a49f8b8b6d8529ea0534c43aef6", "fb249da326c6f8b1203dce9fd09f0676563f570a8830d91c46e26cefdf75fe15",
    "fb34f6e23900cfa6055beb78b9693259de8899f7730829de6ab4184929e1c766", "fb69f95e991bd255bdb29a17bd007f3383c437c7e2243e79993c53879116956b", "fb6e7834c72571175043761637260dc45344b1b74bf33723b129e965b4f4a9d1", "fb78c3e2ed561a4907f409f01667a2c1b3c2199569c57c7cfc6d6491c5797d7d",
    "fb82269acc3d92238bd71e97da5bba25b59bba9e08a461cd5158dcb94368a42f", "fbc36b212ad140ea0bfade69f379d0cdcf6fb3bfc5e009b455df26c38a362a12", "fbe0fbd741d2f6bf4f4492ea3c442f9239429b96e301d08dbe852595fc1806b1", "fbe8fc2576e0edecaedc5002d8ce738514f7ade9eb2990e3af8c634618846005",
    "fc3006c4739e9ebb7429141c547d370b15a1095892d870b66b0532a6b4b8eaf6", "fc58fc30251549e1cb60c3745e3de0cf19530e38dc73ec7450b0ca4b00ed2277", "fc84784f1017d8c8983db42fa788adfe6ca230f7c50b0e0067976541a4764c66", "fca2edc80e31f101bd1fda0df113f44d9b0b8c4c6b1fe61e1d44feb31eea72eb",
    "fd5b70aa1e7ee072c73c96b8ed0f7881fabfa6e3e2fad9206f43af03175886b0", "fd82564c5bf35a96e982d84ae5d479e27067ab4b89b9df68cf0fcdd382befeb7", "fd862f3e929657b739458e04a6c308b5a59df768aeb2ed3497620ef956ab2bd5", "fd8a3f54b63e53cc7a2da8a309d89105604257e39f763da605d459c206cc5b0f",
    "fd8ed3c5b4162a8674900582b90a7d31b83b38af33b80e02098143f4673c0468", "fda0581b83ee6be22b865464f337cdaa234b540712cc90dc672a57d02aad17ba", "fdb62b8e849acdba43055b3077f944f81a38d32babf298dea944f3d97dac1932", "fdc57a9253ac53daa5032de635cff6a20bd9836c3acc5cf42504022f8b35fb3e",
    "fdea7db88c9197f7c9638603597fa61f306c8c2e31e80a0aeaf6a7c1fc78bc18", "fe1879748bb4972acea47739010034f56262b4720f0cf4c8ccb1e51334c6908b", "fe1d433408c0f205a00c632641f4e93517eeb30fb75790259837445ce0313317", "fe24fa0501a682d5d69e45204d5d7faa396c37833968b5b5b17c1062506ae392",
    "fe4883f5435314be4434b8cf6aa65c0356423eaf54f6395d164a95128ba3fbd6", "fea7ebced8ab21de0a36b548c8fac955a213a54cd4a70ed32ce903cf4e02d803", "feb53756567b8c394bf4ff2440d3d7ee9dcc2d090dad75a1141e34292e360eef", "febac57d6e2eb94f35588049ba1e858c5d1f2af42ad89378e485530c5a40b4e3",
    "febd205c63c462f425294bdb83966c9deb18d9dce61c5353f96147e51312f80e", "fed3367dbc78d5b1d0a70d840e7054ef3df6b697bea260cf5f86fdcf4056e9a9", "fedce96d87faf2772e36776cd95643ef42abba533bc791e74c2d01b7d410fe41", "ff6b29bd653dc7c39f44f68a38dc31a5ccfc6e3325837ea54ca736e31819fd84",
    "ffdf3d43d0390f4a40154d1ce38b0c01ca3639727925e28109f4a65b25365c76", "fffd8fb48a0da513a4548172cac9ee6da4e8c70e237eef4bc23efa7bf78f0f47",
})


# Every legacy fingerprint has multiplicity one unless listed here.  The
# overrides freeze the few intentional duplicate AST sites; an extra copy is
# therefore a census failure rather than borrowed authority.
VALUE_BOUND_EXECUTABLE_FINGERPRINT_MULTIPLICITY: Mapping[str, int] = {
    '8ec5fd80ce08f35646ea5c46c08ea4b84df5c2c7e0ad807bdf3466c52db22fe2': 2,
    'b4371e5e2e65944bee08a7bba8826dd9692d1842090d14f151027b22398cd22f': 2,
    'f1de99566f1bb789d306eba1f3af06cb5e9d3d3ef58f26aa0a27484eea5499e6': 2,
}

VALUE_BOUND_TECHNICAL_FINGERPRINT_MULTIPLICITY: Mapping[str, int] = {
}

VALUE_BOUND_INLINE_FINGERPRINT_MULTIPLICITY: Mapping[str, int] = {
    'fd0529d429e040e925e65ab65c20e37919b2cbc9877c73fe02318f40534363e9': 2,
    'fb14cd683cc96ed1a913b78a797d0f3068ac358bb450093e6d8670415c93deda': 2,
    '0116795a8cef104bdd1080b4e96d5f4607fc83b8c6146cb6f2fa1ae8fca38431': 2,
    '07fbf20e2ad8f6125dfffa5446ce34845404420074206550f587c99cdda8cc32': 2,
    '082ac71cee3535db11ee8cb61cdd39d2c81246a52b76ac211d659530e0b408d4': 2,
    '0f42c425a50f3acedb84bc0e2e1daae827f59c0c1f1aa9748a7a81525043ea85': 2,
    '154a662cb41e9e826825ebd560ff1fb2d7121468807ae213a958691f5c8aa780': 3,
    '173ead6218902bd859f0256f7511a5e135d5e7aead2a5d78dbea5cb1f8e39ecd': 2,
    '18939295b5e6258ad82b7db7d183fbd56207d54e3e6adeb26490af33df3ca3e6': 2,
    '18f39867f05e4a035faa2fa8caefdd4cae36857b966732b63b30e05436434faf': 2,
    '1c42af4d13f03f76c4ee9e2918c9ff9a44c25d715fdf9eaf0e8c21deadbdf570': 2,
    '1fe411c3fb5ba573fa7bd9103c0cd722a256b02811eaa6dc85d8fd72c83585b3': 2,
    '2065b7a43eeb3f261384cee88c888dfc97958c3233cb1aa8eab6453fcf96cc39': 2,
    '21f85d92889b0a6c7d947dcaee2063d2050b0b6a580044759ec98df4e31821e5': 2,
    '267854688ec7a4fdf209a73e47c98d563aba306da9c90a5e3733d7147cf1ca6e': 2,
    '2db185a2c565ee12d1f9bbbe7701b64c99f244275875c558f56c845abddcf6e4': 2,
    '38a1facd4372f00010a2f2764fd111fd3331a21aadf9653b9283971ea6ab3444': 2,
    '39a671b30fa20835759edffca7b22f37e91916b4ed02b2c384009091d8bc0ac5': 2,
    '406e4b78f6ded6c8d2ce9164b16e1a674db6e66dbfbc8d6a2f4068eb75c9eb25': 2,
    '4655c2ec311c4a1aa57e4bb947d3024e416eb17aec177319c5117db5a5a6fe61': 2,
    '4d80588cd6edc7acde318454862c2dc5efd3c22e5e6f47431487d8589300b746': 3,
    '4dde4ca22f77860bd52385aa155e45e921dc4df5215f0f0309936a2213bc810c': 2,
    '4fa5a2a689f0867764119664627b154331c5b778849b64f02c1bbb6f00d9f485': 2,
    '571cc406e91ea3e9ad633b41671c9ab1d480f75b814f3ae978009d5e847d2cdd': 4,
    '6084618d19fdb7c3bf45a2597063bf6e2834a6bda781c928a8da9e5110fdacd1': 2,
    '61d59d71ae6768ae9b46861428da72539e5e9da090a714dc6c40e051e7b9dc65': 3,
    '67758c65d723d6c14b349a25e4a9ebcc898711bde7902d64d9a62a9e29f099f2': 2,
    '784ed0a82c8d90d7ffa72d081be057b044375b6eb571a0a27ddf5d93edd50c93': 2,
    '855beba5e6ddf3955037cd675f705fa95d1d4c02a9963076ea35c734cd47ad27': 2,
    '871c295707cc1e3a221eef6c377427203ae8736c34dd17f84373ef4b2de3c5cf': 3,
    '8bd38bd0da81c7069cf95280b9dc843e1c2c73d3302c288b01456797e60f57fa': 2,
    '8bd63f27b90f02f3e75e40999ebcff1080ad9cf57dfa89f889aced7ad8af4f8a': 2,
    '982c74997890cb37e198c96c8866a4b58378b992b725d0545745736c34ebf8da': 2,
    'a8f3b94154d1d53c8fd17be431d34cfa3c3e64a064b61075a86bf9150189ab30': 2,
    'ac067aa5bac9cc639a05511c7b8df14daf50750cde65d087dd447d1d3486231e': 2,
    'ad5200f89669816de2ab7a89f0f53c4677a13cf13e15daa192a3de20e7d74ba2': 2,
    'b0e99e95e7afb22a46eeffa64b1438d87895ffb65133f0f22c228507a791b2fe': 2,
    'b1123fbb9f1a6756202c657993ee8e942161275a004058e3fc084567ab1b86f1': 2,
    'b8502c16874f65445c6ad5578a0a8a5b38054d185824ab9cfe8ca96ca7721ddf': 2,
    'bbec83c83f0caf33f685874654b83be3e89a9cfd9f975203efb4448ecc83a795': 2,
    'c12d4d5a11f9323224168f2d9e1e987477c1e73b91a102c9302c396df009db9a': 2,
    'c4e38792b8ab32270fca5c48ca4d39a297bab162d5ecca01a80b7a7c8bd721f0': 2,
    'cfee87fc053dff2f383fee3d79cddfe33e98ea29d6268c716103e627213c7633': 2,
    'd29b44bae85b4592e116da81e0cd05fcaf6630a2d0828de793223ef077886ac7': 2,
    'd55797120e302c5394a60743cc5e47a13051c1cf0dec8d6ed2ca83a56606be3b': 2,
    'd5d14f8d43ac6a90e529c39cc2b25da0afa64bd7e8be3c3474f3366d24e67424': 2,
    'dbdbd7d0554eac1e5d56827cc8698330ade02bd97ff05e0406ef1c8e8a5e1774': 2,
    'dcdfb391c05a84f756bf1b4da82d3a962ca78d2550658973b0b8fe62811554d9': 3,
    'de15d41a14badfca8de3193d54e5f92b68deb21e9bf3f06eb2945c5b5aba67c5': 3,
    'e8ce3e269c3edeff118bdb11d0b8133090cff0b681a628bce47df4cebe1f461a': 2,
    'ede31fc034f5f9a1c9bff16efcc8d8cdebf03d2b7c7db9223d7d1aae221847c8': 2,
    'efe223a642db1a30e029d2f6b0a2057faa473e0fbf3b7ac005dcfbb34a405104': 2,
    'f05ccce39b2db38236ca083bf0463bc5b909a9a1e0dad3c40a219cf4f8873cea': 2,
    'f355ba82e3a80f4be85e846f89b8671dada9b391087a518866a5334817426736': 4,
    'f8430366406aceef71200650af39578f247b0abe468508b5782b72fdb4e1f40e': 4,
}


# Literal affinity metadata is especially dangerous because it is nested
# below a neutral planner/manifest assignment.  Scan that key in every module.
# Current legacy declarations are exact-frozen: BUILTIN_INPROC_SPECS entries
# are ignored by the authenticated builtin loader; the verb-unique admin
# manifest remains an explicitly reviewed pre-existing boundary.
VALUE_BOUND_NESTED_AFFINITIES: Mapping[
    tuple[str, str | None, str], tuple[frozenset[str], str]
] = {
    ("describe_entries.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({"1014aca826fb0098a2ed38cadf61726496ec3c33b7a1aeed2b47de4870bdc519"}),
        "legacy builtin metadata ignored in favour of its signed contract",
    ),
    ("recurring_tasks.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({
            "0c4b8e951c6b84cefab3ad8940a1a72540a0667d1f8439bd4e273096893e02c3",
            "253156f226e39e970f8ebbbdc20549d4e12517cf0f2b73e82b0775e025f34e48",
            "af6ed309ddada70afe46992920b16600241f94c2a806ccb95e338d8a5a3834f1",
            "bbb7875c05136b96db9c253f195e56f1d8e0aa71bf45378fc66b14b9304cf8f8",
            "f6fc41d0d3f4163046c8ab424d0f41eeaf476e937eb389711c37ca42ee7ddc67",
            "41e8ce22d3b45df6dd01eff492065b98fedf5be7c35d658d25bc98608a919e90",
        }),
        "legacy builtin metadata ignored in favour of signed contracts",
    ),
    ("lre_submission.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({"cb923f545b641cdc743071c61be28e2ef73b96e82edc6fa101b06e681cdf0c30"}),
        "legacy builtin metadata ignored in favour of its signed contract",
    ),
    ("describe_images.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({"d16d311cf7910f2dc5690639450c8e736f1858a3acf517934d920bc174b74e74"}),
        "legacy builtin metadata ignored in favour of its signed contract",
    ),
    ("user_preferences.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({
            "255564438ed8c00002341e0f5964bfc148a0a92d5361c2c5abea4e29506b9258",
            "a9bc290df35f68fc1a1f6d39a14d0b275d2fd667d558bd0ea4a11afb2f2714bb",
            "ef6aed7940369476d2bc5a4f46364f7cca95a208ac56a1c0d385d2750a305808",
        }),
        "legacy builtin metadata ignored in favour of signed contracts",
    ),
    ("classify_entries.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({"a9ced06fa5f03fba1ee729dfcb1b165a5c1db1627d6449e6a936ecbb82c25ad7"}),
        "legacy builtin metadata ignored in favour of its signed contract",
    ),
    ("extract_entries.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({"2f756d51d78fc0ed59a7018a78f7067a610fd4c90b138354a387b25daaea45ff"}),
        "legacy builtin metadata ignored in favour of its signed contract",
    ),
    ("compare_entries.py", None, "BUILTIN_INPROC_SPECS"): (
        frozenset({"270c6084f5da242e246ad52b09f7bafc41bc4e3944239ddefe0ed5c3372f8c8c"}),
        "legacy builtin metadata ignored in favour of its signed contract",
    ),
    ("system/admin.py", None, "MANIFEST_VIRTUAL"): (
        frozenset({"d812ecbe30a6bdfedac008264c98e5b45d59943645b149cf78c0716572bbbb28"}),
        "reviewed verb-unique administrative manifest boundary",
    ),
}

# These two ``affinity`` keys describe a manifest field grammar; they are not
# affinity payloads.  Bind the complete value AST so prose or a dynamic value
# cannot be smuggled under the schema exception.
VALUE_BOUND_AFFINITY_SCHEMA_VALUES: Mapping[
    tuple[str, str | None, str], str
] = {
    ("executor_birth_identity.py", None, "MANIFEST_FIELD_GRAMMAR_V1"):
        "8f2cda988d9ca73053c7f508ff6d429ab1df0a26a6352507088f780e596b398c",
    ("synt.py", None, "PROPOSE_EXECUTOR_TOOL"):
        "6e04fd3432ee7f5b148da42c490eeaf7853767992e171930d6093b305d66a4fe",
}

# Dynamic ``affinity`` is denied by default.  These are the only classified
# pass-through/authoring sites: the key+value AST digest is part of the owner,
# and the expected occurrence count prevents one site from lending authority
# to another identical expression.
VALUE_BOUND_DYNAMIC_AFFINITY_VALUES: Mapping[
    tuple[str, str | None, str, str, str], tuple[int, str]
] = {
    (
        "manifest_normalize.py", "load_catalog", "<assignment>", "affinity",
        "35ca7c70b822e1791120cf2a74a51fa76c993f37087b4267aa9b9108490fc950",
    ): (1, "pass-through of manifest metadata for the normalization tool"),
    (
        "skill_codegen.py", "build_context", "<inline-affinity>", "affinity",
        "49a4a7c996e2d664491ad28af6100556fd339268a4dd5c464f0838460c8c8e48",
    ): (1, "pass-through of affinity already built for the generated contract"),
    (
        "skill_description_llm.py", "generate_description_or_fallback",
        "<inline-affinity>", "affinity",
        "49a4a7c996e2d664491ad28af6100556fd339268a4dd5c464f0838460c8c8e48",
    ): (1, "LLM authoring output remains subject to Executor Birth admission"),
    (
        "skill_description_llm.py", "generate_description_or_fallback",
        "<inline-affinity>", "affinity",
        "0b18c4e13398f805e4ecba27e6d7943f0c2d12b99b837ccd9237a74418798c7b",
    ): (1, "boilerplate authoring output remains subject to Executor Birth"),
    (
        "store_entries.py", None, "BUILTIN_INPROC_SPECS", "affinity",
        "f14385a4e9b45d73446b27b0d1c5038d09a9846ef6828cd91c2c1488c5f4f9f9",
    ): (3, "all three specs reuse only native-ready _store_affinity lexicon data"),
    (
        "synt.py", "_write_proposal_to_disk", "meta", "affinity",
        "db96d4f5bc4fc0bcd1266488f6cd8eefde002ba38101966bdd007f9c1e3c0597",
    ): (1, "pass-through of the governed proposal affinity to authenticated metadata"),
    (
        "synt.py", "_generate", "prop", "affinity",
        "db96d4f5bc4fc0bcd1266488f6cd8eefde002ba38101966bdd007f9c1e3c0597",
    ): (1, "pass-through of governed authoring affinity into the proposal object"),
    (
        "synt.py", "_executor_attrs", "<inline-affinity>", "affinity",
        "b193dc4b1b3afc13fcab5feea0a720ba2a08284e2d568fe8374445455d8cc7d3",
    ): (1, "read-only projection of authenticated catalog affinity for comparison"),
    (
        "synt_multistage.py", "run_full", "_man", "affinity",
        "853fd5c8f81dfc0f6af7aa6961b3ff176700b04db2046847e3aa0867bf58350b",
    ): (1, "authoring output is linted and remains subject to Executor Birth"),
}

# Store affinity has a local authority chain, so freeze both the native-ready
# loader and the sole assignment consumed by the three builtin specs.
VALUE_BOUND_DYNAMIC_AFFINITY_AUTHORITY_NODES: Mapping[
    tuple[str, str], str
] = {
    ("store_entries.py", "_store_affinity"):
        "18b2274fd6d006c584196cf4266e5572626f7874032ee5ca92345664d1d1dc38",
    ("store_entries.py", "_AFFINITY"):
        "b1360e479fae7c45f4237ca6de6fe023c8d89cdace05410c1e810b2e142d5c17",
}

# Executable mutation of an affinity payload is denied even when no dict
# literal owns the key.  The two historical authoring pass-throughs are bound
# below after the same path/function/operation AST fingerprinting used for
# other inline sites; their exact cardinality is checked by ``scan_runtime``.
VALUE_BOUND_AFFINITY_MUTATION_FINGERPRINTS: Mapping[str, tuple[int, str]] = {
    "cf034835a87bbe44f3facc8890dcd00ea1db0ed48e8301cdf721b91f4254606f": (
        1, "LLM authoring payload remains subject to Executor Birth admission",
    ),
    "ad7db38ca0f5936e7b5bf6e0b992cfa5b36fdac81042fde4e811ddaa33ed007f": (
        1, "governed proposal affinity passed through to authoring output",
    ),
}
VALUE_BOUND_AFFINITY_MUTATION_OWNERS: Mapping[str, str] = {
    "cf034835a87bbe44f3facc8890dcd00ea1db0ed48e8301cdf721b91f4254606f":
        "skill_description_llm.py",
    "ad7db38ca0f5936e7b5bf6e0b992cfa5b36fdac81042fde4e811ddaa33ed007f":
        "synt.py",
}

_AUDITED_OSM_TAG_IDENTITIES = frozenset({
    "amenity:pharmacy", "amenity:restaurant", "amenity:bar",
    "amenity:pub", "amenity:fast_food", "amenity:ice_cream",
    "amenity:bank", "amenity:atm", "amenity:hospital", "amenity:fuel",
    "amenity:parking", "shop:supermarket", "shop:bakery",
    "amenity:post_office", "highway:bus_stop", "railway:station",
    "tourism:hotel", "tourism:museum", "amenity:cinema",
    "amenity:theatre", "leisure:fitness_centre", "shop:hairdresser",
    "shop:optician", "shop:books", "amenity:school", "amenity:library",
    "leisure:park", "amenity:place_of_worship", "amenity:townhall",
})
_AUDITED_GOOGLE_TYPE_IDENTITIES = frozenset({
    "pharmacy", "restaurant", "bar", "pizza_restaurant",
    "ice_cream_shop", "bank", "atm", "hospital", "gas_station",
    "parking", "supermarket", "bakery", "post_office", "bus_stop",
    "train_station", "lodging", "museum", "movie_theater",
    "performing_arts_theater", "gym", "hair_care", "store",
    "book_store", "school", "library", "park", "church", "city_hall",
})


# Exact technical literals inside otherwise audited call sites.  Entries are
# intentionally value-bound rather than function-wide exemptions.
TECHNICAL_REGEX_LITERALS: Mapping[
    tuple[str, str, str], str
] = {
    (
        "engine/executor.py", "_prepare_static_read_args", r"\b\d+\b",
    ): "language-independent explicit integer grammar",
    (
        "engine/executor.py", "run", r"\b\d+\b",
    ): "language-independent explicit integer grammar",
}
TECHNICAL_MEMBERSHIP_LITERALS: Mapping[
    tuple[str, str, str, frozenset[str]], str
] = {
    (
        "http_routes_agent.py", "_apply_dialog_pending", "schema_kind",
        frozenset({"text", "credentials", "file_path", "location"}),
    ): "closed dialog field-kind wire identities",
}


# This allowlist is intentionally narrow.  Adding an entry is a security and
# localization decision, not a way to silence the scanner.
TECHNICAL_INVARIANTS: Mapping[str, tuple[TechnicalInvariant, ...]] = {
    "prefilter.py": (
        _invariant("_WORD_RE", "unicode-tokenizer", "Unicode word boundaries"),
        _invariant("_FS_EXTENSIONS", "file-protocol", "file suffix identifiers"),
        _invariant("_DOMAIN_RE", "network-grammar", "DNS/domain syntax"),
        _invariant("_OBJECT_PRIMARY_TOOLS", "protocol-graph", "canonical tool identities"),
        _invariant("_COMMAND_TOKEN_RE", "shell-grammar", "argv token syntax"),
        _invariant("_CLI_FIRST_ARGUMENT_RE", "shell-grammar", "argv position syntax"),
    ),
    "compound_decomposer.py": (
        _invariant("PRODUCER_VERBS", "protocol-enum", "canonical read-only action identities"),
        _invariant("MUTATING_VERBS", "protocol-enum", "canonical mutating action identities"),
        _invariant("TRANSFORM_VERBS", "protocol-enum", "canonical transform action identities"),
    ),
    "engine/dispatch.py": (
        _invariant("_READ_INTENT_VERBS", "protocol-enum", "canonical read-only action identities"),
        _invariant("_SINK_VERBS", "protocol-enum", "canonical sink action identities"),
        _invariant("_MASS_MUTATION_VERBS", "protocol-enum", "canonical destructive action identities"),
    ),
    "prefilter_strategies/token_flat_v2.py": (
        _invariant("_PENALIZED_PROVIDER_SUFFIXES", "tool-protocol", "closed provider suffix identities"),
        _invariant("_VERB_FAMILY", "protocol-graph", "canonical action-family equivalence"),
    ),
    "prefilter_rules.py": (
        _invariant("_PATH_PATTERNS", "file-network-grammar", "file suffix, path and URL syntax routing"),
        _invariant("_PATH_DEMOTE_PATTERNS", "network-grammar", "URL host/path syntax routing"),
        _invariant("_QUERY_PATTERN_BOOSTS", "protocol-graph", "lexicon concept to canonical tool routing"),
    ),
    "ordering_clause.py": (
        _invariant("ORDERING_MARKER", "protocol-identifier", "internal framework argument marker"),
    ),
    "time_window_resolver.py": (
        _invariant("_SAFE_VERB_HEADS", "protocol-enum", "canonical read-only action identities"),
    ),
    "args_extractor.py": (
        _invariant("_PATH_RE", "path-grammar", "POSIX, Windows and UNC syntax"),
        _invariant("_URL_RE", "network-grammar", "HTTP URL syntax"),
        _invariant("_EMAIL_RE", "network-grammar", "email address syntax"),
        _invariant("_FILE_EXT_RE", "file-protocol", "literal file suffix syntax"),
        _invariant("_LANG_EXT_MAP", "file-protocol", "programming-language identifiers to suffixes"),
        _invariant("_KNOWN_EXTENSIONS", "file-protocol", "format and file suffix identifiers"),
        _invariant("_TILDE_STANDALONE_RE", "path-grammar", "POSIX home sigil syntax"),
        _invariant("_REPO_SLUG_RE", "network-grammar", "repository owner/name syntax"),
        _invariant("_PLACEHOLDER_OWNERS", "placeholder-grammar", "non-user example identifiers"),
    ),
    "fast_path.py": (
        _invariant("_TIME_PATTERNS", "derived-resource", "runtime view of the versioned detection lexicon"),
        _invariant("_CONFIGURED_TIMEZONE_PATTERNS", "derived-resource", "runtime view of the versioned detection lexicon"),
        _invariant("_DATE_PATTERNS", "derived-resource", "runtime view of the versioned detection lexicon"),
        _invariant("_UNDO_PATTERNS", "derived-resource", "runtime view of the manually reviewed detection lexicon"),
        _invariant("_LOCATION_PATTERNS", "derived-resource", "runtime view of the versioned detection lexicon"),
        _invariant("_FAST_PATTERNS", "protocol-graph", "lexicon-derived forms to canonical executor routing"),
        _invariant("_FAST_PATTERN_BY_INTENT", "protocol-graph", "canonical intent to fast-path identity"),
    ),
    "backend_resolver.py": (
        _invariant("OBJECT_BACKENDS", "protocol-registry", "canonical object, provider and argument identities"),
    ),
    "read_format_resolver.py": (
        _invariant("_AUTO_SUFFIXES", "file-protocol", "document format suffix identifiers"),
    ),
    "target_device.py": (
        _invariant("_TARGET_LEXICON_KEYS", "resource-schema", "canonical keys of the versioned target mapping"),
    ),
    "agent_runtime.py": (
        _invariant("_RE_HTML_DOC_MARKER", "markup-grammar", "structural HTML document syntax"),
        _invariant("_ACTION_VERBS_PRED", "protocol-enum", "canonical action identities"),
        _invariant("_MUTATING_VERBS", "protocol-enum", "canonical mutating action identities"),
        _invariant("_NON_ACTION_VERB_PREFIXES", "protocol-enum", "canonical non-action identities"),
        _invariant("_LOOP_BREAK_HINT_OBJECTS", "protocol-enum", "canonical object identities for message selection"),
    ),
    "system/admin.py": (
        _invariant("VERB", "protocol-identifier", "verb-unique builtin identity"),
        _invariant("_SHELL_LITERAL_PATTERNS", "shell-grammar", "operators and privilege-wrapper tokens"),
        _invariant("_REVERSIBLE_HINTS", "command-template", "canonical inverse argv templates"),
    ),
    "skill_codegen.py": (
        _invariant("_STATUS_WORD_BY_VERB", "wire-enum", "canonical generated result status identities"),
    ),
    "backends/messages/email_metnos.py": (
        _invariant("criteria", "imap-grammar", "IMAP SEARCH wire criteria assembled from canonical arguments"),
    ),
    "classify_entries.py": (
        _invariant("DEFAULT_CRITERIA", "llm-prompt", "localized editorial classifier instructions, not an input recognizer"),
        _invariant("DEFAULT_KIND_CRITERIA", "llm-prompt", "localized domain classifier instructions, not an input recognizer"),
    ),
    "field_synonyms.py": (
        _invariant("FIELD_SYNONYMS", "provider-wire-schema", "canonical provider output field aliases"),
    ),
    "telos_proposals_store.py": (
        _invariant("_TOOL_NAME_BLACKLIST", "diagnostic-corpus", "non-routing false positives in offline Telos mention diagnostics"),
    ),
}


def _reviewed(
    kind: str, reason: str, *symbols: str,
) -> tuple[TechnicalInvariant, ...]:
    return tuple(_invariant(symbol, kind, reason) for symbol in symbols)


# Full-runtime adversarial classification performed during the RM-0005 close
# gate.  These are executable string collections, but their values are wire
# identities, syntax, configuration or data derived from an already governed
# source.  They remain in the census so a rename/removal makes the waiver
# stale and forces a new review.
_REVIEWED_TECHNICAL_INVARIANTS: Mapping[
    str, tuple[TechnicalInvariant, ...]
] = {
    "agent_runtime.py": (
        *_reviewed("pipeline-grammar", "from_step wire reference syntax", "PATTERN"),
        *_reviewed("protocol-enum", "canonical dialog tool and virtual-step identities", "_DIALOG_MARKERS"),
    ),
    "agent_server.py": (
        *_reviewed("installer-sentinel", "byte marker in the polyglot CMD/PowerShell installer", "_CMD_MARKER"),
        *_reviewed("derived-telemetry", "request telemetry assembled from canonical fields", "hint"),
    ),
    "alignment_engine.py": _reviewed("file-protocol", "suffixes of generated JSONL artifacts", "suffix"),
    "arg_provenance.py": _reviewed("protocol-enum", "closed argument-provenance value", "PROV_CLAUSE"),
    "audit/queries.py": _reviewed("diagnostic-corpus", "offline audit examples, not a runtime recognizer", "VERBS"),
    "backends/_google_api_runner.py": _reviewed("tls-grammar", "TLS and ASN.1 library error tokens", "_SSL_PATTERNS"),
    "change_intents.py": _reviewed("protocol-enum", "closed ChangeIntent kinds", "KIND_CACHE_PATTERN", "KIND_REJECT_PATTERN"),
    "config.py": (
        *_reviewed("filesystem-config", "configured database path", "DB_CHANGE_INTENTS"),
        *_reviewed("instance-identity", "administrative aliases from environment and hostname", "SERVER_ALIASES"),
        *_reviewed("locale-config", "canonical bootstrap BCP-47 language tag", "BOOTSTRAP_LANGUAGE"),
    ),
    "contract_bootstrap.py": _reviewed("authenticated-layout", "contract-store filesystem layout", "STORE_RELATIVE", "ACTIVE_RELATIVE"),
    "contract_store.py": _reviewed("authenticated-layout", "contract-store shadow path", "SHADOW_RELATIVE"),
    "durable_workloads/coordinator.py": _reviewed("protocol-state", "closed lease-mutation state", "STOP_REQUESTED"),
    "durable_workloads/compiler.py": _reviewed("locale-grammar", "BCP-47 language-tag syntax", "_LANGUAGE_RE"),
    "durable_workloads/source_authority.py": _reviewed("file-grammar", "source-authority filename suffix", "_SUFFIX_RE"),
    "durable_workloads/storage.py": _reviewed("sql-grammar", "SQL clauses assembled from canonical columns", "clauses"),
    "durable_workloads/worker.py": _reviewed("protocol-state", "closed worker state", "STOPPED"),
    "engine/autopath.py": _reviewed("numeric-config", "environment name for an intent score threshold", "COSINE_FLOOR_INTENT"),
    "engine/cache_validity.py": _reviewed("protocol-enum", "canonical producer actions in pool signatures", "_PRODUCER_VERBS"),
    "engine/dispatch.py": (
        *_reviewed("protocol-enum", "canonical store action identities", "_STORE_VERBS"),
        *_reviewed("derived-intent", "Counter built from canonical Intent actions", "by_verb_obj"),
    ),
    "engine/executor.py": _reviewed("placeholder-grammar", "unresolved step/runtime/filler placeholder syntax", "_UNRESOLVED_PATTERNS"),
    "engine/routing_pool.py": _reviewed("derived-intent", "runtime projection of the canonical Intent object", "intent_dict"),
    "executor_birth_prepared_set.py": _reviewed("authenticated-protocol", "prepared-set marker filename and fields", "MARKER_BASENAME_V1", "MARKER_FIELDS_V1"),
    "executor_birth_snapshot.py": _reviewed("authenticated-layout", "canonical language-state filename", "LANGUAGE_STATE_FILE"),
    "executor_scheduler.py": _reviewed("context-grammar", "structured scheduler language-context marker", "_CONTEXT_LANGUAGE_RE"),
    "executor_birth_service_catalog.py": _reviewed("derived-catalog", "unit names derived from the signed service source", "stop_units"),
    "http_routes_agent.py": _reviewed("capability-grammar", "INLINE_FORM capability marker syntax", "_DIALOG_FORM_MARKER_RE"),
    "from_contains_resolver.py": _reviewed("derived-resource", "regular expressions assembled from detection_lexicon", "patterns"),
    "i18n.py": _reviewed("resource-schema", "technical ERR/WARN/MSG/LOG key namespaces", "by_family"),
    "i18n_translator.py": _reviewed("markup-grammar", "Jinja and Markdown syntax preserved byte-semantically", "_PRESERVE_PATTERNS"),
    "intent_extractor.py": (
        *_reviewed("wire-enum", "closed Intent kinds and canonical read actions", "INTENT_KINDS", "_READING_VERBS"),
        *_reviewed("json-grammar", "parser for the JSON verb property", "verb_m"),
    ),
    "jobs/detection_translate_pending.py": _reviewed("translator-prompt", "translation template, not an executable recognizer", "_PHRASES_TMPL"),
    "jobs/promoter_example.py": _reviewed("markup-protocol", "HTML marker consumed by the example promoter", "_LLM_MARKER"),
    "junk_mail_resolver.py": _reviewed("protocol-enum", "canonical category_hints emitted by mail_client", "_JUNK_MARKERS"),
    "learning_loop.py": _reviewed("derived-change-intent", "runtime ChangeIntent object", "intent"),
    "llm_helpers.py": _reviewed("public-channel-protocol", "internal markup sentinels forbidden on public output", "_PUBLIC_FORBIDDEN_MARKERS"),
    "llm_router.py": _reviewed("configuration-enum", "aliases between configured LLM tiers", "TIER_BINDING_ALIASES"),
    "loader.py": _reviewed("filesystem-config", "configured affinity-audit path", "_AFFINITY_AUDIT_DIR"),
    "mail_account_resolver.py": _reviewed("derived-resource", "quantifiers loaded from detection_lexicon", "quantifiers"),
    "mail_client.py": _reviewed("imap-grammar", "IMAP LIST wire response syntax", "pattern"),
    "manifest_lint.py": _reviewed("protocol-enum", "canonical action families imported from vocab", "PRODUCER_VERBS", "DESTRUCTIVE_VERBS"),
    "manifest_normalize.py": _reviewed("protocol-enum", "canonical action families and orthogonal producer subset", "PRODUCER_VERBS", "DESTRUCTIVE_VERBS", "_PRODUCER_ORTHO_VERBS"),
    "naming_grammar.py": _reviewed("canonical-name-grammar", "closed verbs allowed in executor canonical names", "_ENTRIES_FORBIDDEN_VERBS", "_SYSTEM_PSEUDO_VERBS", "_LISTS_ALLOWED_VERBS"),
    "output_policy.py": (
        *_reviewed("protocol-enum", "canonical output-policy action families", "_READ_VERBS", "_ENUM_VERBS", "_TRANSFORM_VERBS", "_MUTATE_VERBS", "_PACKAGE_VERBS"),
        *_reviewed("step-reference-grammar", "${step...} reference syntax", "_REF_PATTERNS"),
    ),
    "pipeline_shape.py": _reviewed("protocol-enum", "canonical FSM action roles", "FORMATTER_OUT_VERBS", "ACTION_OUT_VERBS"),
    "playwright_sidecar/credential_injection.py": _reviewed("browser-dom-grammar", "JavaScript DOM selectors and constraint API checks", "_HAS_PASSWORD_JS", "_HAS_LOGIN_SURFACE_JS", "_PASSWORD_REJECTED_JS"),
    "prefilter.py": _reviewed("protocol-enum", "canonical universal helper identities", "_UNIVERSAL_HELPER_VERBS"),
    "prefilter_strategies/constraint.py": _reviewed("protocol-enum", "canonical read-action equivalence", "READ_FAMILY"),
    "prefilter_strategies/verb_first.py": _reviewed("derived-intent", "verbs derived from the parsed Intent", "verbs"),
    "prompts_lint.py": _reviewed("template-protocol", "STATIC-END template marker", "_STATIC_END_MARKER_RE"),
    "proposal_evaluator.py": _reviewed("protocol-enum", "wire error classes and canonical transform actions", "_ERROR_CLASS_HINTS_RE", "_TRANSFORMATIVE_VERBS"),
    "published_docs.py": _reviewed("derived-publication", "path and URL aliases built from document inventory", "strong_aliases"),
    "reverse_patterns.py": _reviewed("protocol-registry", "canonical reverse-pattern names", "PATTERNS"),
    "safety/canonicalize.py": _reviewed("derived-policy", "binary target identifiers loaded from policy JSON", "_BINARY_TARGET_HINTS"),
    "sandbox.py": _reviewed("capability-enum", "closed system-read capability identifiers", "_SYSTEM_READ_HINTS"),
    "skill_admission.py": _reviewed("test-corpus", "admission smoke queries grouped for execution", "queries_by_pattern"),
    "skill_audit.py": _reviewed("file-protocol", "migration filename suffix", "_MIGRATION_SUFFIX"),
    "skill_translator.py": _reviewed("schema-and-egress", "canonical argument names, configured hosts and output kinds", "_SINGULAR_RESOURCE_HINTS", "_DOMAIN_HOST_HINT", "_OUTPUT_KIND_BY_VERB"),
    "skills_paths.py": _reviewed("filesystem-layout", "canonical skill directory markers", "markers", "SKILL_PATH_MARKERS"),
    "smoke.py": _reviewed("protocol-enum", "canonical producer/consumer action graph", "_PRODUCER_VERBS", "CONSUMER_VERBS_NEEDING_PRECURSOR", "intent"),
    "stack_migration.py": _reviewed("file-protocol", "stack source filename suffixes", "STACK_SOURCE_SUFFIXES"),
    "synt.py": _reviewed("derived-manifest", "affinity data inherited from the canonical parent manifest", "affinity"),
    "synt_multistage.py": _reviewed("protocol-grammar", "reverse registry, canonical actions and i18n key syntax", "REVERSE_PATTERNS", "SPECIALIZED_VERBS", "_I18N_KEY_PATTERN"),
    "system/sudoer.py": _reviewed("protocol-identifier", "verb-unique builtin identity", "VERB"),
    "testing/registry.py": _reviewed("sql-grammar", "SQL clauses assembled from canonical fields", "clauses"),
    "testing/runner.py": _reviewed("diagnostic-enum", "test status to developer glyph mapping", "marker"),
    "time_window_resolver.py": _reviewed("resource-schema", "canonical temporal field identifiers", "temporal_aliases"),
    "target_device.py": _reviewed("derived-resource", "server markers loaded from detection_lexicon", "server_markers"),
    "tutor/semantic.py": _reviewed("unicode-tokenizer", "Unicode word boundaries", "_WORD"),
    "tutor/service.py": _reviewed("internal-protocol", "Unicode gap token and internal-only markers", "_GAP_WORD", "_INTERNAL_MARKERS"),
    "tutor/sources.py": _reviewed("manifest-protocol", "mandatory description purpose anchor", "_PURPOSE_MARKER"),
    "ui_surfaces.py": _reviewed("output-resource", "editorial output resolved through i18n plus developer diagnostics", "SURFACES", "marker"),
    "vaglio.py": _reviewed("security-grammar", "path and destructive-shell syntax enforced language-independently", "_FORBIDDEN_PATH_PATTERNS", "_DANGEROUS_SHELL_PATTERNS"),
    "vocab.py": _reviewed("protocol-graph", "canonical provider and action/object/safety identities", "PROVIDER_SUFFIXES", "PRODUCER_VERBS", "COVERAGE_REQUIRED_VERBS", "PROCESSOR_VERBS", "DESTRUCTIVE_VERBS", "PRECURSOR_VERBS", "OBJECT_DEFAULT_MUTATING_VERB", "SAFE_VERBS", "SYSTEM_VERBS"),
}

TECHNICAL_INVARIANTS = {
    path: (
        *TECHNICAL_INVARIANTS.get(path, ()),
        *_REVIEWED_TECHNICAL_INVARIANTS.get(path, ()),
    )
    for path in set(TECHNICAL_INVARIANTS) | set(_REVIEWED_TECHNICAL_INVARIANTS)
}


_NAME_MARKERS = (
    "WORD", "MARKER", "HINT", "VERB", "ARTICLE", "STOP", "PREPOSITION",
    "PATTERN", "REGEX", "SUFFIX", "FAMILY", "CLAUSE", "CONNECTOR",
    "RELATIVE", "QUANTIFIER", "IMPERATIVE", "REFUSAL", "AFFINITY",
    "INTENT", "PHRASE", "ALIAS", "LEXICON", "SURFACE", "CONFIRM", "LOOKUP",
    "ITALIAN", "ENGLISH", "LANGUAGE", "SYNONYM", "CRITERIA", "BLACKLIST",
)


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def _authority_node_digests(tree: ast.AST) -> Mapping[str, str]:
    observed: dict[str, str] = {}
    for node in ast.walk(tree):
        name = (
            node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else _assigned_name(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            else None
        )
        if not name:
            continue
        observed[name] = hashlib.sha256(ast.dump(
            node, annotate_fields=True, include_attributes=False,
        ).encode("utf-8")).hexdigest()
    return observed


def _authority_node_counts(tree: ast.AST) -> Counter[str]:
    counts: Counter[str] = Counter()
    for node in ast.walk(tree):
        name = (
            node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else _assigned_name(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            else None
        )
        if name:
            counts[name] += 1
    return counts


def _enclosing_function(
    node: ast.AST, parents: Mapping[ast.AST, ast.AST],
) -> str | None:
    ancestor = parents.get(node)
    while ancestor is not None:
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ancestor.name
        ancestor = parents.get(ancestor)
    return None


def _literal_value(node: ast.AST):
    """Evaluate a closed constant expression without executing source.

    ``ast.literal_eval`` deliberately rejects useful constant construction
    forms such as ``('a',) + ('b',)`` and ``dict(affinity=[...])``.  Those
    forms are security-equivalent to a literal table, so the census folds the
    small, explicit subset below itself.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        pass
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_literal_value(child) for child in node.elts]
        if any(value is None for value in values):
            return None
        if isinstance(node, ast.List):
            return values
        if isinstance(node, ast.Tuple):
            return tuple(values)
        try:
            return set(values)
        except TypeError:
            return None
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):
            return None
        keys = [_literal_value(key) for key in node.keys]
        values = [_literal_value(value) for value in node.values]
        if any(value is None for value in (*keys, *values)):
            return None
        try:
            return dict(zip(keys, values))
        except (TypeError, ValueError):
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_value(node.left)
        right = _literal_value(node.right)
        if ((isinstance(left, str) and isinstance(right, str))
                or (isinstance(left, list) and isinstance(right, list))
                or (isinstance(left, tuple) and isinstance(right, tuple))):
            return left + right
        return None
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"frozenset", "set", "tuple", "list"}
            and len(node.args) == 1 and not node.keywords):
        value = _literal_value(node.args[0])
        if value is None:
            return None
        try:
            return {
                "frozenset": frozenset,
                "set": set,
                "tuple": tuple,
                "list": list,
            }[node.func.id](value)
        except (TypeError, ValueError):
            return None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "dict" and len(node.args) <= 1):
        if any(keyword.arg is None for keyword in node.keywords):
            return None
        if node.args:
            base = _literal_value(node.args[0])
            if not isinstance(base, dict):
                return None
            result = dict(base)
        else:
            result = {}
        for keyword in node.keywords:
            value = _literal_value(keyword.value)
            if value is None:
                return None
            result[str(keyword.arg)] = value
        return result
    return None


def _constant_container_value(node: ast.AST):
    """Fold closed container constructors without widening inline literals."""

    direct = _literal_value(node)
    if direct is not None:
        return direct
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "split" and not node.keywords
            and len(node.args) <= 2):
        base = _literal_value(node.func.value)
        arguments = [_literal_value(argument) for argument in node.args]
        if (not isinstance(base, str)
                or any(argument is None for argument in arguments)
                or (arguments and not isinstance(arguments[0], str))
                or (len(arguments) == 2
                    and not isinstance(arguments[1], int))):
            return None
        try:
            return base.split(*arguments)
        except (TypeError, ValueError):
            return None
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json" and node.func.attr == "loads"
            and len(node.args) == 1 and not node.keywords):
        payload = _literal_value(node.args[0])
        if not isinstance(payload, str):
            return None
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, RecursionError):
            return None
    return None


def _flat_string_mapping(node: ast.AST) -> dict[str, str] | None:
    literal = _constant_container_value(node)
    if (not isinstance(literal, dict) or not literal
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in literal.items()
            )):
        return None
    return literal


def _string_mapping_sha256(mapping: Mapping[str, str]) -> str:
    canonical = json.dumps(
        mapping, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _literal_container_sha256(value: object) -> str:
    if isinstance(value, dict):
        payload = {"type": "dict", "items": sorted(value.items())}
    elif isinstance(value, (set, frozenset)):
        payload = {"type": "set", "items": sorted(value)}
    elif isinstance(value, tuple):
        payload = {"type": "tuple", "items": list(value)}
    elif isinstance(value, list):
        payload = {"type": "list", "items": value}
    else:
        raise TypeError("unsupported literal container")
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _literal_leaf_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        # Detection mappings are canonical-key -> surface collection.  Keys
        # are schema identities, not native-language evidence.
        return tuple(
            item
            for child in value.values()
            for item in _literal_leaf_strings(child)
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(
            item for child in value for item in _literal_leaf_strings(child)
        )
    return ()


def _is_string_literal_container(value: object) -> bool:
    """Return whether *value* is a non-empty, recursively string-only table.

    Language is intentionally not guessed.  A one-word Spanish set and a
    nested dict of lists have exactly the same security posture as the old
    bilingual IT/EN aliases.  Technical tables are admitted only by a typed
    validator or an owner/shape/value fingerprint.
    """
    if isinstance(value, dict):
        return bool(value) and all(
            isinstance(key, str)
            and (isinstance(item, str) or _is_string_literal_container(item))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return bool(value) and all(
            isinstance(item, str) or _is_string_literal_container(item)
            for item in value
        )
    return False


def _deep_literal_payload(value: object) -> object:
    """Canonical, type-preserving payload for nested literal containers."""
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, dict):
        return [
            "dict",
            [
                [["str", key], _deep_literal_payload(item)]
                for key, item in value.items()
            ],
        ]
    if isinstance(value, (set, frozenset)):
        items = [_deep_literal_payload(item) for item in value]
        return [
            "frozenset" if isinstance(value, frozenset) else "set",
            sorted(items, key=lambda item: json.dumps(item, sort_keys=True)),
        ]
    if isinstance(value, tuple):
        return ["tuple", [_deep_literal_payload(item) for item in value]]
    if isinstance(value, list):
        return ["list", [_deep_literal_payload(item) for item in value]]
    raise TypeError("unsupported string literal container")


def _deep_literal_container_sha256(value: object) -> str:
    canonical = json.dumps(
        _deep_literal_payload(value), ensure_ascii=True,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _executable_container_fingerprint(
    relative: str, function: str | None, name: str, value: object,
) -> str:
    canonical = json.dumps(
        [relative, function, name, _deep_literal_payload(value)],
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _technical_literal_fingerprint(
    relative: str, function: str | None, name: str, value: ast.AST,
) -> str:
    canonical = json.dumps(
        [
            relative, function, name,
            ast.dump(value, annotate_fields=True, include_attributes=False),
        ],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _affinity_key_value_ast_sha256(
    key_node: ast.AST, value_node: ast.AST,
) -> str:
    pair = ast.Tuple(elts=[key_node, value_node], ctx=ast.Load())
    canonical = ast.dump(
        pair, annotate_fields=True, include_attributes=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _inline_literal_fingerprint(
    relative: str, function: str | None, kind: str, node: ast.AST,
) -> str:
    canonical = json.dumps(
        [
            relative, function, kind,
            ast.dump(node, annotate_fields=True, include_attributes=False),
        ],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


_CLOSED_SOURCE_REVIEW_PIN_NAMES = frozenset({
    "BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
    "_BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
})


def _module_ast_sha256(tree: ast.AST) -> str:
    """Hash a reviewed module while treating the closed-source pin as data.

    The pin is deliberately rotated whenever the reviewed source set changes.
    It must not invalidate every unrelated, structurally reviewed technical
    literal in the two gate modules.  Only the value of the two exact pin
    assignments is normalized; names, assignment shape and all other SHA-256
    values remain part of the module authority.
    """
    canonical_tree = copy.deepcopy(tree)
    for node in ast.walk(canonical_tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        else:
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id in _CLOSED_SOURCE_REVIEW_PIN_NAMES
            for target in targets
        ):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value.startswith("sha256:")
            and len(value.value) == 71
            and all(char in "0123456789abcdef" for char in value.value[7:])
        ):
            continue
        value.value = "sha256:" + "0" * 64
    return hashlib.sha256(ast.dump(
        canonical_tree, annotate_fields=True, include_attributes=False,
    ).encode("utf-8")).hexdigest()


def _inline_executable_literal_sites(
    tree: ast.AST, relative: str, parents: Mapping[ast.AST, ast.AST],
) -> tuple[tuple[ast.AST, str, str, str], ...]:
    """Return (node, function, kind, fingerprint) for inline literal gates."""
    sites: list[tuple[ast.AST, str, str, str]] = []
    seen_nodes: set[tuple[int, str]] = set()

    def add(node: ast.AST, kind: str) -> None:
        identity = (id(node), kind)
        if identity in seen_nodes:
            return
        seen_nodes.add(identity)
        function = _enclosing_function(node, parents) or "<module>"
        sites.append((
            node, function, kind,
            _inline_literal_fingerprint(
                relative, None if function == "<module>" else function,
                kind, node,
            ),
        ))

    string_bindings = _constant_string_bindings_by_scope(tree, parents)

    def folded_string(node: ast.AST) -> str | None:
        scope = _lexical_scope(node, parents)
        value = _constant_folded_string(node, string_bindings.get(scope, {}))
        if value is None and scope is not None:
            value = _constant_folded_string(
                node, string_bindings.get(None, {}),
            )
        return value

    def is_gate_literal(node: ast.AST) -> bool:
        value = _literal_value(node)
        if _is_string_literal_container(value):
            return True
        text_value = folded_string(node)
        return bool(text_value and any(character.isalpha()
                                       for character in text_value))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare)
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)):
            old_membership = any(
                _is_string_literal_container(_literal_value(comparator))
                for comparator in node.comparators
            )
            if old_membership:
                add(node, "membership")
            if (not old_membership and any(
                    is_gate_literal(operand)
                    for operand in (node.left, *node.comparators))):
                add(node, "comparison")
        elif (isinstance(node, ast.Compare)
                and any(isinstance(op, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot))
                        for op in node.ops)
                and any(
                    is_gate_literal(operand)
                    for operand in (node.left, *node.comparators)
                )):
            add(node, "comparison")
        elif isinstance(node, (ast.For, ast.comprehension)):
            if _is_string_literal_container(_literal_value(node.iter)):
                add(node, "iteration")
        elif isinstance(node, ast.Subscript):
            if _is_string_literal_container(_literal_value(node.value)):
                add(node, "literal-lookup")
        if not isinstance(node, ast.Call):
            continue
        call_name = (
            node.func.id if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute)
            else ""
        )
        if (call_name in {"compile", "match", "search", "fullmatch"}
                and node.args
                and (pattern := folded_string(node.args[0])) is not None
                and any(character.isalpha() for character in pattern)):
            add(node, "regex")
        if (call_name in {"startswith", "endswith"} and node.args
                and (
                    isinstance(_literal_value(node.args[0]), str)
                    or _is_string_literal_container(
                        _literal_value(node.args[0]),
                    )
                )):
            add(node, "prefix-suffix")
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "items", "keys", "values",
                                       "__contains__"}
                and _is_string_literal_container(
                    _literal_value(node.func.value),
                )):
            add(node, "literal-lookup")
        if call_name not in {
                "dict", "frozenset", "list", "set", "tuple",
                "compile", "match", "search", "fullmatch",
        } and any(
            _is_string_literal_container(_literal_value(argument))
            for argument in (
                *node.args, *(keyword.value for keyword in node.keywords)
            )
        ):
            add(node, "helper-argument")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = (
            *node.args.defaults,
            *(default for default in node.args.kw_defaults
              if default is not None),
        )
        if any(_is_string_literal_container(_literal_value(default))
               for default in defaults):
            add(node, "default")
    return tuple(sites)


def _constant_folded_string(
    node: ast.AST, bindings: Mapping[str, str] | None = None,
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and bindings is not None:
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_folded_string(node.left, bindings)
        right = _constant_folded_string(node.right, bindings)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for value in node.values:
            if not (isinstance(value, ast.Constant)
                    and isinstance(value.value, str)):
                return None
            pieces.append(value.value)
        return "".join(pieces)
    return None


def _constant_string_bindings_by_scope(
    tree: ast.AST, parents: Mapping[ast.AST, ast.AST],
) -> Mapping[ast.AST | None, Mapping[str, str]]:
    pending: dict[ast.AST | None, list[tuple[str, ast.AST]]] = {}
    for node in ast.walk(tree):
        if (not isinstance(node, (ast.Assign, ast.AnnAssign))
                or node.value is None):
            continue
        name = _assigned_name(node)
        if name:
            pending.setdefault(_lexical_scope(node, parents), []).append(
                (name, node.value),
            )
    resolved: dict[ast.AST | None, dict[str, str]] = {
        scope: {} for scope in pending
    }
    for _round in range(sum(len(items) for items in pending.values()) + 1):
        changed = False
        for scope, assignments in pending.items():
            candidates: dict[str, set[str]] = {}
            for name, value_node in assignments:
                value = _constant_folded_string(value_node, resolved[scope])
                if value is not None:
                    candidates.setdefault(name, set()).add(value)
            new_bindings = {
                name: next(iter(values))
                for name, values in candidates.items() if len(values) == 1
            }
            if new_bindings != resolved[scope]:
                resolved[scope] = new_bindings
                changed = True
        if not changed:
            break
    return resolved


def _folded_string_in_scope(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
    bindings_by_scope: Mapping[ast.AST | None, Mapping[str, str]],
) -> str | None:
    scope = _lexical_scope(node, parents)
    value = _constant_folded_string(node, bindings_by_scope.get(scope, {}))
    if value is None and scope is not None:
        value = _constant_folded_string(node, bindings_by_scope.get(None, {}))
    return value


def _affinity_mutation_sites(
    tree: ast.AST, relative: str, parents: Mapping[ast.AST, ast.AST],
) -> tuple[tuple[ast.AST, str, str], ...]:
    """Find construction or mutation which can inject an affinity payload."""
    bindings = _constant_string_bindings_by_scope(tree, parents)
    sites: list[tuple[ast.AST, str, str]] = []
    seen: set[int] = set()

    def key_is_affinity(node: ast.AST) -> bool:
        return _folded_string_in_scope(node, parents, bindings) == "affinity"

    def target_is_affinity(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return "affinity" in node.id.casefold()
        if isinstance(node, ast.Attribute):
            return "affinity" in node.attr.casefold()
        if isinstance(node, ast.Subscript):
            return key_is_affinity(node.slice)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault" and node.args
                and key_is_affinity(node.args[0])):
            return True
        return False

    def expression_has_affinity_key(node: ast.AST) -> bool:
        if isinstance(node, ast.Dict):
            return any(
                key is not None and key_is_affinity(key)
                for key in node.keys
            )
        return bool(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
            and any(keyword.arg == "affinity" for keyword in node.keywords)
        )

    def add(node: ast.AST) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        function = _enclosing_function(node, parents) or "<module>"
        fingerprint = _inline_literal_fingerprint(
            relative, None if function == "<module>" else function,
            "affinity-mutation", node,
        )
        sites.append((node, function, fingerprint))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Subscript)
                   and key_is_affinity(target.slice) for target in targets):
                add(node)
            continue
        if isinstance(node, ast.AugAssign):
            if target_is_affinity(node.target):
                add(node)
            continue
        if not isinstance(node, ast.Call):
            continue
        if (isinstance(node.func, ast.Name) and node.func.id == "dict"
                and any(keyword.arg == "affinity"
                        for keyword in node.keywords)):
            add(node)
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        method = node.func.attr
        if method in {"append", "extend", "update"} and target_is_affinity(
                receiver):
            add(node)
            continue
        if method != "update":
            continue
        if any(keyword.arg == "affinity" for keyword in node.keywords):
            add(node)
            continue
        for argument in node.args:
            if expression_has_affinity_key(argument):
                add(node)
                break
    return tuple(sites)


def _lexical_scope(
    node: ast.AST, parents: Mapping[ast.AST, ast.AST],
) -> ast.AST | None:
    ancestor = parents.get(node)
    while ancestor is not None:
        if isinstance(
                ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return ancestor
        ancestor = parents.get(ancestor)
    return None


def _container_drives_execution(
    tree: ast.AST, assignment: ast.Assign | ast.AnnAssign, name: str,
    parents: Mapping[ast.AST, ast.AST],
) -> bool:
    """Whether a literal table is loaded by executable code.

    Scope matching prevents a same-named local in a different function from
    making an unrelated declaration executable.  Module-owned tables may be
    consumed in any child function.  Passing or returning the table counts:
    otherwise a neutral helper call could hide the actual membership test.
    """
    owner_scope = _lexical_scope(assignment, parents)
    for use in ast.walk(tree if owner_scope is None else owner_scope):
        if (not isinstance(use, ast.Name) or use.id != name
                or not isinstance(use.ctx, ast.Load)):
            continue
        if owner_scope is not None and _lexical_scope(use, parents) is not owner_scope:
            continue
        return True
    return False


def _valid_structural_technical_table(
    relative: str, name: str, value: ast.AST,
) -> bool:
    literal = _literal_value(value)
    if relative == "skill_wrapper.py":
        expected = {
            "_STORE_TRUE_CANONICAL_TRUE": frozenset({"1", "true"}),
            "_STORE_TRUE_CANONICAL_FALSE": frozenset({"0", "false"}),
        }.get(name)
        return expected is not None and literal == expected
    if relative == "google_places_client.py" and name == "_OSM_TO_GOOGLE_TYPE":
        if not isinstance(literal, dict) or not literal:
            return False
        return all(
            osm_tag in _AUDITED_OSM_TAG_IDENTITIES
            and google_type in _AUDITED_GOOGLE_TYPE_IDENTITIES
            for osm_tag, google_type in literal.items()
        )
    if (relative == "admin/promotions_review.py"
            and name.startswith("_OPTIONS_")):
        import re
        return bool(literal) and isinstance(literal, tuple) and all(
            isinstance(pair, tuple) and len(pair) == 2
            and isinstance(pair[0], str)
            and re.fullmatch(r"[a-z][a-z0-9_]*", pair[0])
            and isinstance(pair[1], str)
            and re.fullmatch(r"MSG_[A-Z0-9_]+", pair[1])
            for pair in literal
        )
    return False


def _literal_strings(node: ast.AST) -> tuple[str, ...]:
    return tuple(
        child.value for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
        and any(character.isalpha() for character in child.value)
    )


def _membership_symbol(node: ast.Compare) -> str:
    try:
        return ast.unparse(node.left)
    except Exception:
        return "<membership>"


def _contains_literal_words(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if any(character.isalpha() for character in child.value):
                return True
    return False


def _local_literal_table(node: ast.AST) -> bool:
    """Whether a function-local expression actually owns literal language.

    A local ``lexicon = load_family("concept.name")`` contains a string but
    does not own a word table.  Literal containers (including a container
    nested in ``dict.fromkeys``/``list``) and literal regular expressions do.
    """
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
        return _contains_literal_words(node)
    if not isinstance(node, ast.Call):
        return False
    function_name = ""
    if isinstance(node.func, ast.Name):
        function_name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        function_name = node.func.attr
    if function_name in {"compile", "match", "search", "fullmatch"}:
        return bool(node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and _contains_literal_words(node.args[0]))
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        nested_name = (
            child.func.id if isinstance(child.func, ast.Name)
            else child.func.attr if isinstance(child.func, ast.Attribute)
            else ""
        )
        if (nested_name in {"compile", "match", "search", "fullmatch"}
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
                and _contains_literal_words(child.args[0])):
            return True
    return any(
        isinstance(child, (ast.Dict, ast.List, ast.Set, ast.Tuple))
        and _contains_literal_words(child)
        for child in ast.walk(node)
    )


def _is_suspect_name(name: str) -> bool:
    if name.endswith("_CONCEPT") or name.endswith("_CONCEPTS"):
        return False
    upper = name.upper()
    return any(marker in upper for marker in _NAME_MARKERS)


def _named_assignment_is_audited(
    relative: str, function: str | None, name: str,
) -> bool:
    return bool(
        _is_suspect_name(name)
        or name in KNOWN_LINGUISTIC_SYMBOLS.get(relative, frozenset())
        or name in AUDITED_LINGUISTIC_SYMBOLS.get(relative, frozenset())
        or (
            function is not None
            and name in AUDITED_LOCAL_SYMBOLS.get(relative, {}).get(
                function, frozenset(),
            )
        )
        or (
            function is not None
            and function in AUDITED_TABLE_FUNCTIONS.get(
                relative, frozenset(),
            )
        )
    )


def scan_file(path: Path, *, relative_path: str | None = None) -> list[CensusIssue]:
    relative = relative_path or path.name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [CensusIssue(relative, 0, "", "LEXICON_PARSE", str(exc))]
    allowed = {
        entry.symbol: entry for entry in TECHNICAL_INVARIANTS.get(relative, ())
    }
    structural = {
        entry.symbol: entry
        for entry in STRUCTURAL_TECHNICAL_INVARIANTS.get(relative, ())
    }
    issues: list[CensusIssue] = []
    declared: set[str] = set()
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    constant_strings = _constant_string_bindings_by_scope(tree, parents)
    inline_literal_sites = _inline_executable_literal_sites(
        tree, relative, parents,
    )
    unbound_inline_counts = Counter(
        kind for _node, _function, kind, fingerprint in inline_literal_sites
        if fingerprint not in VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS
    )
    legacy_gate_authority = LEGACY_LITERAL_GATE_FILE_AUTHORITIES.get(relative)
    legacy_gate_authority_valid = bool(
        legacy_gate_authority is not None
        and legacy_gate_authority[0] == _module_ast_sha256(tree)
        and Counter(dict(legacy_gate_authority[1])) == unbound_inline_counts
    )
    authority_node_digests = _authority_node_digests(tree)
    authority_node_counts = _authority_node_counts(tree)
    dynamic_affinity_authority_valid = all(
        authority_node_digests.get(symbol) == expected_digest
        for (owner_path, symbol), expected_digest
        in VALUE_BOUND_DYNAMIC_AFFINITY_AUTHORITY_NODES.items()
        if owner_path == relative
    )
    handled_literal_nodes: set[int] = set()
    seen_value_bound_mappings: Counter[
        tuple[str, str | None, str]
    ] = Counter()
    seen_value_bound_collections: Counter[
        tuple[str, str | None, str]
    ] = Counter()
    seen_nested_affinities: Counter[
        tuple[tuple[str, str | None, str], str]
    ] = Counter()
    seen_dynamic_affinities: dict[
        tuple[str, str | None, str, str, str], int
    ] = {}
    seen_affinity_mutations: Counter[str] = Counter()
    seen_affinity_schema_values: Counter[
        tuple[str, str | None, str]
    ] = Counter()
    seen_structural: Counter[str] = Counter(
        name
        for candidate in ast.walk(tree)
        if isinstance(candidate, (ast.Assign, ast.AnnAssign))
        and (name := _assigned_name(candidate)) in structural
    )
    seen_bound_executable: Counter[str] = Counter()
    seen_bound_technical: Counter[str] = Counter()
    seen_bound_inline: Counter[str] = Counter(
        fingerprint
        for _node, _function, _kind, fingerprint in inline_literal_sites
        if fingerprint in VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS
    )

    # Detect recursively string-only lookup/membership/iteration tables without
    # guessing their language.  Exact exceptions cannot be borrowed by a new
    # file, function, symbol, container kind or value.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        name = _assigned_name(node)
        if not name or node.value is None:
            continue
        function = _enclosing_function(node, parents)
        owner = (relative, function, name)
        literal = _constant_container_value(node.value)
        is_string_container = _is_string_literal_container(literal)
        mapping = _flat_string_mapping(node.value)
        exact_guard_changed = False
        if mapping is not None:
            expected = VALUE_BOUND_STRING_MAPPINGS.get(owner)
            if expected is not None:
                seen_value_bound_mappings[owner] += 1
                handled_literal_nodes.add(id(node))
                if _string_mapping_sha256(mapping) == expected[0]:
                    continue
                exact_guard_changed = True
            elif (name in structural
                    and _valid_structural_technical_table(
                        relative, name, node.value,
                    )):
                handled_literal_nodes.add(id(node))
                continue
        if not is_string_container:
            continue
        expected_collection = VALUE_BOUND_LANGUAGE_COLLECTIONS.get(owner)
        if (mapping is None and expected_collection is not None):
            seen_value_bound_collections[owner] += 1
            handled_literal_nodes.add(id(node))
            if _literal_container_sha256(literal) == expected_collection[0]:
                continue
            exact_guard_changed = True
        fingerprint = _executable_container_fingerprint(
            relative, function, name, literal,
        )
        if fingerprint in VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS:
            handled_literal_nodes.add(id(node))
            seen_bound_executable[fingerprint] += 1
            continue
        if (name in structural
                and _valid_structural_technical_table(
                    relative, name, node.value,
                )):
            handled_literal_nodes.add(id(node))
            continue
        if not exact_guard_changed and not (
            _named_assignment_is_audited(relative, function, name)
            or _container_drives_execution(tree, node, name, parents)
        ):
            continue
        handled_literal_nodes.add(id(node))
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)), name,
            (
                "LEXICON_LITERAL"
                if _named_assignment_is_audited(relative, function, name)
                else (
                    "LEXICON_NEUTRAL_TABLE"
                    if isinstance(literal, dict)
                    else "LEXICON_NEUTRAL_COLLECTION"
                )
            ),
            "unreviewed executable string container must move to "
            "detection_lexicon; technical data requires an exact "
            "owner/shape/value guard or typed structural validator",
        ))

    # Planner/schema dictionaries can hide affinity words below any neutral
    # outer assignment.  This guard is discovery-wide; exact legacy values
    # above are the only exceptions.
    for dictionary in ast.walk(tree):
        if not isinstance(dictionary, ast.Dict):
            continue
        function = _enclosing_function(dictionary, parents)
        assignment = "<inline-affinity>"
        ancestor = parents.get(dictionary)
        while ancestor is not None and not isinstance(
                ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(ancestor, (ast.Assign, ast.AnnAssign)):
                assignment = _assigned_name(ancestor) or "<assignment>"
                break
            ancestor = parents.get(ancestor)
        owner = (relative, function, assignment)
        for key_node, value_node in zip(dictionary.keys, dictionary.values):
            bindings = constant_strings.get(
                _lexical_scope(dictionary, parents), {},
            )
            if (key_node is None
                    or _constant_folded_string(key_node, bindings) != "affinity"):
                continue
            literal = _constant_container_value(value_node)
            expected = VALUE_BOUND_NESTED_AFFINITIES.get(owner)
            schema_digest = hashlib.sha256(ast.dump(
                value_node, annotate_fields=True, include_attributes=False,
            ).encode("utf-8")).hexdigest()
            if VALUE_BOUND_AFFINITY_SCHEMA_VALUES.get(owner) == schema_digest:
                seen_affinity_schema_values[owner] += 1
                continue
            dynamic_owner = (
                relative, function, assignment, "affinity",
                _affinity_key_value_ast_sha256(key_node, value_node),
            )
            if (dynamic_owner in VALUE_BOUND_DYNAMIC_AFFINITY_VALUES
                    and dynamic_affinity_authority_valid):
                seen_dynamic_affinities[dynamic_owner] = (
                    seen_dynamic_affinities.get(dynamic_owner, 0) + 1
                )
                continue
            digest = (
                _literal_container_sha256(literal)
                if (isinstance(literal, (list, tuple, set, frozenset))
                    and literal
                    and all(isinstance(item, str) for item in literal))
                else None
            )
            if (expected is not None and digest is not None
                    and digest in expected[0]):
                seen_nested_affinities[(owner, digest)] += 1
                continue
            issues.append(CensusIssue(
                relative, int(getattr(key_node, "lineno", 0)), assignment,
                "LEXICON_NESTED_TABLE",
                "nested 'affinity' metadata, including computed values, must "
                "use the authenticated detection lexicon",
            ))

    for node, function, fingerprint in _affinity_mutation_sites(
            tree, relative, parents):
        expected = VALUE_BOUND_AFFINITY_MUTATION_FINGERPRINTS.get(fingerprint)
        if expected is not None:
            seen_affinity_mutations[fingerprint] += 1
            continue
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)),
            f"{function}::<affinity-mutation>", "LEXICON_NESTED_TABLE",
            "affinity construction or mutation must use native-ready "
            "detection_lexicon data under an exact authority",
        ))

    # Local helper tables are executable lexicon just as much as module-level
    # constants.  Walking the complete tree prevents hiding a language table
    # inside a routing function.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        name = _assigned_name(node)
        value = node.value
        if name:
            declared.add(name)
        if id(node) in handled_literal_nodes:
            continue
        function = _enclosing_function(node, parents)
        known = name in KNOWN_LINGUISTIC_SYMBOLS.get(relative, frozenset())
        audited_symbol = name in AUDITED_LINGUISTIC_SYMBOLS.get(
            relative, frozenset(),
        )
        audited_local = bool(
            function and name in AUDITED_LOCAL_SYMBOLS.get(
                relative, {},
            ).get(function, frozenset())
        )
        audited_table = bool(
            function and function in AUDITED_TABLE_FUNCTIONS.get(
                relative, frozenset(),
            )
        )
        if name in structural:
            if _valid_structural_technical_table(relative, name, value):
                continue
            issues.append(CensusIssue(
                relative, int(getattr(node, "lineno", 0)), name,
                "LEXICON_LITERAL",
                "technical table shape changed or owns natural-language data",
            ))
            continue
        if (not name or value is None
                or not (_is_suspect_name(name) or known or audited_symbol
                        or audited_local or audited_table)):
            continue
        is_local = function is not None
        if is_local and not _local_literal_table(value):
            continue
        if not _contains_literal_words(value):
            continue
        fingerprint = _technical_literal_fingerprint(
            relative, function, name, value,
        )
        if (name in allowed
                and fingerprint in VALUE_BOUND_TECHNICAL_LITERAL_FINGERPRINTS):
            seen_bound_technical[fingerprint] += 1
            continue
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)), name,
            "LEXICON_LITERAL",
            "natural-language-like table must move to detection_lexicon; "
            "technical literals require an exact owner/shape/value guard",
        ))

    # Literal regular expressions can bypass the assignment-name heuristic
    # completely (``return bool(re.search('annulla|cancel', query))``).  Audit
    # only the already identified semantic boundaries and exempt exact
    # language-independent grammars, never a whole function.
    for node, function, kind, fingerprint in inline_literal_sites:
        if fingerprint in VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS:
            continue
        if legacy_gate_authority_valid:
            continue
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)),
            f"{function}::<inline-{kind}>", "LEXICON_INLINE_LITERAL",
            "inline executable string literal must move to detection_lexicon; "
            "technical syntax requires an exact path/function/shape/value guard",
        ))

    # Keep the narrower historical codes below for precise regression messages
    # at the mutating/authoritative boundaries already identified by RM-0005.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = _enclosing_function(node, parents)
        if not function or function not in AUDITED_REGEX_FUNCTIONS.get(
                relative, frozenset()):
            continue
        call_name = (
            node.func.id if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute)
            else ""
        )
        if call_name not in {"compile", "match", "search", "fullmatch"}:
            continue
        if (not node.args or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
                or not _contains_literal_words(node.args[0])):
            continue
        pattern = node.args[0].value
        if (relative, function, pattern) in TECHNICAL_REGEX_LITERALS:
            continue
        owner = "<inline-regex>"
        ancestor = parents.get(node)
        while ancestor is not None and not isinstance(
                ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(ancestor, (ast.Assign, ast.AnnAssign)):
                owner = _assigned_name(ancestor) or owner
                break
            ancestor = parents.get(ancestor)
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)),
            f"{function}::{owner}", "LEXICON_INLINE_REGEX",
            "literal regex in audited language boundary must use detection_lexicon",
        ))

    # Membership against a literal tuple/set is an executable word table even
    # when no variable receives it.  Exact wire-enum tuples are value-bound in
    # TECHNICAL_MEMBERSHIP_LITERALS.
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Compare)
                or not any(isinstance(op, (ast.In, ast.NotIn))
                           for op in node.ops)):
            continue
        function = _enclosing_function(node, parents)
        if not function or function not in AUDITED_MEMBERSHIP_FUNCTIONS.get(
                relative, frozenset()):
            continue
        symbol = _membership_symbol(node)
        for comparator in node.comparators:
            if not isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                continue
            words = _literal_strings(comparator)
            if not words:
                continue
            literal = _literal_value(comparator)
            values = frozenset(
                value for value in literal if isinstance(value, str)
            ) if isinstance(literal, (tuple, list, set, frozenset)) else words
            if (relative, function, symbol, values) in (
                    TECHNICAL_MEMBERSHIP_LITERALS):
                continue
            issues.append(CensusIssue(
                relative, int(getattr(node, "lineno", 0)),
                f"{function}::{symbol}", "LEXICON_INLINE_COMPARISON",
                "literal linguistic membership in audited boundary",
            ))

    # Generator expressions such as ``any(x in query for x in ('si',
    # 'yes'))`` hide the table in ``comprehension.iter`` rather than a Compare.
    for node in ast.walk(tree):
        if not isinstance(node, ast.comprehension):
            continue
        function = _enclosing_function(node, parents)
        if not function or function not in (
                AUDITED_LITERAL_ITERATION_FUNCTIONS.get(
                    relative, frozenset(),
                )):
            continue
        if (not isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))
                or not _literal_strings(node.iter)):
            continue
        target = node.target.id if isinstance(node.target, ast.Name) else "<item>"
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)),
            f"{function}::{target}", "LEXICON_INLINE_COLLECTION",
            "literal linguistic iterable in audited boundary",
        ))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "prefilter":
            for alias in node.names:
                declared.add(alias.asname or alias.name)
                if (alias.name.startswith("_")
                        and _is_suspect_name(alias.name)
                        and alias.name not in allowed):
                    issues.append(CensusIssue(
                        relative, int(getattr(node, "lineno", 0)), alias.name,
                        "LEXICON_PRIVATE_IMPORT",
                        "consumer must use a public semantic API",
                    ))
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "prefilter"
            and node.attr.startswith("_")
            and _is_suspect_name(node.attr)
            and node.attr not in allowed
        ):
            issues.append(CensusIssue(
                relative, int(getattr(node, "lineno", 0)), node.attr,
                "LEXICON_PRIVATE_ACCESS", "consumer must use a public semantic API",
            ))
    # An obsolete waiver is itself drift: the symbol it justified disappeared.
    for name, invariant in allowed.items():
        if name not in declared:
            issues.append(CensusIssue(
                relative, 0, name, "LEXICON_STALE_INVARIANT",
                f"unused {invariant.kind} exception: {invariant.reason}",
            ))
    for name, invariant in structural.items():
        if seen_structural[name] != 1:
            issues.append(CensusIssue(
                relative, 0, name, "LEXICON_STALE_INVARIANT",
                f"{invariant.kind} structural guard expected exactly 1 owner, "
                f"observed {seen_structural[name]}; {invariant.reason}",
            ))
    for owner, (_digest, reason) in VALUE_BOUND_STRING_MAPPINGS.items():
        if (owner[0] == relative
                and seen_value_bound_mappings[owner] != 1):
            issues.append(CensusIssue(
                relative, 0, owner[2], "LEXICON_STALE_INVARIANT",
                "value-bound mapping authority changed: expected exactly 1, "
                f"observed {seen_value_bound_mappings[owner]}; {reason}",
            ))
    for owner, (_digest, reason) in VALUE_BOUND_LANGUAGE_COLLECTIONS.items():
        if (owner[0] == relative
                and seen_value_bound_collections[owner] != 1):
            issues.append(CensusIssue(
                relative, 0, owner[2], "LEXICON_STALE_INVARIANT",
                "value-bound collection authority changed: expected exactly "
                f"1, observed {seen_value_bound_collections[owner]}; {reason}",
            ))
    for owner, (digests, reason) in VALUE_BOUND_NESTED_AFFINITIES.items():
        if owner[0] != relative:
            continue
        for digest in digests:
            if seen_nested_affinities[(owner, digest)] != 1:
                issues.append(CensusIssue(
                    relative, 0, owner[2], "LEXICON_STALE_INVARIANT",
                    "value-bound affinity authority changed: expected exactly "
                    f"1, observed {seen_nested_affinities[(owner, digest)]}; "
                    f"{reason}",
                ))
    for dynamic_owner, (expected_count, reason) in (
            VALUE_BOUND_DYNAMIC_AFFINITY_VALUES.items()):
        if dynamic_owner[0] != relative:
            continue
        actual_count = seen_dynamic_affinities.get(dynamic_owner, 0)
        if actual_count != expected_count:
            issues.append(CensusIssue(
                relative, 0, dynamic_owner[2], "LEXICON_STALE_INVARIANT",
                "dynamic affinity authority changed: expected "
                f"{expected_count}, observed {actual_count}; {reason}",
            ))
    for owner in VALUE_BOUND_AFFINITY_SCHEMA_VALUES:
        if owner[0] != relative:
            continue
        actual_count = seen_affinity_schema_values[owner]
        if actual_count != 1:
            issues.append(CensusIssue(
                relative, 0, owner[2], "LEXICON_STALE_INVARIANT",
                "affinity schema authority changed: expected exactly 1, "
                f"observed {actual_count}",
            ))
    for (owner_path, symbol), expected_digest in (
            VALUE_BOUND_DYNAMIC_AFFINITY_AUTHORITY_NODES.items()):
        if owner_path != relative:
            continue
        if (authority_node_digests.get(symbol) != expected_digest
                or authority_node_counts[symbol] != 1):
            issues.append(CensusIssue(
                relative, 0, symbol, "LEXICON_STALE_INVARIANT",
                "dynamic affinity authority node changed; native-ready "
                "derivation must be reviewed again",
            ))
    for fingerprint, actual_count in seen_affinity_mutations.items():
        expected_count, reason = (
            VALUE_BOUND_AFFINITY_MUTATION_FINGERPRINTS[fingerprint]
        )
        if actual_count > expected_count:
            issues.append(CensusIssue(
                relative, 0, "<affinity-mutation>",
                "LEXICON_STALE_INVARIANT",
                "affinity mutation authority duplicated: expected at most "
                f"{expected_count}, observed {actual_count}; {reason}",
            ))
    for fingerprint, (expected_count, reason) in (
            VALUE_BOUND_AFFINITY_MUTATION_FINGERPRINTS.items()):
        if VALUE_BOUND_AFFINITY_MUTATION_OWNERS[fingerprint] != relative:
            continue
        actual_count = seen_affinity_mutations[fingerprint]
        if actual_count != expected_count:
            issues.append(CensusIssue(
                relative, 0, "<affinity-mutation>",
                "LEXICON_STALE_INVARIANT",
                "affinity mutation authority changed: expected "
                f"{expected_count}, observed {actual_count}; {reason}",
            ))
    for label, observed, overrides in (
        (
            "executable", seen_bound_executable,
            VALUE_BOUND_EXECUTABLE_FINGERPRINT_MULTIPLICITY,
        ),
        (
            "technical", seen_bound_technical,
            VALUE_BOUND_TECHNICAL_FINGERPRINT_MULTIPLICITY,
        ),
        (
            "inline", seen_bound_inline,
            VALUE_BOUND_INLINE_FINGERPRINT_MULTIPLICITY,
        ),
    ):
        for fingerprint, actual_count in observed.items():
            expected_count = overrides.get(fingerprint, 1)
            if actual_count > expected_count:
                issues.append(CensusIssue(
                    relative, 0, f"<{label}-literal-waiver>",
                    "LEXICON_STALE_INVARIANT",
                    "exact legacy waiver duplicated: expected at most "
                    f"{expected_count}, observed {actual_count}",
                ))
    return issues


def _discovered_consumer_paths(root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in _DISCOVERY_EXCLUDED_DIRS for part in relative.parts):
            continue
        if relative.as_posix() in _DISCOVERY_EXCLUDED_PATHS:
            continue
        if relative.as_posix() in KNOWN_DETECTION_SEED_PATHS:
            continue
        paths.append(relative.as_posix())
    return tuple(sorted(paths))


def _unique_seed_registration_boundaries(
    tree: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
) -> tuple[frozenset[ast.Call], frozenset[ast.AST]]:
    """Return unique actual registration calls and their corpus iterators."""

    candidates: list[tuple[ast.Call, str]] = []
    concept_counts: Counter[str] = Counter()
    bindings = _constant_string_bindings_by_scope(tree, parents)
    bound_aliases = {
        (_lexical_scope(node, parents), name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (name := _assigned_name(node)) is not None
        and node.value is not None
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "_dl"
        and node.value.attr == "register"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        direct_register = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_dl"
            and node.func.attr == "register"
        )
        bound_register = (
            isinstance(node.func, ast.Name)
            and (_lexical_scope(node, parents), node.func.id) in bound_aliases
        )
        if not direct_register and not bound_register:
            continue
        concept = _folded_string_in_scope(node.args[0], parents, bindings)
        kind = _literal_value(node.args[1])
        if kind not in {"mapping", "phrases", "regex"}:
            continue
        if isinstance(concept, str) and concept.strip():
            identity = "literal:" + concept
        elif isinstance(node.args[0], (ast.Name, ast.JoinedStr)):
            # A loop may derive one scalar concept from each corpus row.  The
            # call site remains unique and the iterable is admitted separately
            # below; no surrounding function receives an exemption.
            identity = "dynamic:" + ast.dump(
                node.args[0], annotate_fields=True, include_attributes=False,
            )
        else:
            continue
        candidates.append((node, identity))
        concept_counts[identity] += 1
    calls = frozenset(
        node for node, concept in candidates if concept_counts[concept] == 1
    )
    iterators: set[ast.AST] = set()
    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.AsyncFor)):
            continue
        if any(candidate in calls for statement in loop.body
               for candidate in ast.walk(statement)
               if isinstance(candidate, ast.Call)):
            iterators.add(loop.iter)
    return calls, frozenset(iterators)


def _seed_site_is_declaration_or_registration(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
    registration_calls: frozenset[ast.Call],
    registration_iterators: frozenset[ast.AST],
) -> bool:
    """Admit seed corpus declaration/registration, never a direct gate."""
    ancestor = node
    while True:
        if ancestor in registration_calls:
            # Only the registration call node is corpus framing.  A comparison
            # or resolver nested in one of its arguments is executable code and
            # must still be reported.
            return ancestor is node
        if ancestor in registration_iterators:
            return True
        if ancestor not in parents:
            break
        ancestor = parents[ancestor]
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
        if isinstance(ancestor, (ast.Assign, ast.AnnAssign)):
            value = ancestor.value
            return value is not None and _literal_value(value) is not None
    return False


def _scan_known_seed(path: Path, relative: str) -> list[CensusIssue]:
    """Validate that a known seed owns corpus, not a private resolver gate."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [CensusIssue(
            relative, int(getattr(exc, "lineno", 0) or 0), "",
            "LEXICON_PARSE", str(exc),
        )]
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    issues: list[CensusIssue] = []
    registration_calls, registration_iterators = (
        _unique_seed_registration_boundaries(tree, parents)
    )
    inline_sites = _inline_executable_literal_sites(tree, relative, parents)
    observed_bound = Counter(
        fingerprint
        for node, function, _kind, fingerprint in inline_sites
        if not _seed_site_is_declaration_or_registration(
            node, parents, registration_calls, registration_iterators,
        )
        and fingerprint in VALUE_BOUND_SEED_GATE_FINGERPRINTS
    )
    for node, function, kind, fingerprint in inline_sites:
        if _seed_site_is_declaration_or_registration(
                node, parents, registration_calls, registration_iterators):
            continue
        expected_count = VALUE_BOUND_SEED_GATE_FINGERPRINTS.get(fingerprint)
        if (expected_count is not None
                and observed_bound[fingerprint] == expected_count):
            continue
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)),
            f"{function}::<seed-{kind}>", "LEXICON_SEED_GATE",
            "known seed may declare/register corpus but may not execute a "
            "literal resolver or language gate",
        ))
    for node, function, _fingerprint in _affinity_mutation_sites(
            tree, relative, parents):
        if _seed_site_is_declaration_or_registration(
                node, parents, registration_calls, registration_iterators):
            continue
        issues.append(CensusIssue(
            relative, int(getattr(node, "lineno", 0)),
            f"{function}::<seed-affinity-mutation>", "LEXICON_SEED_GATE",
            "known seed may not construct or mutate executable affinity",
        ))
    return issues


def _seen_bound_seed_gate_fingerprints(root: Path) -> Counter[str]:
    observed: Counter[str] = Counter()
    for relative in KNOWN_DETECTION_SEED_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        registration_calls, registration_iterators = (
            _unique_seed_registration_boundaries(tree, parents)
        )
        observed.update(
            fingerprint
            for node, function, _kind, fingerprint
            in _inline_executable_literal_sites(tree, relative, parents)
            if not _seed_site_is_declaration_or_registration(
                node, parents, registration_calls, registration_iterators,
            )
            and fingerprint in VALUE_BOUND_SEED_GATE_FINGERPRINTS
        )
    return observed


def _seen_bound_fingerprints(
    root: Path, paths: Iterable[str],
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    """Re-find exact exceptions so deleted waivers cannot become stale."""
    executable: Counter[str] = Counter()
    technical: Counter[str] = Counter()
    inline: Counter[str] = Counter()
    for relative in paths:
        path = root / relative
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path),
            )
        except (OSError, SyntaxError):
            continue
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        allowed = {
            entry.symbol
            for entry in TECHNICAL_INVARIANTS.get(relative, ())
        }
        inline.update(
            fingerprint
            for _node, _function, _kind, fingerprint
            in _inline_executable_literal_sites(tree, relative, parents)
            if fingerprint in VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS
        )
        for node in ast.walk(tree):
            if (not isinstance(node, (ast.Assign, ast.AnnAssign))
                    or node.value is None):
                continue
            name = _assigned_name(node)
            if not name:
                continue
            function = _enclosing_function(node, parents)
            literal = _literal_value(node.value)
            if _is_string_literal_container(literal):
                fingerprint = _executable_container_fingerprint(
                    relative, function, name, literal,
                )
                if fingerprint in VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS:
                    executable[fingerprint] += 1
            if name in allowed and _contains_literal_words(node.value):
                fingerprint = _technical_literal_fingerprint(
                    relative, function, name, node.value,
                )
                if fingerprint in VALUE_BOUND_TECHNICAL_LITERAL_FINGERPRINTS:
                    technical[fingerprint] += 1
    return executable, technical, inline


def scan_runtime(
    root: Path, paths: Iterable[str] | None = None,
) -> list[CensusIssue]:
    issues: list[CensusIssue] = []
    # Known seeds own corpus, but their exact paths do not grant permission to
    # host resolver gates.  Validate their structure instead of skipping them.
    for relative in sorted(KNOWN_DETECTION_SEED_PATHS):
        seed_path = root / relative
        if not seed_path.exists():
            continue
        issues.extend(_scan_known_seed(seed_path, relative))
    full_discovery = paths is None
    selected = _discovered_consumer_paths(root) if full_discovery else tuple(paths)
    for relative in selected:
        if relative in KNOWN_DETECTION_SEED_PATHS:
            continue
        path = root / relative
        if not path.is_file():
            issues.append(CensusIssue(
                relative, 0, "", "LEXICON_CONSUMER_MISSING",
                "censused runtime consumer is missing",
            ))
            continue
        issues.extend(scan_file(path, relative_path=relative))
    if full_discovery and (root / "executable_lexicon_census.py").is_file():
        seen_executable, seen_technical, seen_inline = _seen_bound_fingerprints(
            root, selected,
        )
        missing_executable = (
            VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS
            - set(seen_executable)
        )
        missing_technical = (
            VALUE_BOUND_TECHNICAL_LITERAL_FINGERPRINTS
            - set(seen_technical)
        )
        missing_inline = (
            VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS - set(seen_inline)
        )
        if missing_executable:
            issues.append(CensusIssue(
                "executable_lexicon_census.py", 0,
                "VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS",
                "LEXICON_STALE_INVARIANT",
                f"{len(missing_executable)} exact executable-container "
                "fingerprint(s) are no longer owned by runtime source",
            ))
        if missing_technical:
            issues.append(CensusIssue(
                "executable_lexicon_census.py", 0,
                "VALUE_BOUND_TECHNICAL_LITERAL_FINGERPRINTS",
                "LEXICON_STALE_INVARIANT",
                f"{len(missing_technical)} exact technical-literal "
                "fingerprint(s) are no longer owned by runtime source",
            ))
        if missing_inline:
            issues.append(CensusIssue(
                "executable_lexicon_census.py", 0,
                "VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS",
                "LEXICON_STALE_INVARIANT",
                f"{len(missing_inline)} exact inline-literal fingerprint(s) "
                "are no longer owned by runtime source",
            ))
        multiplicity_mismatches = []
        for label, allowed_fingerprints, observed, overrides in (
            (
                "executable", VALUE_BOUND_EXECUTABLE_CONTAINER_FINGERPRINTS,
                seen_executable,
                VALUE_BOUND_EXECUTABLE_FINGERPRINT_MULTIPLICITY,
            ),
            (
                "technical", VALUE_BOUND_TECHNICAL_LITERAL_FINGERPRINTS,
                seen_technical,
                VALUE_BOUND_TECHNICAL_FINGERPRINT_MULTIPLICITY,
            ),
            (
                "inline", VALUE_BOUND_INLINE_LITERAL_FINGERPRINTS,
                seen_inline, VALUE_BOUND_INLINE_FINGERPRINT_MULTIPLICITY,
            ),
        ):
            for fingerprint in allowed_fingerprints:
                expected = overrides.get(fingerprint, 1)
                actual = observed[fingerprint]
                if actual != expected:
                    multiplicity_mismatches.append(
                        (label, fingerprint, expected, actual),
                    )
        if multiplicity_mismatches:
            issues.append(CensusIssue(
                "executable_lexicon_census.py", 0,
                "VALUE_BOUND_FINGERPRINT_MULTIPLICITY",
                "LEXICON_STALE_INVARIANT",
                f"{len(multiplicity_mismatches)} exact legacy waiver "
                "cardinality record(s) changed",
            ))
        missing_gate_authorities = (
            set(LEGACY_LITERAL_GATE_FILE_AUTHORITIES) - set(selected)
        )
        if missing_gate_authorities:
            issues.append(CensusIssue(
                "executable_lexicon_census.py", 0,
                "LEGACY_LITERAL_GATE_FILE_AUTHORITIES",
                "LEXICON_STALE_INVARIANT",
                f"{len(missing_gate_authorities)} exact module gate "
                "authority record(s) are no longer owned by runtime source",
            ))
        seen_seed_gates = _seen_bound_seed_gate_fingerprints(root)
        changed_seed_gate_authorities = {
            fingerprint
            for fingerprint, expected_count
            in VALUE_BOUND_SEED_GATE_FINGERPRINTS.items()
            if seen_seed_gates[fingerprint] != expected_count
        }
        if changed_seed_gate_authorities:
            issues.append(CensusIssue(
                "executable_lexicon_census.py", 0,
                "VALUE_BOUND_SEED_GATE_FINGERPRINTS",
                "LEXICON_STALE_INVARIANT",
                f"{len(changed_seed_gate_authorities)} exact seed-gate "
                "authority/cardinality record(s) changed",
            ))
    return issues


def main() -> int:
    root = Path(__file__).resolve().parent
    issues = scan_runtime(root)
    for issue in issues:
        print(
            f"{issue.path}:{issue.line}: {issue.code}: "
            f"{issue.symbol}: {issue.message}"
        )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
