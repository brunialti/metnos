# Referto RUN3_STYLE prompt_style_v0.1 — 13 agosto 2026

## Esito semplice

RUN3_STYLE e' completo, integro e valutato. I risultati sono utilizzabili
soltanto come descrizione: il controllo di stabilita' fra run non e' passato,
quindi non esiste un vincitore attribuibile allo stile del prompt.

- richieste: 158 casi per 4 bracci, **632/632** POST accettati, seriali e senza
  retry;
- canonico: A_SYSTEM_CURRENT **79/120**, S0_CURRENT **44/120**,
  S1_METNOS_SHORT **21/120**, S2_PROCEDURAL **18/120**;
- tipizzati: rispettivamente **1/4, 1/4, 0/4, 1/4**;
- legacy separato: rispettivamente **25/34, 27/34, 26/34, 27/34**;
- verdetto: `style_measure_valid: false` e
  `non_attributable_anchor_drift`.

Questi numeri non sono un ranking e non autorizzano un claim di superiorita'.

## Integrita' e audit

Il batch completo e' legato a journal, checkpoint, marker, authorization,
protocollo e sigillo. Il replay pre-gold ricostruisce 632/632 raw ed estrazioni
con zero differenze inattese. Gli artefatti principali restano:

- protocol freeze:
  `2dec09e3edce00b13e833c908df389a4425ff1c00db345ff26a014e41212dcad`;
- batch sigillato:
  `8f43a12831f2820cda3d2695e7435eb4e270bffb59b2a12f048eaca4afbe05dc`;
- seal:
  `e01d19ca4c8d4aa379d8cf76dedc8539430df50d44766400dcd043d6f6a10f61`;
- evaluation:
  `66ff0dc6204cd7d923a49ccfef47bc7004458f5e84eba7c31f239ae7186e9fde`.

La revisione indipendente post-evaluation conclude **20/20 PASS, 0 FAIL** sul
calcolo e sull'integrita', ma conferma il divieto di inferenza causale:

`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/reviews/functional_b/prompt_style_v0_1_run3_post_evaluation_audit.md`

SHA-256 del referto indipendente:
`d48baa33e0787cf0d152e360fc9034123a328d83fe54b0f812b3bfd9790f3e40`.

## Perche' la misura non e' attribuibile

Gli anchor richiedevano tolleranza zero rispetto a RUN2. A_SYSTEM_CURRENT ha
149/158 raw ed estrazioni esatti; S0_CURRENT ne ha 120/158. Le richieste sono
byte-identiche a RUN2 in 158/158 casi per ciascun anchor. Coincidono anche
query, modello, fingerprint, profilo di generazione e token di prompt. Il
runner ha inviato una richiesta alla volta, senza retry, e il replay degli
adapter e' deterministico.

La divergenza nasce quindi nei nuovi output del backend. Il server usato dalla
misura era condiviso e stateful: due slot, continuous batching, prompt cache e
cache reuse, speculative MTP e nessun reset fra RUN2 e RUN3_STYLE. Inoltre il
passaggio da due a quattro bracci ha cambiato l'ordine e la storia della cache;
il journal del server mostra checkpoint mescolati fra gli slot e altro traffico
nella stessa finestra. Temperatura zero e seed 42 non rendono identici i logit
Vulkan quando cambiano stato della cache, batching o percorso numerico.

La diagnosi e' pertanto doppia: il backend non e' risultato byte-deterministico
nelle condizioni ammesse e il protocollo pretendeva uguaglianza byte-per-byte
senza isolare tutto lo stato che poteva influenzarla. Non e' un errore del
request builder, del trasporto, dell'adapter o del verificatore.

## Difetti meccanici osservati nei prompt

I difetti seguenti sono fatti sugli output, non una classifica causale:

- S0_CURRENT: 29 `document_invalid` canonici;
- S1_METNOS_SHORT: 88 `document_invalid` e 5 `technical_invalid` canonici;
- S2_PROCEDURAL: 80 `document_invalid` e 3 `technical_invalid` canonici;
- nei nuovi stili, ogni documento invalido usa `from` sul primo passo e quindi
  si auto-riferisce;
- gli otto errori tecnici terminano al limite di 4000 token durante una
  enumerazione ripetitiva.

Tutti i JSON completi rispettano lo schema strutturato. E' il validatore IR a
respingere i riferimenti a se stessi, vincolo dinamico non espresso dal JSON
Schema. Non e' stato applicato repair.

## Revisione avversariale

1. I risultati descrittivi non possono essere trasformati a posteriori in un
   winner rilassando l'anchor da raw a semantico: esistono anche drift
   semantici.
2. Ripetere lo stesso protocollo condiviso non distingue stile, cache,
   batching e rumore numerico e costituirebbe stallo.
3. I fallimenti meccanici di S1/S2 sono sufficienti a bloccarne il porting, ma
   non stimano da soli un effetto causale dello stile.
4. Correggere prompt, schema o limiti prima di fissare il nuovo metodo
   cambierebbe un'altra variabile e non e' autorizzato da questo referto.
5. Nessuna query, ID, indice o hash del banco e' stato introdotto nella logica.

## Scelta metodologica aperta

Prima di un eventuale RUN4 Roberto deve scegliere fra due metodi diversi:

1. profilo deterministico isolato: server dedicato, un solo slot, niente
   continuous batching/cache/speculazione e gate di replica esatta; oppure
2. profilo realistico ripetuto: repliche uguali e bilanciate di ogni
   caso/braccio, con numero di repliche e criterio statistico fissati prima
   dell'apertura gold.

La prima opzione cambia il profilo backend rispetto a RUN2; la seconda richiede
nuove decisioni su repliche, intervallo di confidenza e soglia. Questo referto
non sceglie, non prepara RUN4 e non autorizza GPU, rete o nuovi POST. Il punto 5
resta incompleto.

