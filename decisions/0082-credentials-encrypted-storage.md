---
id: 0082
title: Storage credenziali cifrato + scrub turn log + capability auth.password_storage
date: 2026-05-04
status: accepted
area: runtime, security, executor
related:
  - 0042  # capability + sandboxing
  - 0078  # http api phase 1 (admin.key shared secret)
  - 0081  # web crawler multi-tier (companion)
---

## Context

Il caso d'uso «pipeline web autenticata» (ADR 0081) richiede di salvare
credenziali utente (username + password) per siti come registri
elettronici, banche, news redazionali behind-paywall. Tre vincoli:

1. **Niente plaintext su disco**: credenziali in chiaro nel filesystem
   sarebbero un disastro per il modello di minaccia di un assistente
   self-hosted (qualunque lettura del FS le esporrebbe).
2. **Niente passphrase utente**: l'utente non vuole digitare una
   passphrase ogni volta che chiede «scarica le circolari di oggi».
   L'assistente deve poter agire offline per task ricorrenti.
3. **Scrub nel turn log**: le query Telegram tipo «accedi col mio user
   X password Y» devono **non** finire in chiaro nel JSONL del turno.
   In RAM la credenziale puo' restare per il turn, su disco mai.

## Decision

### 1. `runtime/credentials.py` (~150 LOC)

Cifratura simmetrica derivata da `~/.config/metnos/admin.key` via
HKDF-SHA256 con salt random per file. Cipher: `cryptography.fernet.
Fernet` (gia' presente nel progetto per sign.py / http_auth).

**Layout file** `~/.config/metnos/credentials/<domain>.json.age` (mode
0600):

```
<salt_b64_url_safe>\n<fernet_token>
```

Il salt e' diverso per ogni file → rotazione/replay attack mitigation.
La master key e' la admin.key locale (256-bit hex generata da
`http_auth.get_or_create_admin_key()` al primo avvio del server HTTP).

**Contratto**:

```python
def store(domain: str, payload: dict) -> Path
def load(domain: str) -> dict | None
def list_domains() -> list[str]
def remove(domain: str) -> bool
def fingerprint(domain: str) -> str | None  # sha256[:16] della pwd
```

**Schema payload** (non validato dal modulo, convenzione):

```json
{
  "login_url": "https://web.spaggiari.eu/cvv/app/default",
  "method": "POST",
  "form_data": {"username": "...", "password": "..."},
  "session_cookie_names": ["JSESSIONID", "PHPSESSID"]
}
```

`fingerprint(domain)` ritorna sha256[:16] della pwd salvata: serve per
audit log («password cambiata?» = «fingerprint diverso da snapshot
precedente?») senza esporre la pwd stessa.

### 2. Scrub credenziali nel turn log

`runtime/agent_runtime.py` aggiunge:

```python
_CRED_RE = re.compile(r"(\bp(?:wd|assword|sw|ass)\s*[:=]?\s*)(\S+)", re.IGNORECASE)
_USER_RE = re.compile(r"(\bu(?:ser|name|tente)\s*[:=]?\s*)(\S+)", re.IGNORECASE)
```

Funzione `_scrub_credentials(text)` sostituisce il valore con
`<REDACTED:cred>`, idempotente. Funzione `_scrub_args_recursive(node, total)`
visita dict/list/str e applica scrubbing inclusi value diretti di
chiavi `password|pwd|psw|pass`.

**Quando**: in `TurnLog.write()`, dopo `asdict(self)` e prima della
serializzazione JSONL. `user_query` + ogni `step.raw_args` + ogni
`step.resolved_args` vengono scrubati. Il record ottiene
`redacted: true` + `n_redacted_fields: int` se almeno un match.

**Motivazione DOPO il PLANNER**: durante il turno la credenziale serve
per i tool reali (es. `login_session` la legge dal file cifrato); lo
scrub e' di output, non di input.

### 3. Capability `auth.password_storage`

Nuova capability dichiarata da `login_session`. Separa l'audit:
- `network.read` / `network.write` → traffico HTTP normale
- `auth.password_storage` → **lettura** del file cifrato
  `~/.config/metnos/credentials/<domain>.json.age`

Vaglio puo' auto-approvare per tier 3 (owned) e chiedere conferma per
gli altri.

### 4. Storage prima volta

Il **salvataggio** della credenziale è **fuori** dal flusso planner —
non vogliamo che il PLANNER LLM «venga in possesso» di una pwd attraverso
il prompt. CLI dedicata o admin web UI:

```python
import credentials
credentials.store("web.spaggiari.eu", {
  "login_url": "...",
  "method": "POST",
  "form_data": {"username": "u", "password": "p"},
  "session_cookie_names": ["JSESSIONID"],
})
```

L'admin key locale fa da master: chi controlla `~/.config/metnos/admin.key`
(mode 0600) puo' decifrare. Il backup deve essere offline.

## Alternatives considered

- **Passphrase utente per ogni op**: scartato, viola il caso d'uso
  ricorrente notturno (l'assistente agisce mentre l'utente dorme).
- **OS keyring (libsecret)**: dipende dal desktop env, non self-hosted
  pure. Funzionerebbe sul laptop ma non sul server `.33` headless.
- **Vault esterno (HashiCorp Vault, ecc.)**: overkill per il modello
  single-user self-hosted.
- **Base64 «obfuscation»**: scartato ovviamente, non e' cifratura.
- **Hash della pwd**: non puoi fare login con un hash. Serve la pwd in
  chiaro in RAM al momento della POST.
- **GPG**: ottimo, ma richiede config gpg-agent che e' overhead per
  questa singola feature. Fernet via cryptography Python e' gia'
  installato e copre il modello di minaccia.

## Consequences

- **Nuovo modulo runtime**: `runtime/credentials.py` (con 6 funzioni
  pubbliche + 6 test in `tests/runtime/safety/test_credentials.py`).
- **Modifiche a TurnLog**: 2 nuovi campi (`redacted`, `n_redacted_fields`)
  + scrub in `write()` (~30 LOC). 6 test in
  `tests/runtime/safety/test_scrub_credentials.py`.
- **Sicurezza modello**: chi compromette `admin.key` compromette tutte
  le credenziali. La admin.key e' mode 0600 ed e' generata localmente.
  Per trasportare credenziali fra device: re-store sul nuovo device,
  niente sync.
- **Audit**: `fingerprint(domain)` permette al daemon scheduler di
  notare cambi-pwd e disabilitare task ricorrenti che fallirebbero.
- **Non implementato**: rotazione automatica chiave (master key locale
  resta la stessa salvo manuale rigenerazione + rifirma di tutto).
  MFA / TOTP (lascia hook nel payload; la POST POST manualmente da
  un sito-by-site basis).

## References

- `runtime/credentials.py` (modulo cifratura).
- `runtime/agent_runtime.py` (`_scrub_credentials`, `_scrub_args_recursive`,
  TurnLog fields, write() scrub).
- `tests/runtime/safety/test_credentials.py` (6 test).
- `tests/runtime/safety/test_scrub_credentials.py` (6 test).
- `executors/login_session/` (consumer di credentials.load).
- ADR 0078 (admin.key origin: http_auth.get_or_create_admin_key).
- ADR 0081 (companion: web crawler che usa cookies prodotti da login).
