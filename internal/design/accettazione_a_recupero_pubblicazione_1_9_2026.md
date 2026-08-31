# RM-0008 · accettazione A del recupero pubblicazione incompleta

Data: 1 settembre 2026  
Agente revisore: A  
Commit accettato: `52f379cb3ddc0ed898bb04c69e2cef0fd1fb6758`  
Stato: **ACCETTATA**

## Verdetto

Accetto il commit esatto `52f379cb` per la primitiva separata di recupero di
una prima pubblicazione incompleta.

Evidenza sul clone immutabile
`/tmp/metnos-rm0008-b-review-52f379cb`:

```text
prova_recupero_pubblicazione.py                 28/28 verdi
prova_robustezza_recupero_pubblicazione_a.py     5/5 verdi
git diff --check                                 verde
```

Le proprietà convergenti sono:

1. bersaglio derivato dall'inventario autoriale e dalla radice autorizzata;
2. osservazione senza scritture;
3. controllo per descrittori e rifiuto atomico della collisione;
4. ricevuta durevole a stati `prepared` e `committed`, legata all'identità
   esatta del contenitore;
5. ripresa monotona dopo ogni rimozione intermedia;
6. idempotenza distinta dal caso «mai esistito»;
7. sincronizzazione della radice anche nel ritorno idempotente.

L'accettazione riguarda codice e prove, non autorizza l'applicazione al negozio
reale. La primitiva resta separata dall'esecuzione automatica F4.

