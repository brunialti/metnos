# Specifiche implementative — 4 item della coda (6/7/2026)

Analisi condotta da Fable con 4 indagini parallele sul codice reale. Ogni spec è **eseguibile da un LLM inferiore** (file:riga, criteri di done, test, rischi).

| Spec | Dimensione del guadagno | Sforzo | Raccomandazione |
|---|---|---|---|
| [CP5 grammar-on-args](spec_cp5_grammar_on_args.md) | **Correttezza engine / debito guard** (cardine §8.3) | Medio (macchina GBNF già esiste, orfana) | **PRIMA SCELTA** — guadagno misurabile (guard-fire in meno) |
| [Telos dry-run §G](spec_telos_dry_run.md) | Auto-miglioramento / fossato | Basso (dry-run osserva) | **Alta info-value** — ma Telos è GIÀ ON in prod, e i dati mostrano possibili patologie |
| [i18n translation quality](spec_i18n_translation_quality.md) | Polish output IT (§7.8) | Medio-alto (sweep corpus) | Reale ma cosmetico; vincolo YAGNI 2-locali |
| [wheels device](spec_wheels_device.md) | Capability remota di nicchia | Medio (canale già mezzo-costruito) | **Basso valore/sforzo** — NON iniziare senza bisogno concreto |

## Scoperte che cambiano il quadro (emerse dall'analisi)

1. **Telos è ATTIVO in produzione dal 12/6** (drop-in systemd, ogni 72h). «A secco» = osservare senza scrivere, non «prima di accendere». Dati reali già disponibili: **116 proposte, new_valid=0 (mai un executor nuovo valido), 115 pending mai triageate, 1 sola decisione umana in 7 settimane**. L'esito onesto dell'analisi potrebbe essere «ridimensionare/spegnere», non «accendere».

2. **CP5 è a rischio-basso e alto-valore**: la macchina schema→GBNF esiste già (`tool_grammar.py`, 43 test, orfana dal planner legacy rimosso). grammar-on-verbs fu accantonata per ragioni che NON valgono sugli args (gli args si àncorano allo schema-manifest = ground-truth, non a `intent.actions` = output LLM). ~7-10 dei 14 guard esistono per correggere args che una GBNF eviterebbe alla sorgente.

3. **wheels**: il proxy PyPI server-side è già completo; manca il consumatore client + un gate (`assert_stdlib_only`) da rimuovere. Ma OCR≠wheel (binari tesseract/poppler) e la spreadsheet-family ha un import-gap da chiudere prima. openpyxl è il solo target pulito, di valore basso.

4. **i18n**: la metrica di qualità attuale (`translator_quality.score`) **premia** gli anglicismi (cosine più alto verso il source EN). Un solo presidio anti-anglicismo esiste (un'istruzione in `promoter_commentary.j2`, non un detector). Il prompt del job every_6h è una riga sola, il più debole.

## Ordine raccomandato se si procede
1. **CP5** (guadagno solido e misurabile sul cardine dell'engine).
2. **Telos dry-run** (basso costo, de-risking; ma prima decidere con Roberto le 7 domande aperte in §3 della spec — soprattutto: analizzare il cumulato o spegnere+dry).
3. **i18n** (quando si tocca la lingua; sweep guidato dal detector).
4. **wheels** (solo su bisogno utente concreto e ricorrente, es. OCR di documenti locali — ma quello è il ramo binari, il più complesso).
