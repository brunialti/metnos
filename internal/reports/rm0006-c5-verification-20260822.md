# RM-0006 — verifica C5 del 22 agosto 2026

## Esito

C5 e' completata: cinque sonde non distruttive su confini reali sono verdi,
una oltre il minimo richiesto. Il rapporto macchina e' in
`internal/reports/rm0006-c5-real-probes-20260822.json`.

| Sonda | Contratto osservato | Esito |
|---|---|---|
| stack Metnos installato | server pronto, catalogo caricato e contratti HTTP/sidecar allineati | pass |
| turno reale in sola lettura | `get_now` raggiunto dalla route di turno e risposta terminale presente | pass |
| catalogo modello locale | endpoint compatibile OpenAI disponibile con un modello dichiarato | pass |
| sito pubblico Cloudflare | redirect canonico, HTTPS, HTML e risposta 200 da Cloudflare | pass |
| origine GitHub pubblica | branch pubblico immutabile `refs/heads/main` visibile | pass |

Le sonde non hanno modificato servizi esterni, repository o dati utente. Il
solo turno applicativo era esplicitamente in sola lettura.

## Correzioni causali ricongelate

I cicli diagnostici hanno portato a tre correzioni sul punto comune, ciascuna
coperta da regressioni indipendenti dalla frase:

1. la polarita' di un riferimento e' risolta da un unico lessico sintattico
   traducibile (`syntax.negation` e `syntax.contrast`) e da
   `detection_lexicon.asserted_at`; i domini conservano soltanto i propri
   marcatori;
2. un'azione inversa senza destinazione puo' derivarla esclusivamente dal
   precedente envelope standard `_undo.reverse_pattern`, con executor esatto,
   schema chiuso e precedenza assoluta degli argomenti espliciti;
3. l'ingresso tecnico compatibile `start_lre` e' riconosciuto dal contratto
   registrato (un solo profilo ammesso e percorsi assoluti), passa dal cancello
   canonico e conserva gli identificatori del turno attraverso la ripresa.

Non sono state aggiunte eccezioni per richieste, lingue, provider o domini. La
matrice e' stata ricongelata dopo l'ultima correzione con digest
`3623f900e37e8a8fe966eb2030fc7303ac670a7202d208a007b59ea1fbf00062`.

## Difetti residui

Difetti aperti bloccanti: 0. Difetti aperti alti: 0. Le cinque sonde non hanno
mostrato differenze di contratto rispetto ai sostituti isolati usati dalla
matrice.
