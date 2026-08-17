# Review statica indipendente V26.5.5

Data: 9 agosto 2026.

Verdetto: **STATIC PASS** sul delta facade offline. Nessuna autorizzazione
live.

Il conteggio cumulativo è esatto e precede `json.dumps`. Include envelope,
separatori, richiesta originale, chiavi, valori e ogni occorrenza condivisa;
gestisce escape JSON, larghezze UTF-8, null, booleani, interi e float finiti.
Surrogati, valori non finiti, subclass e cicli falliscono prima del dump. La
lunghezza codificata resta verificata contro il conteggio dopo il dump.

## Evidenza fresca

| Controllo | Esito |
|---|---:|
| self-test V26.5.5 | 14/14 |
| gruppi V26.5.4 | 24/24 |
| suite compatta | 106/106 |
| audit anti-contaminazione | 15/15 |
| semantica candidata | 6/6 + 16/16 |
| differenziale canonico | 1.004/1.004 |
| float finiti | 10.011/10.011 |

Il boundary esatto accetta 1.500.000 byte e rifiuta 1.500.001 prima del dump;
picco aggiuntivo osservato dopo l'input: 3.368 byte. Il probe chiavi con escape
accetta 1.499.791 e rifiuta 1.500.375 prima del dump, con picco 79.116 byte.
Quattordici casi indipendenti per surrogati, non-finiti, alias ciclici e
subclass sono falliti chiusi. Multi-azione, multi-dominio, riuso proiezione,
ambiguità tipizzata e coverage mista restano verdi. P50 one-shot fresco:
87,884 ms su sette campioni.

Worker e manifest V26.5.4 sono byte-identici: rispettivamente
`c57c8ece...0e22` e `88d7b009...2391`. Facade, self-test e risultato autore
V26.5.5 non sono stati modificati. Nessuna rete, modello, freeze, gate o run
live è stata usata o autorizzata.

Report leggibile a macchina: `metnos_v2655_independent_static_review.json`.
