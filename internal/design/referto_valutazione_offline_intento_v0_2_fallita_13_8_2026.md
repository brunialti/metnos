# Referto di arresto — valutazione offline intent-shadow 0.2 (13/8/2026)

## Esito

**STOP controllato. Nessun punteggio prodotto. Nessuna seconda esecuzione.**

Il controllo pre-gold 0.2 e il relativo freeze hanno superato la verifica:
316/316 record ricalcolati, una sola divergenza diagnostica autorizzata con
`PYTHONHASHSEED=0`, zero divergenze inattese.

Dopo questo PASS è stata avviata una sola volta la valutazione offline 0.2.
L'evaluator ha aperto il confine gold previsto, ma il verificatore canonico
dell'oracolo ha restituito errore prima del calcolo dei punteggi:

`RuntimeError:oracle canonical verification failed`

L'output macchina è:

`internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_2.json`

SHA-256:

`96fc99ddd2d8518737da0bb51da9e88ce09adea5917397d7d4a1d4432cc83fbc`

Il file dichiara `status=error` ed `evaluation_executed=false`. Non esistono
quindi risultati validi per i pannelli 120, 4 o 34, né un verdetto A/B.

Come richiesto, non sono stati modificati evaluator, freeze, raw, batch,
journal, seal, marker, manifest, protocollo o oracolo; non è stata tentata una
seconda valutazione. Non sono state usate rete, GPU, endpoint o servizi.
Checkpoint e handover non sono stati aggiornati.

Prima di qualsiasi nuova valutazione serve una revisione indipendente della
causa del rifiuto canonico. Questo referto non propone né applica correzioni.
