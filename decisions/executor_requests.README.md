# Executor Requests Log

Append-only JSONL: ogni linea e una richiesta NL di executor desiderato.
Lo stato precede `proposed`: e testo libero, non manifest.

Format per linea:
{"ts": "<ISO>", "actor": "host"|"guest:<id>", "channel": "<channel>", "request": "<NL text>", "use_case": "<context>", "id": "<uuid>"}

Cycle: una request viene letta dal synt che, se ha materiale per un manifest stub,
crea un nuovo executor in stato `proposed` e segna la request come `addressed`.
La synt scrive un record `addressed` nello stesso file linkando request_id -> executor_name.

