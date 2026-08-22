# RM-0004 — verifica privata F13 (2026-08-22)

Stato: **gate F13 superato e distribuzione completata**.
Questo rapporto è interno: non va pubblicato, incluso in Tutor o usato come
testo destinato all'utente. Le guide pubbliche descrivono soltanto il
comportamento verificato e i suoi limiti operativi.

## Esito sintetico

F13 ha collegato LRE ai normali turni Metnos attraverso il solo executor di
sistema firmato `start_lre`. L'ingresso accetta esclusivamente profili presenti
nel registro chiuso del runtime; non consente al pianificatore di inventare
profili, executor, sorgenti remote o percorsi. La chiave di riconsegna del
canale viene resa opaca e circoscritta a proprietario, conversazione e canale
prima di entrare nello stato persistente.

L'unità utente `metnos-durable-worker.service` è installata, abilitata e
supervisionata. Al termine della certificazione il servizio è rimasto in
esecuzione, mentre l'interruttore per i nuovi invii è stato riportato a
`enabled=false`: l'archivio resta leggibile e il worker attende senza reclamare
nuovo lavoro. Questa separazione è intenzionale e costituisce il comportamento
predefinito sicuro.

## Progetto pilota

Il progetto pilota ha usato quattro immagini sintetiche prive di informazioni
personali. Il primo lavoro, `wrk_fa2110b3d29e4b2080deffae63637225`, è fallito
in modo esplicito perché un modello locale aveva restituito una lista JSON alla
radice dove il contratto richiedeva un oggetto. Il lavoro è rimasto nello
storico come fallito: non è stato presentato come completato e non è stato
riscritto. La correzione successiva è generale ma chiusa: l'adattatore ammette
una lista alla radice soltanto per i tre contratti che dichiarano un involucro
di lista nominato; non contiene rami legati a immagini, OCR o a un corpus
particolare.

Il secondo lavoro, `wrk_5d2d69d8a5e74d9b89a5c3334f194457`, revisione
`rev_41372a5c0e014fc79ffba959fcf99f99`, è stato interrotto mediante riavvio del
worker dopo otto commit e un tentativo in corso. Il nuovo processo ha ripreso
lo stesso lavoro e lo ha portato a `completed`.

Controllo indipendente finale sull'archivio distribuito:

| Proprietà | Evidenza osservata |
|---|---:|
| sorgenti attese e sigillate | 4 |
| unità | 23 |
| tentativi | 23 |
| risultati committati | 23 |
| digest di risultato distinti | 23 |
| artefatti validati | 3 |
| revisioni del lavoro riuscito | 1 |
| integrità SQLite | `ok`; nessuna violazione di chiave esterna |

La riconsegna HTTP con la stessa chiave di idempotenza è confluita sullo stesso
lavoro e sulla stessa revisione. Il turno conclusivo ha eseguito una sola
azione, `start_lre`; conteggi, digest e artefatti sono rimasti invariati. La
semantica generale resta *at least once* per il tentativo e un solo commit per
unità: non viene promessa l'esecuzione *exactly once* di effetti esterni.

## Verifiche finali

| Verifica | Esito |
|---|---|
| suite completa | 6.801 passati, 102 esclusi e 1.138 subtest passati in 515,41 s; ha individuato sei difetti circoscritti |
| correzione dei sei difetti e moduli interessati | 185 passati e 1.112 subtest passati in 6,91 s |
| manifest firmati e budget del testo per il pianificatore | 43 passati |
| compilazione Python | `compileall` senza errori |
| documentazione pubblica | 97 documenti HTML indicizzabili; lingue `it` ed `en` |
| Tutor | 4 schede, 3.545 unità; digest sorgente `sha256:ba18cf563b9b36b42975125f5b0f54f0dd61bd68e7858e331a0b51768df8e23c` |
| esportazione pubblica | 1.395 file; zero dati personali, segreti o file sensibili |

I sei difetti emersi dalla suite completa riguardavano tre pagine generate con
una vecchia versione degli asset, il repertorio i18n dello shim non rigenerato,
una descrizione di manifest oltre il budget e un oggetto HTTP minimale senza
attributo `headers`. Dopo le correzioni sono stati rieseguiti tutti e sei i test
originariamente falliti e le suite dei moduli coinvolti. Non è stata ripetuta
una seconda volta l'intera suite da oltre otto minuti, perché le modifiche erano
meccaniche o coperte da test puntuali e il proprietario aveva chiesto di
limitare il consumo di risorse. L'esito non viene quindi rappresentato come una
nuova esecuzione integrale verde.

## Documentazione e distribuzione

La guida pubblica bilingue è organizzata nel wiki sotto **Sistema** e comprende
panoramica, modelli, servizi, LRE, sicurezza, utenti e dispositivi. Ogni pagina
termina con una schermata reale della UI anonimizzata e richiami numerici. ADR,
roadmap, rapporti e documenti di analisi restano esclusi sia dal sito sia dal
catalogo Tutor.

- repository pubblico: commit `f6761db` su `brunialti/metnos`;
- distribuzione Cloudflare: `https://4d5050e3.mykleos.pages.dev`;
- percorso stabile verificato: `https://metnos.com/it/system/`;
- asset CSS verificato con HTTP 200 e versione `20260822-1`.

## Limiti residui dichiarati

1. Il primo profilo registrato usa immagini, ma è un adattatore esterno al
   nucleo: LRE non contiene logica di dominio e ammetterà altri profili soltanto
   mediante la stessa registrazione chiusa.
2. Nessun limite di esempio, compresi 98 o 980 elementi, costituisce un limite
   teorico del motore. Ogni revisione conserva invece budget finiti, visibili e
   verificabili; il lavoro procede per lotti e riduzioni.
3. Effetti esterni non idempotenti richiedono un protocollo specifico del
   relativo executor; LRE non può trasformarli universalmente in *exactly
   once*.
4. Costi, latenze e qualità di provider reali vanno misurati nell'uso
   operativo. Non resta alcun TODO indispensabile a isolamento, fencing,
   ripresa, cancellazione, completezza o pubblicazione F13.

Conclusione: F13 soddisfa il gate di uscita di RM-0004. LRE è installato,
documentato e distribuibile, ma i nuovi invii restano disattivati per
impostazione predefinita finché l'amministratore non li abilita esplicitamente.
