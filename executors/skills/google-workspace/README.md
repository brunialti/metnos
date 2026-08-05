# Google Workspace skill

This first-party skill connects Metnos to Gmail, Calendar, Drive, Contacts,
Sheets, and Docs through one Metnos-managed OAuth setup. It remains dormant
until its credential files are present and valid.

The planner uses canonical Metnos executors. Provider selection and API details
remain behind the backend resolver; a user request does not need to name the
Google implementation.

## Contents

| Path | Purpose |
|---|---|
| `SKILL.md` | capability description and complete OAuth setup procedure |
| `scripts/setup.py` | OAuth check, authorization URL, code exchange, and revocation |
| `scripts/google_api.py` | stable JSON command boundary for Workspace operations |
| `scripts/gws_bridge.py` | optional bridge to the `gws` command when installed |
| `scripts/_scopes.py` | service-to-OAuth-scope mapping |
| `scripts/_skill_home.py` | installed skill directory resolution |
| `references/gmail-search-syntax.md` | Gmail query operators used by search commands |

## Credentials

`google_client_secret.json` and `google_token.json` live only in the installed
skill directory under the Metnos user data root. They are ignored by version
control and are never bundled in the public repository.

Run the setup through Metnos or follow the step-by-step commands in `SKILL.md`.
The token refreshes through the same local credential file. Revocation removes
the local authorization state and does not alter executor manifests.

## Execution boundary

`google_api.py` emits structured JSON and prefers the optional `gws` command
when it can preserve the Metnos contract. Otherwise it uses the bundled Python
implementation. This backend choice is deterministic configuration, not an LLM
decision.
