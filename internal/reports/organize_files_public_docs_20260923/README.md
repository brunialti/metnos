# organize_files public documentation — deployment evidence

Date: 23 September 2026. Owner: Codex, organize_files documentation task.
Coordination: R-007, with the executor publication gate still owned by R-003/RM-0008.

## Final availability update

RM-0008 subsequently closed R-003 and R-007 with an actual Birth receipt,
catalog readback and post-restart checks in release 77. `organize_files` is now
in exercise. The public guides were therefore updated from the truthful
activation-pending state recorded below to the active workflow, without changing
their safety qualifications or the documented bounded-summary limitation.

- Active documentation commits: `5739b0fe` and `6b8d875f` on
  `codex/organize-files-public-docs`; both are pushed to the public Git remote.
- Public guides: <https://metnos.com/it/organize_files> and
  <https://metnos.com/en/organize_files>.
- Final Pages deployment: `dd683f03-66cb-4db0-a486-f32f63bc08f1`, source
  `6b8d875`, <https://dd683f03.mykleos.pages.dev>.
- The upload starts from the newer production deployment
  `1eb32901-577a-48c5-8604-a5155a2057e7` (source `1285614`), then overlays only
  the two guides, shared navigation, Quick Tour HTML/PDF and sitemap. This
  preserves the concurrently published LRE and web-interface documentation.
- The resulting artifact has 128 files and 103 canonical documents. All 126
  HTTP-served bodies match on both the immutable deployment and `metnos.com`;
  four browser cases on each endpoint cover IT/EN at 390px and 1440px with no
  script errors or horizontal overflow.
- The focused source checks cover 1,986 links, 32 local browser cases and Tutor
  ingestion: each guide contributes 29 blocks and each Quick Tour one new block.
  No edited page has a link failure. The 41 previously recorded findings remain
  outside the edited pages.
- The active wording is indexable by Tutor; no ad-hoc product string, token,
  administrative command or Birth procedure was added. No executor, runtime,
  manifest, signature, service, product seed or main checkout was modified.

An intermediate Pages receipt `49aea2a9-b8b8-4ac5-b615-5e50908a1b56` had the
same bytes but was immediately superseded because the full source SHA supplied
to the provider did not match the actual commit after its visible prefix. The
final deployment uploaded zero changed assets and attaches the exact commit
`6b8d875f222950f124cdcb5ee0a29ed7c216f537`. Machine-readable final evidence
uses the `*-active*` filenames adjacent to this report; the files without that
suffix preserve the initial activation-pending deployment described below.

## Outcome and scope

The bilingual public documentation has been deployed successfully. This is a
static website deployment, **not** an executor release or evidence of runtime
availability. R-003 and R-007 remain open in the main coordination board at the
final read. The public guides explicitly say activation is in progress.

- Documentation commit: `4ef2d8bf`, branch `codex/organize-files-public-docs`.
- Worktree: `/opt/metnos/.claude/worktrees/organize-files-public-docs`.
- Public Italian guide: <https://metnos.com/it/organize_files>.
- Public English guide: <https://metnos.com/en/organize_files>.
- Pages deployment: `e96949ed-25d6-4399-a46c-9374bc680a68`.
- Immutable URL: <https://e96949ed.mykleos.pages.dev>.
- Provider receipt: production environment, branch `main`, source `4ef2d8b`.
  This is the Pages deployment branch, not a checkout/merge of the main repository.

All tracked edits were made in our own worktrees. No runtime, executor, product
language seed, manifest, signature, service or main-checkout file was modified.
No executor was published, no release created, no Metnos service restarted.
This report and the R-007 appendix live on our existing
`codex/organize-files-handoff` branch, which already contains that board entry.

## Published changes

Eight documentation files changed: new IT/EN guides; localized entries in the
existing wiki navigation resource; IT/EN Quick Tour HTML and rebuilt PDFs; sitemap.
The only shared browser behavior change normalizes `.html` and extensionless URLs
through the same existing canonical-path helper, fixing current-page selection
and language switching on Pages clean URLs.

The guides explain organizing versus moving versus sorting results; explicit
source/reference scope; exact-content duplicates; non-mutating preview; forms
on web chat and linked from Telegram; no authorization for zero effects; consent
bound to the frozen plan; the execution receipt; safe undo; collisions without
overwrite; Linux-local/same-filesystem/existing-directory restrictions; and stale
file refusal. Example scopes are explicit, with no implicit reference to outputs
of an earlier completed query. No domain-specific runtime rule was introduced.

IT and EN share page structure, anchors, reciprocal language links and navigation
keys. No internal authorization material, administrative command or Birth
procedure was added to the public text. Existing public content outside this
change was retained, not rewritten.

The existing `tutor-exclude` convention keeps the pending operational steps out
of the current Tutor corpus. Both guide titles and the explicit pending-status
lead are indexable. The Quick Tour's existing Tutor blocks are unchanged. This
checks source ingestion, not a production Tutor rebuild or a live-model answer.

## Verification

- Public document inventory/build: 101 canonical HTML documents and 125 files
  in the complete distribution; locale/canonical/hreflang validation passed.
- 1,986 local links inspected. No broken links on the new/edited HTML pages.
  The checker records 41 pre-existing findings: one is the valid `/` redirect,
  leaving 40 existing missing-file/fragment references elsewhere on the site.
  They were not introduced by this change and were not silently repaired.
- 32 local browser cases: IT/EN, guide/Quick Tour, `.html`/clean URL, widths
  320/390/768/1440. Navigation, language selector, mobile menu and Escape passed;
  no JavaScript/network errors. The guides have no horizontal overflow. A 17px
  Quick Tour overflow at 320px was reproduced unchanged on baseline `581fd197`.
- Existing PDF builder completed in both languages; extracted PDF text contains
  the localized pending guide reference. IT: 1,860,464 bytes; EN: 1,849,904 bytes.
- `node --check docs/assets/wiki-shell.js` and `git diff --check` passed.
- Post-deploy: all 123 served file bodies matched the artifact. 114 matched byte
  for byte; nine matched after reversing only the existing Cloudflare email
  obfuscation. The initial byte-only assertion exposed that edge transformation,
  not a changed document. `_headers`/`_redirects` are deployment configuration,
  not HTTP-served files, and were verified against the source baseline instead.
- Four live browser cases passed: both languages at 390px and 1440px, HTTP 200,
  correct language/current navigation/pending lead, no overflow or script errors.
  Live mobile and local desktop screenshots were visually inspected.
- Fresh isolated checks against the integrated main-checkout code, read only:
  `test_empty_preview_does_not_create_authorization_or_plan` passed, and all five
  explicit-consent-marker cases passed. These use temporary files/state, not
  personal photos. We did not rerun or claim ownership of RM-0008's full E2E suite.

Machine-readable evidence is adjacent: `checks.json`, `artifact.json`,
`live-files.json`, `live-browser.json`. `check_docs.py` and `verify_deploy.py`
preserve the read-only check drivers. Screenshots and the complete upload remain
in the documentation worktree under `.wrangler/organize-docs-checks/` and
`.wrangler/organize-docs-site/`; they are not product state or public assets.

Re-run the local checks using the project virtual environment, passing the
documentation worktree and an isolated output directory to `check_docs.py`.
`verify_deploy.py` checks the recorded worktree artifact against the public site;
its paths intentionally identify this particular deployment evidence.

## Deployment baseline and safety

The main/handoff documentation was older than the live site. The dedicated docs
branch starts at `581fd197`, the source of the preceding Pages deployment
`9e4d2263-709a-4b74-8b89-cb5f34225e5c`. The upload was assembled from that immutable
deployment, its unchanged source configuration, and the eight changed files.
Two generated public interface pages were retained. Two already-published
certification pages differed from their Git source and were preserved byte for
byte; this task neither authored nor re-certified those claims. Of 123 served
file bodies, 115 are unchanged from the previous deployment and eight are new
or updated. In `artifact.json`, the two configuration files are marked added
relative to the HTTP snapshot only; they are unchanged from source.

The existing repository wrapper hardcodes `/opt/metnos` and performs product
catalog/Tutor generation. Running it would violate this task's explicit scope.
Following the Cloudflare Pages/Wrangler skill, the same installed CLI and existing
project credentials were used for a **static-only** upload from our own artifact.
No deployment script, credentials, project settings or publication procedure was
modified. The previous production deployment was checked again immediately before
upload to avoid overwriting a concurrent publication. Git was clean at upload.

## Actual behavior, limitations and handoff

The main R-007 update records integration `59d02eaa` and pre-publication acceptance
(`8ae9777c`). Its fix for unnecessary authorization is present and the isolated
probe above passes; the earlier development-handoff defect is therefore resolved.

One substantive distinction remains: the current confirmation form exposes a
bounded summary. The consent fixture reports **50 shown of 151 total actions**,
with truncation correctly indicated; applying the full frozen plan is tested,
but that does not prove the user can inspect every action before consenting.
The public guide explicitly calls out this outstanding full-plan consultation
check rather than presenting it as delivered.

Undo updates the existing receipt/journal and returns restored/failed outcomes;
it is not an unconditional guarantee of recovery or necessarily a separate new
receipt document. External changes may prevent a safe restore. The guide keeps
this distinction and recommends an independent backup.

RM-0008 owns the remaining action: verify/provide full-plan consultation before
consent, record successful executor publication in R-007 under the R-003 gate,
then update availability coherently in both guides, navigation labels, Quick
Tour HTML/PDF and Tutor inclusion. Roberto asked not to have to reopen this docs
task; this availability follow-up is therefore handed to the release owner here,
not represented as an already successful activation. Do not mark R-007 closed
on the strength of the website deployment alone.

Integrate commit `4ef2d8bf` **by content on the current documentation baseline**.
Do not replace the newer published site with the stale main/handoff docs tree.
The unrelated 40 link findings and narrow-screen Quick Tour overflow can be
handled by the public documentation maintainer separately; proof of closure is
zero corresponding link findings and no overflow at 320px, in both languages.
