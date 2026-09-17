# LRE: parallelismo generale e compatibilità F5 — 17 settembre 2026

## Stato effettivo

Implementazione e prove isolate concluse; **non attivata in produzione**.
Nessuna pausa, cancellazione, riscrittura di piano, configurazione produttiva,
chiave, migrazione F5, rilascio applicativo o utilizzo della GPU in questa fase.

- Candidato LRE: `ab763e66`, ramo `codex/lre-backend-release`.
- Integrazione con F5 `985fbb38`: `211e9c4f`, ramo
  `codex/lre-general-parallel-f5`, directory stabile
  `/opt/metnos/.claude/worktrees/lre-general-parallel-f5`.
- Fusione della storia completa LRE in `f609608f`: soltanto due rapporti
  riallineati; codice, prove, installer e guide identici a `211e9c4f`.
- Baseline dei sorgenti prima della modifica: `f1ef7e5a`.
- Il TODO sulle priorità GPU preesistente rimane separato e non è incluso
  in questi commit.

## Soluzione riutilizzabile

Il pacchetto della capacità dichiara, tramite un risolutore registrato,
l'insieme verificato delle destinazioni mutabili di ciascun tentativo.
Il nucleo applica soltanto una regola generale: insiemi disgiunti possono
procedere, sovrapposizioni o prove assenti impongono esclusione seriale.
La prenotazione è atomica, limitata e liberata anche in caso di errore.
Una scrittura su directory esclude anche quelle sui suoi discendenti;
la prova include nomi lessicograficamente intercalati, come `/a-other`
fra `/a` e `/a/child`.

Non è un nuovo pianificatore: restano il gruppo centrale, le risorse,
le priorità, le dipendenze, i limiti del piano, le concessioni temporanee
e i numeri di tentativo esistenti. La protezione è per lo stesso executor
e scheduler di processo, non un blocco globale o distribuito del filesystem.
I nuovi fatti non diventano autorità firmata e non viaggiano verso dispositivi.

La funzione immagini è il primo consumatore, fuori dal nucleo generale:
verifica la ricevuta del gruppo e i checkpoint effettivi. Parti immutabili
e riuso della generazione precedente conservano le proprie protezioni.
Scoperta, riduzione e pubblicazione restano seriali.

Configurazione prevista, **non applicata**, nel `runtime.toml` privato:

```toml
[execution_resources]
cpu = 4
vlm = 4
```

Si legge creando un nuovo scheduler, non ridimensionando semafori attivi.
Variabili d'ambiente esistenti prioritarie, valori invalidi ridotti a uno,
valori predefiniti invariati. Sono quattro posti logici, non quattro core.
Le capacità `METNOS_DURABLE_RESOURCE_*` descrivono il singolo tentativo;
non vanno aumentate a quattro per avere quattro lavoratori.

Il beneficio CPU misurato sul campione resta circa +44,5% di rendimento,
non parità con la GPU. Non è ancora una misura di quattro executor completi
con modelli reali e non certifica qualità semantica identica delle descrizioni.

## Prove eseguite

| Insieme | Esito | Confine |
|---|---:|---|
| LRE, scheduler, isolamento, dominio immagini, motore e configurazione nell'albero combinato | 913 superate, 4 saltate | Archivi sintetici; comprende le prove di arresto reale già esistenti |
| Ripresa fra alberi, avanzamento parallelo, F5 inerte e guardia durevole | 43 superate | Prima fase su `f1ef7e5a`, seconda su `211e9c4f`; alcuni casi si sovrappongono alla riga precedente |
| API e console HTTP nell'albero combinato | 15 superate, 2 saltate | Server HTTP temporanei, nessuna chiamata a produzione |
| Lanciatore F5, punto d'ingresso, emittente, migrazione e inattività precedente alla migrazione | 137 superate | Albero F5 `985fbb38`; verifica delle correzioni `f5fadfcb` |
| Ricetta di rilascio nella baseline | 2 superate | Controllo positivo e rifiuto della costante obsoleta |
| Stessa ricetta nel candidato combinato | **1 superata, 1 fallita** | Blocco aperto, non escluso né aggirato |

La prova nuova `test_release_resume.py` usa due interpreti separati e
`RuntimeFactory`, concessioni, archivio SQLite, autorità delle sorgenti e
supervisione reali. La capacità di prova scrive sei file indipendenti:

1. Il processo precedente conferma due batch; ne rimangono quattro.
2. Un caso termina con SIGTERM fra tentativi; l'altro riceve SIGKILL durante
   il terzo tentativo, prima del suo effetto. La politica autorizza tre tentativi.
3. Il processo aggiornato riapre gli stessi archivi. La guardia F5 reale legge
   l'assenza del contrassegno nella directory di prova, senza simulare il verdetto.
4. I quattro batch restanti entrano realmente insieme; l'interrotto viene
   recuperato secondo concessione e politica, senza aggiornamenti SQL manuali.
5. Stesso piano byte per byte e stessa revisione; tutti i riferimenti precedenti
   sono conservati; sei risultati e sei file corretti; nessun batch confermato
   ripetuto, nessun tentativo ancora attivo.

Invocazione riproducibile, indicando un checkout della baseline:

```sh
METNOS_LRE_TEST_PREVIOUS_ROOT=/percorso/checkout-precedente \
  /percorso/venv/bin/python -m pytest -q \
  tests/runtime/durable_workloads/test_release_resume.py
```

Senza la variabile, la prova verifica il riavvio della stessa versione.
Questa evidenza riguarda **sorgenti e archivi sintetici**: non sostituisce
la verifica dei contratti del job reale, della distribuzione firmata,
dell'arresto dei servizi o del riavvio della macchina.

Durante la preparazione del nuovo banco sono stati corretti errori del banco
stesso: nome del campo unità, isolamento dei percorsi, metodo del servizio,
conto dell'inventario già sigillato, riserva per il traffico interattivo e
numero di tentativi. Non è stato allargato alcun limite del job reale per
far riuscire la prova.

## Blocco da chiudere con F5

`tests/runtime/infra/test_rm0008_release_cycle.py::test_early_recipe_check_uses_real_canonical_and_independent_codecs[False]`
fallisce con `PreflightError: service source recipe`; il caso negativo passa.
La baseline passa entrambi. Il confronto mostra che `830e36ca` aggiunge
`legacy-install-operator-authority` a `SERVICE_SOURCE_V1`, mentre la costante
indipendente approvata in `executor_birth_admin_preflight.py` resta invariata.
La divergenza va riesaminata da F5 come modifica della ricetta autorevole,
non risolta copiando automaticamente l'impronta candidata o rimuovendo il test.
Le tre correzioni del lanciatore sono confermate ma non chiudono questo blocco.

Prima del rilascio: chiudere il controllo della ricetta, verificare che il ramo
effettivamente preparato includa F5 e il candidato LRE, controllare i contratti
del job corrente, poi pianificare la pausa ordinaria con svuotamento e la
ripresa della stessa revisione. Dopo il rilascio, misurare quattro tentativi
completi reali e ricontrollare conservazione dei risultati, memoria e latenza.

## Produzione e documentazione

Controllo in sola lettura `run-t27pyhrw`, 11:44 Europe/Rome:
`wrk_4121cc6258c447759e94ccc6f8090e5c` ancora `running`, 215/967 batch
di analisi confermati, uno attivo, 1.183 risultati complessivi. Processo
1175505, revisione `rev_d8d3982283a140bf993f2517731ad00d` e impronta del piano
`a96bc10667200eb04d1b1f6f0637f206df147ff3725cc19e328ed9bf6c484421` invariati.

Pubblicate soltanto le guide statiche IT/EN: distribuzione Pages
`32e2aadc.mykleos.pages.dev`; archivio Tutor locale non modificato. Il primo
tentativo si è fermato sul controllo del riferimento UI prima dell'invio;
rigenerazione e nuovo confronto non hanno lasciato differenze nel riferimento,
il secondo tentativo ha superato tutti i controlli. Nessun rapporto interno,
immagine privata o dato del lavoro è stato incluso.

Risultati XML di questa esecuzione nel banco temporaneo
`/tmp/metnos-lre-general-parallel-20260917.8vkQ8j`:
`combined-core.xml`, `cross-release-resume.xml`, `combined-http.xml`,
`f5-launcher.xml`, `combined-release-recipe.xml`.
