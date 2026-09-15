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

Production follow-up publication is pending below; the owner has authorized
incremental local/public commits with English messages.
