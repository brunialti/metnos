"""Sidecar Playwright per JS-rendering (ADR 0125, Phase 1).

Modulo separato dal runtime principale: l'import di `playwright` e' isolato
nel `server`, cosi' il client puo' essere caricato anche se Playwright NON
e' installato (probe via HTTP /health, degrade graceful, ADR 0125 §C).

Esposti:
    server.main()           entrypoint aiohttp del sidecar (porta 8771)
    client.is_up()          probe rapido (timeout 1s) del sidecar locale
    client.render(url,**)   POST /render → HTML/text renderizzato

Vincoli:
    - Single-instance (no parallelismo lato sidecar, ADR 0125 §F + §7.4).
    - Fail-loud §2.8: niente eccezioni mute, sempre dict con `ok=false`.
    - Determinismo §7.9 nel client (HTTP); il browser stesso e' non
      deterministico per natura (JS).
"""
