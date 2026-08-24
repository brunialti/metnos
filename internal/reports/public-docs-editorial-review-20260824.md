# DOC-IT-001 — revisione finale della documentazione pubblica

## Perimetro verificato

La revisione del 24 agosto 2026 ha coperto le pagine tecniche italiane e la
rispettiva superficie inglese:

- ingresso e uso: landing page, guida web, interfaccia, domini, codice e
  roadmap;
- Sistema: indice, modelli, servizi, LRE, safety, utenti e dispositivi;
- Architettura: tutte le 31 pagine sotto `docs/it/architecture/` e i pari
  documenti inglesi;
- visita guidata, glossario e cataloghi generati.

I dialoghi e i saggi con voce autoriale sono esclusi deliberatamente dalla
revisione tecnica: `Metnos_Dialogo_v1`, `Metnos_Dialogo_Executor_v1`,
`Metnos_Prospettive_Estese_v1` e `Metnos_Prospettive_Giudizio_v1`, insieme ai
rispettivi documenti inglesi. Non sono debito tecnico e non sono stati riscritti.

## Correzioni dell'ultimo passaggio

- `policy`: confronto didattico fra lettura e modifica, con separazione fra
  capability dichiarativa, autorizzazione effettiva, sandbox e annullamento;
  chiarito che la pagina Modifiche non governa ogni scrittura di file;
- guida web: il flusso di login amministrativo ora descrive redirect e ritorno
  automatico alla destinazione sicura;
- utenti e pairing: `utente` e `identita'` sostituiscono `persona` quando il
  testo indica l'account Metnos;
- QuickTour: il conteggio Settings deriva dal registro corrente ed e' stato
  aggiornato da 14 a 15 in ogni occorrenza IT/EN;
- roadmap: le classi visuali di RM-002 e RM-005 sono state riallineate ai loro
  stati testuali, mantenendo id, stato, definizione e implementazione nella
  stessa riga compatta;
- glossario: corretta una concordanza senza alterare la voce autoriale.

Le dichiarazioni comparative della QuickTour sono state ricontrollate il
24/8/2026 contro le fonti ufficiali correnti di OpenClaw e Hermes Agent. Non e'
stata introdotta una graduatoria o una promessa commerciale.

## Parita' e controlli

- 43 coppie IT/EN con lo stesso percorso hanno identica struttura di heading,
  tabelle e righe;
- 99 file HTML analizzati, zero errori di parsing;
- zero collegamenti o asset locali mancanti;
- 42 test su inventario pubblicato, Reference dei domini, catalogo executor,
  Reference dell'interfaccia, QuickTour e contratto documentale: tutti verdi;
- cataloghi executor, domini e interfaccia rigenerati dalle fonti canoniche;
- nessuna correzione a doppia fonte per le pagine generate.

Il perimetro tecnico di DOC-IT-001 e' chiuso. Nuove revisioni delle opere
autoriali richiedono una richiesta editoriale distinta, non restano come TODO di
sviluppo.

## Consegna finale

- Cloudflare Pages: deployment immutabile
  `https://7f3c642e.mykleos.pages.dev`, promosso sul dominio `metnos.com`;
- verifica sul dominio canonico: 28 pagine modificate identiche byte per byte;
  nelle altre 5 l'unica trasformazione e' la protezione automatica degli
  indirizzi email applicata da Cloudflare;
- GitHub pubblico: commit incrementale
  `72aa3c7da1693d7e6b32020e5d0d5f1893bdc0d5`, verificato come testa di
  `brunialti/metnos:main` dopo il cancello con zero PII e zero segreti.
