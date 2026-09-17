# LRE: prova isolata di parallelismo VLM e pausa/ripresa — 17 settembre 2026

Richiesta di Roberto: misurare il possibile vantaggio del parallelismo senza
alterare il job; successiva autorizzazione esplicita alla pausa e alla ripresa.
Nessun rilascio, cambio di configurazione, nuovo job o cancellazione.

## Esito della misura

Sul campione, quattro richieste simultanee aumentano il numero di immagini
elaborate al minuto del **44,5%**, riducendo il tempo del **30,8%** rispetto
alla media dei due riferimenti seriali. Due richieste non migliorano il
rendimento. Non è un'accelerazione di quattro volte e non è una misura
dell'intera indicizzazione.

| Ordine | Richieste simultanee | Immagini | Tempo totale | Immagini/minuto | Risposte valide | Slot occupati osservati |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 8 | 42,25 s | 11,36 | 8/8 | 1 |
| 2 | 2 | 8 | 43,80 s | 10,96 | 8/8 | 2 |
| 3 | 4 | 8 | 29,24 s | 16,41 | 8/8 | 4 |
| 4 | 1 | 8 | 42,29 s | 11,35 | 8/8 | 1 |

La ripetizione seriale differisce dello 0,097%. Tutte le 32 risposte hanno
terminato normalmente e rispettano lo schema delle descrizioni. Con quattro
richieste la latenza media della singola richiesta sale a 14,36 s: il vantaggio
è nel rendimento complessivo, non nella risposta individuale.

## Metodo e confini

- Otto immagini distinte, due da ciascuno di quattro batch già confermati,
  distribuiti fra gli ultimi venti batch di analisi; copie private sigillate
  del job, verificate rispetto alle impronte delle ricevute. Nessuna lettura
  ricorsiva dell'archivio e nessun file originale modificato.
- Stesso processo VLM locale esistente, quattro slot configurati, modello
  `qwen3vl-2b`, immagini ridimensionate come nel client produttivo a lato
  massimo 1024, JPEG qualità 85, schema e prompt italiani correnti.
- Impronta del prompt verificata rispetto al contratto congelato del job;
  massimo 512 token, temperatura 0,2, stessi parametri di campionamento.
  Per rendere confrontabili le ripetizioni: seme 42 e `cache_prompt=false`
  soltanto nelle richieste diagnostiche, non nella configurazione del prodotto.
- Richieste inviate direttamente al modello locale durante la pausa,
  fuori dal lavoro durevole: nessuna ricevuta di prova diventa un risultato
  del job, nessuna modifica a indice o contabilizzazione LRE.
- Prima, fra e dopo le prove: job in `paused`, zero tentativi attivi,
  identica revisione e identici riferimenti a tutti i risultati confermati.
  Fine prova con tutti gli slot VLM liberi. Nessun modello aggiuntivo avviato.
- Limiti: otto immagini, una sola prova per livello parallelo; è una misura
  esplorativa, non una certificazione del comportamento sotto carico prolungato.
  Non comprende lettura, volti, vettori, checkpoint, ammissione delle risorse
  o quattro executor completi concorrenti.

La validità attestata è strutturale, non una valutazione indipendente della
qualità delle descrizioni. I due riferimenti seriali producono gli stessi
otto risultati; due richieste ne conservano sei identici e quattro nessuno.
Non dichiarare equivalenza semantica sulla sola base dello schema. Con quattro
richieste i token medi in uscita sono 208,1 contro 193,5 in seriale: il vantaggio
misurato non proviene da risposte più brevi.

## Pausa e ripresa del lavoro reale

Job: `wrk_4121cc6258c447759e94ccc6f8090e5c`.
Revisione invariata: `rev_d8d3982283a140bf993f2517731ad00d`.
Piano invariato: `a96bc10667200eb04d1b1f6f0637f206df147ff3725cc19e328ed9bf6c484421`.

- Pausa richiesta alle 09:27:19 Europe/Rome tramite l'API ordinaria,
  con versione attesa e chiave di idempotenza specifiche.
- Il batch già in corso termina: analisi da 180 a 181 batch confermati.
  Alle 09:28:09 il job è `paused`, versione 6, zero tentativi attivi.
- Tutti i 1.148 risultati già confermati sono conservati; dopo lo svuotamento
  sono 1.149. La loro impronta ordinata resta identica durante il test:
  `fd30bc1872de269beb66f2cca6c391c5f1f9daf5dac94d7db84f04ce2cf16ff7`.
- Ripresa richiesta alle 09:33:42; alle 09:33:44 stesso job `running`,
  versione 8, un tentativo attivo, tutti i risultati conservati.
- Lavoratore PID 1175505 invariato. La prova riguarda la pausa e ripresa
  ordinaria del job, **non** un riavvio del servizio, arresto forzato o reboot.
- Alle 09:36:58 il job ha confermato il batch di analisi **182** ed è ancora
  `running`, con un nuovo tentativo attivo. Sono conservati tutti i 1.149
  risultati della pausa e il totale confermato sale a 1.150. La verifica
  riguarda quindi un risultato nuovo salvato, non soltanto lo stato `running`.

Nota sulla sonda: il primo controllo precedente all'invio di `resume` confrontava
tuple lette da SQLite con liste rilette dal JSON e rifiutava erroneamente
l'uguaglianza, pur con impronte identiche. Corretto soltanto quel confronto
nello strumento temporaneo; nessun comando era stato inviato in quel passaggio,
nessun dato LRE è stato riparato o modificato direttamente. La successiva
ripresa ordinaria è riuscita.

## Decisione operativa

Resta il parallelismo produttivo originale: nessun aumento applicato.
Il risultato giustifica una prova dell'intera catena con quattro unità
indipendenti, compresi limiti comuni, memoria, contratti, salvataggi e qualità.
Non basta alzare un solo limite: i vincoli del lavoratore e quelli dello
scheduler restano entrambi autorevoli.

### Vincolo aggiuntivo verificato sulla domanda di generalizzazione

LRE dispone già di corsie generiche (`service._run_parallel_cycle`),
ammissione di risorse dichiarate, prenotazioni e contabilizzazione per
tentativo. Il lavoratore usa però limiti di risorsa predefiniti pari a uno
(`runtime_bindings._resource_limits`); lo scheduler impone propri limiti,
fra cui VLM=1 e CPU=2. Le corrispondenti variabili non risultavano impostate
nell'ambiente del lavoratore osservato.

Inoltre `ExecutorScheduler.can_parallelize` richiede un'identità verificabile
per le scritture. `concurrency_identity_for` per una politica `path` cerca
`dest`, `path`, `output_path` o un solo elemento di `paths`. L'indicizzatore
passa invece `base_path`, generazione ed elementi: non ottiene quell'identità
e `_context_executor_slot` lo serializza. Questo vincolo esiste anche se
si aumentano le disponibilità CPU/VLM. È una protezione da scritture
sovrapposte, non va rimossa prendendo arbitrariamente il solo ID del batch.

Un'evoluzione generale deve dichiarare e verificare i bersagli di scrittura
indipendenti, condividere limiti coerenti con lo scheduler e conservare
contabilità, dipendenze, priorità e pressione sulla memoria. Niente ramo
speciale del nucleo per le foto. Ogni consumer deve soddisfare il contratto
generale prima di ottenere concorrenza sulle proprie scritture.

La prova dimostra conservazione nella pausa/ripresa della versione corrente.
Non dimostra ancora la compatibilità attraverso un aggiornamento di codice o
manifest: identità e contratti congelati del job devono restare validi, oppure
serve un percorso esplicito e verificato per riutilizzare i risultati esistenti.
Non riscrivere i contratti salvati né ricreare il job dichiarando implicitamente
che riprenderà da dove era arrivato. Nessuna di queste modifiche è stata
applicata durante la prova.

La precedente osservazione di venti batch produttivi e 640 chiamate attribuiva
al VLM l'86,6% del tempo. Se, e solo se, il vantaggio misurato si trasferisse
integralmente senza nuova contesa, il miglioramento teorico complessivo sarebbe
circa 1,36 volte. Non è una nuova previsione del job attuale: la sua
configurazione è rimasta invariata.

## Evidenze private

- Diagnosi iniziale: `run-z2vg96gr`, venti batch e 640 chiamate.
- Preparazione privata: `run-r3c4kfyb`, otto ingressi e relative impronte.
- Pausa controllata: `run-cf06jnob`.
- Misure: `run-nt9kl0k1`, 09:30:01–09:32:38 Europe/Rome;
  `measurements.json` contiene i tempi per richiesta senza testi delle descrizioni.
- Primo confronto della sonda rifiutato prima dell'effetto: `run-bbr8a9vt`.
- Ripresa riuscita: `run-qusq36zw`.
- Nuovo batch salvato dopo la ripresa: `run-rjnmz5sc`.
- Strumenti temporanei: `/tmp/metnos-lre-parallel-probe-20260917.PSKDfS`.
  Tutti i dati del campione restano nel deposito amministrativo locale privato;
  nessun invio esterno o pubblicazione di immagini o descrizioni.
