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

_RT = (Path(__file__).resolve().parents[3] / "runtime")

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


# ── protected paths (platform_policy wired 2/7/2026, decisione Roberto) ────

def test_mutant_on_protected_tree_blocked():
    ok, why = guard_check("write_files", {"path": "/etc/nuovo.conf",
                                          "content": "x"})
    assert ok is False and "protetto" in (why or "")
    assert guard_check("delete_files", {"paths": ["/usr/lib/x.so"]})[0] is False
    assert guard_check("move_files", {"src": "/tmp/a", "dst": "/var/a"})[0] is False
    assert guard_check("create_dirs", {"path": "/root/nuova"})[0] is False


def test_read_on_protected_tree_allowed():
    # Le letture restano libere: la policy protegge la SCRITTURA.
    assert guard_check("read_files", {"paths": ["/etc/hosts"]})[0] is True
    assert guard_check("find_files", {"base_path": "/usr/share"})[0] is True


def test_mutant_on_normal_paths_allowed():
    assert guard_check("write_files", {"path": "/tmp/report.md",
                                       "content": "x"})[0] is True
    assert guard_check("delete_files",
                       {"paths": ["/home/utente/vecchio.txt"]})[0] is True
    # valori non-path (folder mail) non matchano mai
    assert guard_check("move_messages", {"dst_folder": "Junk"})[0] is True


def test_protected_string_under_content_key_not_blocked():
    # «scrivi un file che DOCUMENTA /etc/fstab» → /etc nel CONTENUTO è dato.
    assert guard_check("write_files", {"path": "/tmp/doc.md",
                                       "content": "vedi /etc/fstab"})[0] is True


def test_admin_builtin_not_affected():
    # admin/sudoer (verb-unique, §2.2) non hanno prefisso mutante.
    assert guard_check("admin", {"target": "/etc/systemd/system"})[0] is True
