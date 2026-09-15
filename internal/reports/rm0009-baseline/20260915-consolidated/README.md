# RM-0009 — ripartenza sulla base consolidata RM-0008

Data: 15 settembre 2026. Codice sorgente fissato al commit
`1c308922839f7659a3cf54d988f995bba0f215d6`.

La manutenzione RM-0008 pertinente e conclusa e committata. RM-0009 prosegue
nel worktree isolato `/opt/metnos/.claude/worktrees/rm0009-development`, branch
`codex/rm0009-development`; nessun codice di prodotto o servizio e modificato
da questa consegna. Consolidamento sorgente **non** significa certificazione
RM-0008/F5-F6 o autorizzazione delle nuove capacita.

La [roadmap](../../../roadmap/RM-0009-crescita-allineata-delle-capacita.md)
resta normativa. Questi sono rapporti e preparatori, non una seconda roadmap.
I rapporti precedenti in `../20260915/` rimangono fotografie storiche: non
sono stati riscritti per farli apparire riferiti al commit finale.

Ricontrollo finale: il ramo sorgente e avanzato a `5c1220ac25a1899b0d88aac5540fb0ab913db5ea`
con due commit sulla licenza MIT/pubblicazione. Il confronto rileva anche nuovi
digest dei manifest builtin e pin di revisione sorgente: non e equivalenza dei
byte. Questa consegna resta volutamente fissata a `1c308922`; prima del freeze
G0.6 occorre acquisire il seguito e rigenerare gli inventari sulla nuova base.
Non sono stati retrodatati i digest o estese le prove al commit successivo.

## Risultati disponibili

| Artefatto | Che cosa dimostra | Che cosa non dimostra |
|---|---|---|
| `preflight.json` | commit, area isolata, proprieta dei soli preparatori, stato letto dei servizi | versioni DB installate, snapshot P0 o restart riuscito |
| `inventory-data.json` / `.md` | 57 archivi/superfici, 8 ingressi crescita, 3 writer TurnLog; 110 input del commit | completezza universale o purge gia implementato |
| `inventory-security.json` / `.md` | 2407 input, 33 gruppi FS-A, 13 FS-B, 10 percorsi F6; due scansioni concordi | sicurezza o equivalenza eseguite, inventario installed completo |
| `birth-recheck.md` | API/receipt attuali e contratto futuro necessario | accordo G0.3 o proof F5/FS-A |
| `coordinator-decisions.md` | esame puntuale, correzioni accettate e contratti ancora aperti | G0.6 concluso |
| `work-manifest.draft.json` | 145 incarichi candidati, barriere espanse, mapping dei 57 store e file condivisi | incarichi assegnabili: tutti restano bloccati al freeze |
| `inventory-check-review.md` e `inventory-check-review-followup.md` | contestazione e correzione dei falsi verdi del nuovo preparatore | review finale di RM-0009 |
| `decision-clarifications-review.md` | review parziale delle correzioni su campioni, formule e domande | dry-run G0.7 o review G0.8 |
| `verification.json` | risultati finali, digest e confini della verifica del coordinatore | approvazione o certificazione del prodotto |

## Verifiche ripetibili

Eseguire dal worktree RM-0009, non dal checkout principale. Prima di pytest,
usare un nuovo percorso temporaneo vuoto per `METNOS_USER_DATA` e
`METNOS_USER_CONFIG`: il bootstrap dei test non deve importare artefatti
dall'installazione vera. Non eseguire runner legacy o Birth per provare questi
preparatori.

Suite ammessa per questa consegna:

```text
/opt/metnos/.venv/bin/python -m pytest -q tests/internal/test_rm0009_inventory_check.py tests/internal/test_rm0009_plan_check.py tests/internal/test_rm0009_test_lab.py
```

Il verificatore `internal/tools/rm0009_inventory_check.py` riceve l'inventario,
`--repository` e `--expected-commit` espliciti. Controlla il tipo commit Git e
confronta i digest dichiarati con i blob e i byte del checkout. Rifiuta path
ambigui, symlink/hardlink, duplicati JSON e claim riservati; dichiara
`checks_file_mode=false`, `inventory_payload_validated=false` e
`authorizes_implementation=false`. `scope` e versione dello schema sorgente
sono payload specialistico non verificato, mai fusi con il suo rapporto.

Il controllo del documento ordinario conserva i due modelli `.N`, finche
G0.6 non normalizza il piano. Il grafo candidato espanso nel work manifest e
controllato separatamente con l'inventario dei 46 ID. Un esito positivo del
solo grafo non chiude il work manifest, i contratti o l'approvazione.

## Prossima consegna

Allineare la base al seguito MIT osservato e ripetere i censimenti; congelare
ciclo canonico/rollback, barriera snapshot, revoca dei writer e
contratto Birth/helper; completare file, simboli, schema fisico, test e rollback
di ogni incarico. Poi eseguire G0.7/G0.8 e presentare il payload esatto per
G0.9. FS puo iniziare dopo G0.6 e i prerequisiti delle sue righe; il prodotto
non-FS soltanto dopo G0.10. F5/FS restano inoltre veti di esercizio dove
prescritti, non dipendenze inventate del traguardo preliminare I1.1.
