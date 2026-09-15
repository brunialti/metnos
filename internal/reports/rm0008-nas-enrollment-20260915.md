# Tutor follow-up: NAS access and enrollment independence

## Scope

The owner requested a useful Quick Tour example for failed Tutor turn
`fbe55648722741fc`, replacing a less useful example rather than growing the
tour. They also asked whether person enrollment depends on photo indexing.
English commit messages and incremental local/public Git publication are
required. No new NAS mount, archive scan, face enrollment or index rebuild on
the owner's data is part of this verification.

The preceding work was deployed and checked separately in release 52, with
public commit `8dc7f0515f9b6efacbb3a675b81858ee92848600`; see the release ledger.

## Evidence and documentation decisions

Read-only observation `run-3c4lnz2f` identifies the exact original question:
“come faccio a fare il mount di una cartella di un nas in metnos”. Tutor
reported `lacuna`, `composer_insufficient`, and selected a mail-account source.
The question was informational and no operation was performed.

The IT/EN Quick Tour's existing Tutor section now uses the NAS question in
place of the configured-embedder example. All six example sections remain.
The explanation covers SMB/CIFS on a Linux server, required share/path data,
the service account's access, protected credential entry, ordinary policy
review, reuse of an existing mount, reboot persistence as a separate setting,
and the distinction between mounting and indexing. The example uses a reserved
documentation domain, requests read-only access, and never presents passwords
or shell commands as text to paste.

Sources checked: `runtime/system/admin.py` (admitted administrative contract,
credential placeholders and the actual protected form), `runtime/path_alias.py`
(relative-path semantics) and `runtime/workspace_policy.py` (access scope).
This is documentation of the existing interface, not a new mount capability
or a claim that an arbitrary NAS has been mounted end to end.

`runtime/published_docs.py` discovers both Quick Tour HTML masters without a
special registry entry. `runtime/tutor/sources.py` reads semantic paragraph
elements, not arbitrary presentation divs; the new answer therefore uses a
styled paragraph. Tests verify that the NAS instructions and their limits
actually survive source extraction and retain public citation identity.
Both PDFs were regenerated; each is 12 A4 pages. The Italian NAS page was
visually checked and fits on page 5 without pushing content into the next
example.

## Enrollment result

Enrollment and archive indexing are independent preparation operations:

- `set_persons` detects faces in supplied examples and writes the person
  registry; those examples need not belong to the searchable archive.
- `create_images_indices._build_entry` detects and stores facial features
  from archive photos without consulting the person registry.
- `find_images_indices._filter_unified` reads the current registry through
  `resolve_face_embeddings_for_name` at search time and compares it with
  indexed face vectors. No person name is permanently baked into those vectors.

The new regression creates an index first, enrolls a synthetic identity later,
and then replaces that identity's reference. Both searches return the correct
different photo, and all index files remain byte-for-byte unchanged. Registry
storage and search are real; face vectors and example bytes are synthetic.
This tests the lifecycle relationship, not face-recognition accuracy.

Both photo-indexing guides explain that enrollment may happen before or after
indexing without rebuilding the archive. They also retain the limitation:
the index must already contain usable face features; enrollment does not
analyze new photos or recover faces that were not detected. Index updates
remain LRE work. No model or runtime behavior was changed for this follow-up.

## Verification status

The initial related group passed 62 tests with two documentation-segmentation
failures: an existing section had grown beyond the compiler's bounded chunk
size. A dedicated heading now separates incremental/full-update instructions
from the explanation of faster searches, preserving the original test that
keeps the latter claim and its conditions together. The rerun passed all 21
targeted documentation/enrollment checks. Public-document validation admits
99 documents; all three generated-reference checks pass.

The complete related group now passes all 64 tests. Real HTTP/local-model
Tutor checks also pass, with a fresh fixture signing identity, no verification
bypass, and a namespace hiding production Birth state. Compilation took
143.839 seconds. Evidence: `/tmp/rm0008-tutor-candidate-30zjzsig/result.json`.

| Question | Language | Turn | Seconds | Result |
| --- | --- | --- | ---: | --- |
| Exact reported NAS question | IT | bbbd193511a8435e | 9.049 | Grounded, Quick Tour sources |
| NAS mount instructions | EN | 112b317be4ba48c2 | 10.786 | Grounded, Quick Tour sources |
| Enrollment after indexing | IT | cd95d161d13645f9 | 7.623 | Grounded, photo guide sources |
| Enrollment after indexing | EN | 7db2d0da504042b4 | 8.171 | Grounded, photo guide sources |

Every turn has `mode=tutor`, `outcome=fondata`, nonempty source IDs, no evidence
gap and zero executed steps. Both enrollment answers explicitly state the
order independence, lack of required rebuild, and the limits on new photos
and undetected faces. NAS answers describe the existing natural-language
request and protected credential/review flow, not an executed mount.

## Publication

Local implementation/documentation commit:
`f7c5d8fd` — `Explain NAS access and independent person enrollment in public guides`.
No runtime Python changed: both reviewed Python roots remain those of release 52.
The prepared distribution has 1,771 files and census
`54378ae1a64864f25c4ae419702c4e6b7a9e18897ed55ea0cd2edad1e79f95c9`.

Static-only website deployment completed at
`https://557f2efc.mykleos.pages.dev`, uploading six changed assets. All six
origin responses match the local bytes. On `metnos.com`, both PDFs and both
LRE guides also match exactly; the two Quick Tour HTML responses differ only
by Cloudflare's email-obfuscation markup/script. Decoding just that verified
transformation restores exact byte equality. No Cloudflare setting was changed.
The raw comparison failure was retained and explained, not treated as a stale
deployment or hidden with a broad text-only comparison.

The public export preflight passes with zero GII findings.

### Release 53 and live acceptance

The ordinary cycle completed with exit 0 and no changed runtime components.
Source: `sha256:d3074f1472563eb819fa24105d6ae9452c192e73de7aadf5bf7e9987c2a93ac5`;
build: `sha256:277b5c7ff596ad06416cd8946461146933af5d7f7a97a59b87f4a3403260b3f5`;
head: `sha256:e63bc648309c87f43ba9ba39a9f1a22456b0361f2bf14b707cfb8f0cb4f17743`.
Evidence: `/var/lib/metnos-admin/rm0008-cycle-evidence-277b5c7ff596ad06`.

Readiness `run-290p15qu` confirms ready HTTP/LRE and automatic compilation of
the new Tutor catalog, digest
`36469c8b4a8f20f3c5c56756e215db5d9721b2f430b09258e2377611fe034dd0`.
No independent live catalog edit, key replacement or forced compilation was
used; sources arrived through the ordinary release.

- Exact NAS replay: `be4e00789aac49eb`, 8.202 seconds, `fondata`, three Quick
  Tour source IDs, no evidence gap and zero executed steps. Evidence:
  `run-tn9l_j1s`.
- Enrollment replay: `5974c98f71ea4be3`, 5.936 seconds, `fondata`, four photo
  guide source IDs, no evidence gap and zero executed steps. The answer
  correctly explains enrollment after indexing and its limits. Evidence:
  `run-9_sc8t5b`.

The two-question harness initially read the second turn's log before its
record was available and ended with `turn_not_unique`. A bounded read-only
continuation located exactly one enrollment record after the known NAS turn,
under the same release head. It made zero new HTTP requests: neither the
successful NAS test nor the already executed enrollment question was repeated.
This harness failure is retained separately from the two successful product
outcomes.

The second incremental public commit is
`ab9c6a27c9a2cca2c859de6d4229dfa464cdd4bc`:
`Document NAS mounting and enrollment independent of photo indexing`.
The clean local public mirror and GitHub `refs/heads/main` match exactly.
It follows `8dc7f0515f9b6efacbb3a675b81858ee92848600`; both public messages
are in English and no public history was rewritten. Both GII gates passed
with zero findings. Private reports and coordination material are excluded.

The requested documentation and verification work is complete. Large-archive
performance remains for the owner's planned test; no real NAS mount, photo
scan, person enrollment or index deletion was performed for this follow-up.
