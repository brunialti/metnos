# Correzione avvio LRE e dati di avanzamento — 2026-09-16

## Perimetro

Autorizzazione: «fissa il codice e passalo in produzione». Include i dati UI
richiesti precedentemente e sviluppati in parallelo: avvio effettivo, percentuale
sulle unità note, fine prevista prudente e `n.a.` per dati non disponibili.
Nessuna modifica di modelli, prompt Tutor, permessi, budget o dati originali.

`admit_prerequisite` conserva la destinazione server quando entrambi i contratti
verificati sono server-only, compreso il default non remoto. Se uno dei due
ammette dispositivi, mantiene la destinazione ricevuta. Nessuna eccezione per
foto o parole della domanda; il contenuto restituito non sceglie la collocazione.
Un rifiuto preliminare registra il punto del controllo e il tipo di eccezione,
senza argomenti, percorsi o testo libero.

La UI legge lo stato, non lo modifica: una query aggregata per pagina, limitata
al proprietario; avvio dalla revisione corrente, percentuale mai al 100% prima
della conclusione; stima assente per foto multifase, dati vecchi, pausa, errori
o motore non pronto. Cinque chiavi i18n complete IT/EN. Nessuna migrazione DB.

## Verifica precedente alla pubblicazione

- Suite integrata LRE, ammissione, contratti, UI HTTP/JavaScript, servizi,
  riconciliazione, rilascio e documentazione: **900 superati, 1 escluso**.
  L'esclusione richiede una prova root/non-root in ambiente dedicato; non
  conteggiata tra i successi.
- Prima esecuzione: 899 superati e un test incapace di caricare il catalogo
  firmato dall'albero di authoring. Corretta la fixture del test: copia privata,
  chiave effimera, digest e stato linguistico coerenti, verifica firme ancora
  attiva; nessuna modifica al catalogo produttivo o ai suoi controlli.
- Regressione integrata: wrapper, guardia, adapter, compilatore e ammissione
  reali, archivio temporaneo e nessun modello. Tre consegne (ripetizione dello
  stesso turno e turno successivo) producono un solo job del proprietario.
- Operazioni remote, device-only, ibride e metadati ostili non vengono
  convertiti in richieste server; la chiamata diretta remota resta rifiutata.
- 99 HTML pubblici validi, riferimenti UI aggiornati; differenze senza errori
  formali. Nessun processo residuo ad alto consumo rilevato.

## Pubblicazione

In preparazione. Non dichiarare completata finché il passaggio canonico,
i controlli di salute e la ricerca reale non hanno un riscontro registrato.
