# Metnos systemd units

User units. Niente root.

## metnos-telegram-daemon.service

Long-poll Telegram + dispatch al runtime. Persistenza `last_update_id` in
`~/.local/state/metnos/telegram_offset` (riavvii senza riprocessare).

### Installazione

```bash
mkdir -p ~/.config/systemd/user
cp /opt/metnos/systemd/metnos-telegram-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now metnos-telegram-daemon.service
```

### Stato e log

```bash
systemctl --user status metnos-telegram-daemon
journalctl --user -u metnos-telegram-daemon -f
```

### Prerequisiti

- `~/.config/metnos/credentials.env` con `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` (chmod 600).
- Linger abilitato perché il servizio giri anche senza login interattivo:
  `loginctl enable-linger $USER` (richiede sudo).
