# Self-review RUN3 style-only

- A viene confrontato byte per byte con ogni richiesta A del RUN2; S0 con ogni
  richiesta B. Dopo la misura entrambi richiedono uguaglianza esatta di raw,
  estrazione completa e metriche semantiche RUN2 per tutti i 158 casi.
- Una matrice fresh-process gold-free su `PYTHONHASHSEED=0..255` seleziona il
  minimo valore con A158+B158 estrazioni complete esatte; due conferme fresh
  richiedono anche A158+B158 request esatte. Il preflight controlla env e
  fingerprint dell'interprete e fallisce chiuso sul seed errato.
- Una sonda preliminare locale usata soltanto per osservare la non-determinismo
  è stata scartata: non ha fornito range, selezione o branch al protocollo. La
  sola autorità materializzata è la matrice completa predefinita 0..255.
- S1 e S2 condividono letteralmente lo stesso inventario di sei regole.
- Template radice, sezione registry, schema, query e parametri sono uguali.
- La proposta coverage archiviata non compare nel percorso live.
- Schedule latino: ABCD 40, BCDA 40, CDAB 39, DABC 39; ogni braccio ha 158
  richieste e occupa ogni posizione 39 o 40 volte.
- Runner single-use, marker prima del socket, zero retry, partial su trasporto o
  envelope, continuazione soltanto su JSON/IR model-facing invalido.
- Fake full-run e replay 632/632 usano percorsi temporanei e non toccano il namespace.
- Il gold non compare nell'import graph live; evaluator solo post-seal.
- La metrica semantica è riusata dal RUN2, non riscritta.
- Nessun punteggio combinato; sicurezza, typed e legacy restano separati.
- L'auth richiederà due referti distinti PASS e dovrà essere l'ultimo file.

Questa revisione interna non sostituisce i due audit indipendenti e non abilita
GPU, rete o servizi.
