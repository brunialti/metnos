"""test_vaglio_content_not_access — la guardia forbidden-path non deve scambiare
il CONTENUTO di un dato per un accesso.

Bug live (turn dbc5a605): «riassumi i file .md su github» → describe_entries
riceve fra le entries un README che CITA `~/.config/metnos/credentials.env` come
prerequisito → la guardia, scandendo ogni stringa degli args, lo legge come tocco
a un forbidden path e BLOCCA il riassunto. È un falso positivo: le entries sono
dato prodotto a monte da un produttore già verificato.

Fix: lo scan path-pattern salta le chiavi di CONTENUTO (`content`, `body`, …),
ma resta su ogni path/id/value. La security non cala: i path d'ACCESSO (incl.
`entries[].path`) sono ancora tutti scansionati. Questi test difendono la regola.
"""
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parent.parent
if str(_RT) not in sys.path:
    sys.path.insert(0, str(_RT))

from vaglio import guard_check  # noqa: E402


def test_content_citing_forbidden_path_passes():
    # Il bug: riassumere un doc che CITA credentials.env è lecito.
    ok, _ = guard_check("describe_entries", {"entries": [
        {"path": "systemd/README.md",
         "content": "Prerequisito: ~/.config/metnos/credentials.env con TOKEN"}]})
    assert ok is True
    # idem per testo da scrivere / corpo mail: è contenuto, non accesso.
    assert guard_check("write_files", {"path": "/tmp/o.md",
                       "content": "vedi ~/.ssh/config"})[0] is True
    assert guard_check("send_messages", {"to": "x@y.z",
                       "body": "il file /etc/shadow ha le password"})[0] is True


def test_real_access_still_blocked():
    # Un path d'ACCESSO forbidden resta bloccato (security invariata).
    assert guard_check("read_files",
                       {"paths": ["~/.config/metnos/credentials.env"]})[0] is False
    assert guard_check("read_files", {"paths": ["~/.ssh/id_rsa"]})[0] is False
    assert guard_check("delete_files", {"path": "/root/.bashrc"})[0] is False


def test_entry_path_still_scanned():
    # Il path di una entry (target potenziale a valle) resta scansionato: solo il
    # CONTENT è esente, non l'intero sottoalbero entries.
    ok, _ = guard_check("delete_files", {"entries": [
        {"path": "/root/.ssh/id_rsa", "name": "id_rsa"}]})
    assert ok is False
