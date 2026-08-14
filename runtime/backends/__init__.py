"""runtime/backends — area plugin Metnos (Q1 canonical+args).

Layout: `runtime/backends/<area>/<channel>_<provider>.py`. Ogni file espone
le funzioni dei verbi canonici per UN provider builtin (es.
`messaging/email_metnos.py` per i 5 verbi messaging via SMTP/IMAP locale).

Predisposizione plugin esterni (ADR pending, decisione 13/5/2026):
- Per ora SOLO builtin con import statici nel dispatcher dell'executor.
- Il loader plugin (futuro) potra' arricchire la dispatch table degli
  executor scansionando `~/.local/share/metnos/plugins/<area>-*/backends/`.
- NIENTE registry magico ne' decorator: il pattern resta semplice,
  esplicito, Pythonico (the design guide §7.2 + §7.9).
"""
