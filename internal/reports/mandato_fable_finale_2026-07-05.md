# MANDATO FABLE 2026-07-05 — Report finale complessivo

**Mandato**: `internal/fable_mandate_2026-07-05.md` (4 aree + Fase 0, una alla volta, cancello §A + ok Roberto). **Esito: TUTTE LE AREE CONSOLIDATE in un giorno.** Branch `session/detection-lexicon-i18n` (non pushato), prod live, ~50 commit.

## Percorso e cancelli (tutti passati con ok Roberto)
| Tappa | Esito | ADR/report |
|---|---|---|
| **Fase 0** | 4/4 (residuo committato; find no-cartelle; Issue-B form; 697 sul device) | `fase0_mandato_fable_2026-07-05.md` |
| **Area 1 CP1·M0** | idempotenza guard MISURATA (33/33) + contratto `GUARD_PIPELINE` + 2 bug veri scovati dal T4 | `area1_cp1_guard_idempotency_2026-07-05.md` |
| **Area 1 · cache-validity** (deviazione voluta da Roberto) | firme del mondo su L0/L1/proposer, verifica a lettura — chiude il TODO CRUCIALE negative-path-cache | **ADR 0182** · `area1_cache_validity_2026-07-05.md` |
| **Area 1 CP2·M2** | Finalizer unico (S5 chiuso, blocchi gemelli eliminati) | `area1_cp2_finalizer_2026-07-05.md` |
| **Area 2** | remote executors COMPLETI sul Windows REALE: shim ad albero, lazy-gw, read-only+MUTANTI con undo round-trip sullo stesso device; **self-update automatico** (3 migrazioni consecutive del PC senza interventi); versione client in UI; 11 executor device_ok; **undo di prod RIPRISTINATO** (era morto da af6c7b8) | **ADR 0183, 0184** · `area2_remote_executors_2026-07-05.md` |
| **Area 3 W1** | learning-loop: seed shadow da turni costosi ripetuti + lacuna→proposta (triage, anti-resurrezione) + review notturna | **ADR 0185** · `area3_learning_loop_2026-07-05.md` |
| **Area 4** | manutenzione=comandi NL schedulati (watcher ritirato formalizzato, 825 run/0 fail del flusso reale, confine comando/bespoke/timer documentato, bonifica) | **ADR 0186** · `area4_maintenance_2026-07-05.md` |

## Interventi live fuori scaletta (report Roberto in sessione)
Bug zip-line (resume form = turno completo: gallery firmata+breadcrumb+meta, 2 giri) · UX submit form (disable+spinner) · bottone 📎 + drag&drop verificato e2e · `get_processes` cross-platform+onesto (→ ha scovato SystemRoot mancante) · fix path Windows/args/sticky vari (mattina/pomeriggio).

## Debito interno SANATO strada facendo
undo senza scrittore (af6c7b8) · import gw eager su 6 dispatcher · `config.Path.home()` crash in sandbox · test lifecycle che registravano fuori dal mondo del turno · side-effect load_catalog nelle firme · riga scheduler morta + pyc orfani.

## Verifiche differite (mattina 6/7)
1. Seed shadow naturale su `find|issues` (notturno ~01:22): `SELECT id,shadow FROM autopaths WHERE intent_sig LIKE 'find|issues%'`.
2. Primo giro `learning_loop_review` nel notturno (conteggi log).
3. Notturno `change_intent_materialize` con eventuali accept di Roberto dal triage.

## Coda follow-up (approvata da Roberto, ordine mio suggerito)
1. Hint forma-path→host nel placement (sticky manda path server al PC).
2. Telegram media sul resume dialog (sendMediaGroup).
3. Header describe «Stato server» su dati device (presentazione).
4. Blob round-trip per delete remoto (ADR 0183 D3) · ACL scrittura Windows (W4).
5. Canale wheels per xlsx/ocr sul device · similarity di scena (non solo volti).
6. Area-1 CP3/CP5/CP6 (consolidamento guard, grammar-on-args, lessici §7.3) · analisi Telos a secco §G · audit formale adapter.
