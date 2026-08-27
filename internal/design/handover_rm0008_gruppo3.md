# RM-0008 — prompt di subentro per il gruppo 3

Da incollare tale e quale a un agente che subentra. Aggiornare solo se cambia
lo stato descritto nella prima sezione.

```
Continua RM-0008 nel worktree /tmp/metnos-rm0008-a-only (ramo main).
NON toccare /opt/metnos, non creare rami.

STATO: il gruppo 2 e' chiuso. L'installatore prepara un insieme di autorita'
completo ma INERTE: il runtime Birth non e' attivo e nessun chiamante e'
migrato. Il ciclo pubblico e' verde su tutti e nove i lavori. Tutto e'
committato; non esiste lavoro in file temporanei.

LEGGI PRIMA, IN QUEST'ORDINE:
0. internal/reports/rm0008-regole-di-lavoro-fra-gruppi.md
   — cinque regole valide per TUTTI i gruppi dal 3 al 6, misurate sul gruppo 2.
     Si ereditano cosi' come sono. Contengono anche la regola che i piani di
     dettaglio di un gruppo si scrivono QUANDO quel gruppo inizia: i gruppi 4-6
     non hanno un piano e non devono averlo in anticipo.
1. internal/reports/rm0008-gruppo3-piano-ottimizzato.md
   — cosa fare ora, le quattro ottimizzazioni, l'ordine che evita di rifare i
     vettori golden, e §7 la PROCEDURA DI RIFOTOGRAFIA (otto passi, trappole
     gia' pagate: eseguila cosi', non improvvisarla).
2. internal/reports/rm0008-gruppo2-analisi-implementazione.md §13
   — criterio di uscita compilato del gruppo 2, con i tre requisiti dichiarati
     NON provati. Non spacciarli per provati e non toccarli senza mandato.
3. lo stesso rapporto §17.70-§17.81
   — diario: cosa e' stato costruito, i difetti trovati, e soprattutto le
     deduzioni sbagliate corrette dalla misura. Leggile: impediscono di rifare
     lo stesso giro.

COMPITO: il gruppo 3, seguendo il piano ottimizzato. Rende attivo cio' che il
gruppo 2 ha predisposto. L'ordine del §4 del piano non e' negoziabile: portare
gli `enforcement_state` a `productive` va fatto PER ULTIMO, perche' cambia
identificativo ed epoca e obbliga a rifare tutti i vettori golden.

MODO DI LAVORARE:
- commit piccoli e tematici solo su main, con il marcatore
  `RM-0008-Status: candidate-not-certified` in coda al messaggio;
- pubblicazione incrementale:
  `METNOS_VENV=/opt/metnos/.venv bash scripts/publish-public.sh --incremental -m "<inglese>"`;
- dopo OGNI pubblicazione verifica il workflow pubblico e non proseguire se e'
  rosso;
- riporta una sola riga di avanzamento per volta, in parole semplici.

QUATTRO VINCOLI CHE COSTANO CARI SE IGNORATI:
1. Ogni modifica alla base congelata costa un ciclo intero di rifotografia: nel
   gruppo 2 ne sono serviti dieci. ACCUMULA le modifiche alla base e congela
   UNA VOLTA SOLA alla fine dell'incremento.
2. La cella R1 del grafo produttivo respinge ogni nuova "porta" verso la
   capacita' che scrive su disco. Ha colto due errori veri di collocazione:
   estendila dichiarando esattamente chi puo' passare, non aggirarla.
3. Non riesercitare cio' che il gruppo 2 ha gia' certificato (primitiva a
   handle, giornale, documenti canonici, disposizione). Prova il contratto del
   TUO gruppo.
4. Non costruire strumenti diagnostici per curiosita': nel gruppo 2 tre su tre
   hanno risposto per conto proprio prima di dire la verita'. Costruiscine uno
   solo se la sua risposta cambia una decisione, e fagli dichiarare come l'ha
   ottenuta.

REGOLA DI ONESTA': "solo i test necessari" non significa "solo i test che
passano". Cio' che non provi va scritto come non provato, con il motivo, nel
criterio di uscita del gruppo 3.

APERTO E DA NON RISOLVERE A OCCHI CHIUSI: su Windows il predispositore arriva
fino alla rinomina che pubblica il primo finale e riceve accesso negato. Il
privilegio NON c'entra (misurato). L'ipotesi "manca DELETE nella maschera" e'
in tensione con due celle verdi: non toccare la maschera prima di una misura.

GIA' INIZIATO DEL GRUPPO 3, tutto committato e con la suite locale verde:
- `runtime/executor_birth_prepared_set.py` rilegge l'insieme predisposto sotto
  la propria barriera e rifiuta se marcatore, insieme, archivi e materiale non
  concordano; riceve una sessione gia' aperta e non ne apre nessuna;
- `runtime/executor_birth_prepared_root.py` e' la porta del runtime sulla
  radice predisposta, in **sola lettura**; la cella R1 e' gia' estesa per
  ammetterla (due sedi nominate, e la porta vale solo finche' non tocca una
  mutazione) con due mutanti che lo dimostrano;
- prove: `tests/portable/rm0008_2b/test_group3_prepared_set.py`.

**NON ANCORA PUBBLICATO**: la modifica alla base e' in coda per congelare una
volta sola a fine incremento. Prima di pubblicare serve la rifotografia (§7 del
piano).

PROSSIMO PASSO: migrazione del bootstrap, con la mappa gia' scritta nel §6.bis
del piano. Attenzione ai due fatti che oggi la configurazione sceglie e che non
hanno ancora una fonte chiusa (`policy_version`, `receipt_ttl_seconds`): vanno
assegnati o dichiarati, non inventati.
```

## Perche' i gruppi 4-6 non hanno un piano

Deciso con Roberto il 27/8/2026. La forma dei gruppi 4-6 dipende da cosa il
gruppo 3 consegna davvero; un piano scritto in anticipo va riscritto, e
riscriverlo costa piu' che non averlo. Cio' che invece non invecchia — le
regole di lavoro fra gruppi — e' stato sollevato in un documento separato che
quei gruppi ereditano.

Quindi: quando il gruppo 3 chiude, il gruppo 4 comincia scrivendo il **proprio**
piano ottimizzato sullo stesso modello, non prendendone uno gia' pronto.

## Avvertenze per chi consegna

- L'agente parte da freddo: il primo giro serve a leggere, non a produrre.
- Il prompt fa continuare il piano. Se invece si vuole **rimettere in
  discussione** una scelta, va detto esplicitamente cosa riaprire, altrimenti
  l'agente la trattera' come acquisita.
