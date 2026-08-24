# EXE-DESC-001 — analisi delle descrizioni executor

## Baseline aggiornata

Il 24/8/2026 `manifest_lint --all` chiude con **0 errori e 113 warning** su 85
manifest su disco. La baseline di 95 warning nel TODO non e' piu' attuale:
dopo la promozione completa allo standard sono entrati nel controllo contratti
che prima non contribuivano allo stesso insieme.

| Famiglia | Warning | Interpretazione |
|---|---:|---|
| descrizioni di argomenti oltre 180 caratteri | 101 | quasi sempre schema operativo, vincoli, esempi o relazione con input upstream |
| descrizione principale oltre 320 caratteri | 9 | separazione fra scopo, confine negativo e forma di output da verificare per executor |
| testa macchina oltre 240 caratteri | 3 | candidati piu' circoscritti, ma ancora soggetti al benchmark di routing |

L'audit strutturale separato censisce 106 contratti configurati, tutti con claim
conforme e **0 finding**. I warning editoriali non indicano quindi un secondo
catalogo legacy o un'incompatibilita' con `metnos.executor/1.0`.

## Classificazione del contenuto

Il superamento della soglia non equivale a prosa eliminabile. Il campione
contiene almeno queste classi:

1. **Confini di autorita' e sicurezza**: credenziali, cookie, root, account,
   mutazioni e consenso. Accorciarli puo' ampliare l'azione percepita.
2. **Semantica di composizione**: `from_step`, proiezioni, completezza, ordine e
   limiti. Spostarli fuori dalla superficie del proposer puo' produrre argomenti
   validi nello schema ma semanticamente errati.
3. **Disambiguazione provider/dominio**: client, endpoint, sorgente e fallback.
   Una riduzione non misurata puo' aumentare collisioni fra executor.
4. **Scelte con semantica ricca**: per esempio il `mode` di `find_urls` supera
   ampiamente la soglia perche' descrive strategie differenti. La sola enum non
   comunica al modello quando scegliere ciascun valore.
5. **Coda realmente editoriale**: nove teste principali e tre teste macchina
   sono i candidati migliori per una revisione manuale per famiglia.

## Decisione

Non viene eseguita una riscrittura massiva. Non esiste un difetto operativo
dimostrato che giustifichi oggi il rischio di alterare routing, argomenti o
confini; la soglia del linter resta un segnale, non un generatore di patch.

Una futura riduzione deve essere un esperimento per singola famiglia e deve
prima congelare un corpus IT/EN con query positive, near-miss, compound e casi
avversariali. Per ogni candidato si misurano pool, executor scelto, argomenti,
token, p50/p95 e boundary conservati. Solo una variante equivalente puo' essere
firmata e promossa; il testo precedente resta il rollback puntuale.

L'analisi e' quindi conclusa. Il debito editoriale resta misurato dal linter ma
non e' una modifica pendente da applicare senza un benchmark che dimostri un
beneficio.
