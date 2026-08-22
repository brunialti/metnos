# RM-0006 — verifica C1 del 22 agosto 2026

## Esito

C1 e' completata. La matrice contiene 24 flussi logici e 48 casi obbligatori:
24 formulazioni italiane e 24 inglesi. La sua impronta congelata, nella
revisione v2 usata dai cicli qualificanti, e':

```text
23a7455cb09a1498cbacf47b2047d344bc6e6a2980ec4a6465745ac034ebe5ac
```

La v1 e' stata riaperta durante la preparazione di C2, prima di qualsiasi
ciclo qualificante: i percorsi generici «cartella di prova» non rendevano la
fixture eseguibile, la sonda di raggruppamento chiedeva somme non richieste
dalla frase e due piani equivalenti gia' ammessi dall'architettura non erano
elencati. La v2 rende espliciti i path isolati, verifica i membri dei gruppi e
ammette le alternative non distruttive. Da quel congelamento in poi i due
cicli C2 non hanno modificato richieste, sonde, piani o limiti.

## Metodo di selezione

La selezione riusa categorie, confini e difetti gia' documentati nella suite
E2E, negli ADR e nelle verifiche RM-0004, ma non copia richieste private. Le
formulazioni sono sintetiche e sono state controllate per naturalezza,
chiarezza dell'oggetto e assenza di percorsi personali, nomi, email o account.

La sorgente autorevole e' `fixtures/golden_flows.json`. Ogni flusso contiene
un solo oracolo condiviso e due richieste, `it` ed `en`. Il generatore produce
`golden_cases.jsonl`, validando ogni caso contro `CaseSpec`. Ne consegue per
costruzione che le due lingue differiscono soltanto per `case_id`, `locale` e
`request`; effetti, divieti, collocazione, consenso, postcondizioni e limiti
restano identici.

## Copertura congelata

| Famiglia | Flussi |
|---|---:|
| spiegazione e Tutor | 3 |
| lettura e selezione | 4 |
| composizione e passaggio dati | 4 |
| mutazione, consenso e annullamento | 4 |
| dispositivo e proprietario | 3 |
| dialogo e ripresa | 2 |
| lavoro durevole | 2 |
| fallimento e risultato parziale | 2 |
| **Totale** | **24** |

Altri vincoli misurati: 10 flussi richiedono approvazione; 2 richiedono un
dispositivo posseduto; 22 terminano con risultato completo, 1 con fallimento
atteso e 1 con risultato parziale atteso. Tutti i casi hanno almeno una sonda
di postcondizione e almeno un effetto o un'assenza di azione da provare.

## Prove

```text
python3 -m certification.golden_matrix --check
logical_flows=24, cases=48, it=24, en=24

python3 -m pytest scenarios/test_certification_c0.py \
  scenarios/test_certification_c1.py -q
8 passed in 0.21s
```

## Limite

C1 congela gli oracoli prima dei cicli qualificanti, ma non afferma che Metnos
li superi. L'esecuzione HTTP, le fixture isolate e le prove rapide appartengono
a C2; dopo il congelamento v2 eventuali difetti del prodotto non autorizzano a
modificare questa matrice per adattarla all'uscita osservata.
