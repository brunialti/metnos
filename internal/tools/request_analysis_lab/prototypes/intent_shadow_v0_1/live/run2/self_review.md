# Self-review RUN 2

Data: 2026-08-13  
Perimetro: preparazione offline del RUN 2 candidate_v0_3, senza armamento.

## Esito interno

- snapshot A e pannello query-only riusati byte per byte dal RUN1, con fonti
  runtime/database/backend hash-bound;
- B usa `response_format=json_schema`, `strict=true`, una sola chiamata,
  critic OFF, raw byte-preserving, zero repair;
- pannello query-only 120 + 4 + 34 e manifest alternato 316;
- percorso live senza import/nome di gold, oracolo o attesi;
- runner single-use con marker, journal, checkpoint, raw, partial, batch e seal;
- full-run finto 316/316 e replay byte per byte 316/316;
- document-invalid model-facing continua; timeout, transport, envelope o
  eccezione adapter ferma e sigilla partial;
- evaluator separato, gold soltanto dopo completezza, sigillo e replay;
- metrica v0.3 simmetrica e pannello legacy separato;
- verifier/preflight disarmato senza GPU o rete.
- unica diff logica B: prompt projection bilanciata; schema, validator,
  compiler, registry, adapter semantics, modello, limiti ed evaluator invariati;
- B usa lo stesso percorso Unicode/BCP47 per ogni lingua, senza branch `it/en`,
  fallback implicito o allowlist; i tag malformati falliscono prima del send;
- scan anti-hardcoding esteso a candidato e adapter: nessun testo, frammento
  significativo, ID o hash del banco nella logica;
- integrità freeze riusata anche dal gate post-seal prima del gold; marker e
  checkpoint sono confrontati come documenti chiusi con tutti i binding.

## Revisione avversariale

1. L'autorizzazione generale di massimo tre run non deve diventare armamento
   automatico: `authorization_run2.json` è assente e deve essere l'ultimo file.
2. Un file di query può contenere identificativi opachi, ma nessuna risposta
   attesa. Il verifier chiude ricorsivamente i nomi gold-like.
3. Il compilatore B è deterministico solo se i freeze candidate_v0_3 e base
   candidate_v0_2 sono validi: il preflight esegue il verificatore overlay,
   che chiude anche tutti i binding critici del core base.
4. Il trasporto finto usa lo stesso runner ma percorsi temporanei; non crea
   marker o output nel namespace reale.
5. Il replay A ammette soltanto la differenza diagnostica booleana già
   approvata; B richiede uguaglianza esatta.
6. PASS offline non anticipa accuratezza semantica e non autorizza un POST.
7. I dizionari sono ammessi qui soltanto come JSON di laboratorio; un porting
   in produzione richiede oggetti tipizzati e immutabili.
8. La misura è adattiva e senza limite rigido di run, ma ogni nuovo run richiede
   una sola correzione universale, suite/preflight verdi e audit indipendente;
   nuova policy semantica o run identico in stallo impongono stop.

Questa self-review non è audit indipendente e non abilita GPU, rete o servizi.

Esito offline corrente prima dell'audit: suite RUN2 27/27, fake full-run e
replay 316/316, manifest 316, verifier/preflight disarmato con zero errori;
candidate v0.3 9/9 e round-trip 124/124; base v0.2 32/32; prerequisito BCP47
4/4. Questi numeri verificano il protocollo, non l'accuratezza live.
