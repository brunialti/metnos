"""
queries.py — battery of 10 e2e queries per verb (13 verbi specializzati).

Le query NON si modificano per farle passare. Se falliscono, si fixa il
codice (verb addendum / executor / runtime).

Le fixture si creano nelle dirs /tmp/audit/fs/<verb>/ in modo idempotente.
Le query mail puntano alla mailbox metnos@metnos.com (l'unica disponibile).
"""
import shutil
from pathlib import Path

ROOT = Path("/tmp/audit/fs")


def _write(p: Path, content: str = ""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _ensure_clean(d: Path):
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)


def setup_fixtures(verb: str):
    """Setup idempotente delle fixture per un singolo verbo."""
    base = ROOT / verb
    if verb == "read":
        _ensure_clean(base)
        _write(base / "notes.txt", "Riga 1\nRiga 2\nRiga 3\n")
        _write(base / "data.csv", "name,age\nAlice,30\nBob,25\n")
        _write(base / "big.txt", ("X" * 50 + "\n") * 100)
        _write(base / "multi.txt", "uno\ndue\ntre\nquattro\n")

    elif verb == "list":
        _ensure_clean(base)
        for n in ["a.txt", "b.txt", "c.py", "d.md"]:
            _write(base / n, "x")
        (base / "sub").mkdir()
        _write(base / "sub" / "nested.txt", "y")
        (base / "empty").mkdir()

    elif verb == "get":
        _ensure_clean(base)
        _write(base / "file.txt", "contenuto di test\n")

    elif verb == "find":
        _ensure_clean(base)
        for n in ["alpha.py", "beta.py", "gamma.txt", "delta.md"]:
            _write(base / n, "TODO: handle this\n" if "alpha" in n else "ok\n")
        (base / "sub").mkdir()
        _write(base / "sub" / "epsilon.py", "metnos init\n")
        _write(base / "appunti.txt", "appunti del 30/4 metnos\n")

    elif verb == "filter":
        _ensure_clean(base)
        for i, n in enumerate(["small.py", "med.py", "big.py", "tiny.txt", "huge.csv"]):
            _write(base / n, "x" * (10 ** i))

    elif verb == "describe":
        _ensure_clean(base)
        _write(base / "data.csv", "x,y\n1,2\n3,4\n5,6\n7,8\n9,10\n")
        _write(base / "notes.txt", "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20)
        _write(base / "numbers.csv", "n\n10\n20\n30\n40\n50\n60\n70\n80\n90\n100\n")
        _write(base / "article.txt", ("Metnos e' un assistente personale che gira self-hosted. " * 30))
        _write(base / "data.json", '{"name": "metnos", "version": "1.1", "verbs": ["read","write","move"]}\n')
        _write(base / "series.csv", "v\n" + "\n".join(str(i * 2) for i in range(1, 21)) + "\n")

    # Note: `fetch` removed from vocabulary on 2026-05-03; HTTP GET is now
    # the responsibility of the `get` verb (get_urls), so its setup falls
    # under the `get` branch (no specific dir scaffolding needed).

    elif verb == "extract":
        _ensure_clean(base)
        _write(base / "log.txt",
               "INFO start\nERROR connection refused\nINFO retry\nERROR timeout\nWARN slow query\nINFO ok\n")
        _write(base / "code.py", "def foo():\n    # TODO: implement\n    pass\n# TODO: tests\n")
        _write(base / "text.txt",
               "Contact me at alice@example.com or bob@test.org. See https://metnos.com and http://example.org.\n"
               "Phone: +39 333 1234567 or 02 9876543. Date: 2026-04-30.\n")
        _write(base / "list.txt", "\n".join(f"item-{i}" for i in range(1, 21)) + "\n")
        _write(base / "numbers.txt", "Found 42 apples, 17 oranges, and 3.14 pies on day 2026-04-30.\n")
        _write(base / "calendar.txt",
               "Meeting on 2026-05-01.\nDeadline: 30/04/2026.\nReview: May 5, 2026.\n")
        _write(base / "contacts.txt",
               "Alice +39 333 1234567\nBob: 02-9876543\nCarol mobile +1 555 123 4567\n")

    elif verb == "write":
        _ensure_clean(base)
        # write_files vorra' destinazioni; le creiamo come parent dir vuota
        _write(base / "log.txt", "riga preesistente\n")  # per append test

    elif verb == "create":
        _ensure_clean(base)

    elif verb == "move":
        _ensure_clean(base)
        src = base / "src"
        dst = base / "dst"
        src.mkdir(parents=True)
        dst.mkdir(parents=True)
        for n in ["a.txt", "b.txt", "c.txt", "d.txt", "e.csv"]:
            _write(src / n, n)

    elif verb == "delete":
        _ensure_clean(base)
        for n in ["d1.txt", "d2.txt", "d3.txt"]:
            _write(base / n, n)
        for n in ["x.tmp", "y.tmp", "z.tmp"]:
            _write(base / n, n)
        for n in ["a.log", "b.log", "c.log"]:
            _write(base / n, n)

    elif verb == "send":
        _ensure_clean(base)
        _write(base / "attach.txt", "allegato di prova\n")


# === BATTERIA QUERY ==========================================================
# 10 query per verbo. Pattern executor atteso: regex full-match.
# Quando piu' executor sono validi (es. read_files vs read_messages),
# usiamo un'alternation. Quando il verbo lavora in pipeline, ammettiamo che
# il primo step pertinente al verbo compaia tra i tool invocati.

VERBS = {
    "read": {
        "queries": [
            {"q": "leggi /tmp/audit/fs/read/notes.txt",
             "expect_executor_re": r"read_files"},
            {"q": "read /tmp/audit/fs/read/notes.txt",
             "expect_executor_re": r"read_files"},
            {"q": "fammi vedere il contenuto di /tmp/audit/fs/read/data.csv",
             "expect_executor_re": r"read_files(_csv)?"},
            {"q": "leggi le mie ultime 3 mail",
             "expect_executor_re": r"read_messages"},
            {"q": "show last 5 emails",
             "expect_executor_re": r"read_messages"},
            {"q": "leggi /tmp/audit/fs/read/big.txt e dimmi quante righe sono",
             "expect_executor_re": r"read_files"},
            {"q": "apri /tmp/audit/fs/read/multi.txt",
             "expect_executor_re": r"read_files"},
            {"q": "leggi /tmp/audit/fs/read/file_inesistente.txt",
             "expect_executor_re": r"read_files",
             "expect_kind": "answer"},
            {"q": "fammi vedere le mail di oggi su metnos",
             "expect_executor_re": r"read_messages"},
            {"q": "visualizza il file /tmp/audit/fs/read/notes.txt",
             "expect_executor_re": r"read_files"},
        ],
    },

    "list": {
        "queries": [
            {"q": "elenca i file in /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
            {"q": "list files in /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
            {"q": "che cosa c'e' nella cartella /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
            {"q": "mostrami le sottocartelle di /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
            {"q": "elenca le cartelle della mia mailbox",
             "expect_executor_re": r"list_(folders|messages|dirs)"},
            {"q": "che file ho in /tmp/audit/fs/list/empty",
             "expect_executor_re": r"list_dirs"},
            {"q": "ls /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
            {"q": "mostra tutti i file in /tmp/audit/fs/list/sub",
             "expect_executor_re": r"list_dirs"},
            {"q": "elenca ricorsivamente i file in /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
            {"q": "show me the contents of directory /tmp/audit/fs/list",
             "expect_executor_re": r"list_dirs"},
        ],
    },

    "get": {
        "queries": [
            {"q": "che ora e?",
             "expect_executor_re": r"get_now"},
            {"q": "what time is it",
             "expect_executor_re": r"get_now"},
            {"q": "dove sono?",
             "expect_executor_re": r"get_location"},
            {"q": "che ora e a Tokyo",
             "expect_executor_re": r"get_now"},
            {"q": "data di modifica di /tmp/audit/fs/get/file.txt",
             "expect_executor_re": r"get_files"},
            {"q": "metadata di /tmp/audit/fs/get/file.txt",
             "expect_executor_re": r"get_files"},
            {"q": "where am I",
             "expect_executor_re": r"get_location"},
            {"q": "trova posti vicino a Roma",
             "expect_executor_re": r"get_places|find_places"},
            {"q": "che data e oggi",
             "expect_executor_re": r"get_now"},
            {"q": "permessi del file /tmp/audit/fs/get/file.txt",
             "expect_executor_re": r"get_files"},
        ],
    },

    "find": {
        "queries": [
            {"q": "trova file *.py in /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
            {"q": "find *.txt in /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
            {"q": "cerca file con 'TODO' in /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
            {"q": "trova ristoranti vicino a Milano",
             "expect_executor_re": r"find_places"},
            {"q": "find Italian restaurants near Rome",
             "expect_executor_re": r"find_places"},
            {"q": "trova tutti i .md in /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
            {"q": "trova file piu' grandi di 0 bytes in /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
            {"q": "find files in /tmp/audit/fs/find/sub",
             "expect_executor_re": r"find_files"},
            {"q": "cerca dove ho salvato il file appunti.txt sotto /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
            {"q": "trova file che contengono 'metnos' in /tmp/audit/fs/find",
             "expect_executor_re": r"find_files"},
        ],
    },

    "filter": {
        "queries": [
            {"q": "tra i file in /tmp/audit/fs/filter, dammi solo quelli .py",
             "expect_executor_re": r"filter_entries"},
            {"q": "elenca i file in /tmp/audit/fs/filter e tienine solo quelli con 'big' nel nome",
             "expect_executor_re": r"filter_entries"},
            {"q": "filter the files in /tmp/audit/fs/filter and keep only those bigger than 100 bytes",
             "expect_executor_re": r"filter_entries"},
            {"q": "lista i file in /tmp/audit/fs/filter e filtra solo .csv",
             "expect_executor_re": r"filter_entries"},
            {"q": "delle mie ultime 10 mail, dammi solo quelle non lette",
             "expect_executor_re": r"filter_entries"},
            {"q": "tra i file in /tmp/audit/fs/filter prendi solo quelli minori di 10 bytes",
             "expect_executor_re": r"filter_entries"},
            {"q": "list files in /tmp/audit/fs/filter and keep only those smaller than 1000 bytes",
             "expect_executor_re": r"filter_entries"},
            {"q": "elenca le 5 ultime mail e tieni solo quelle del 2026",
             "expect_executor_re": r"filter_entries"},
            {"q": "guardando i file in /tmp/audit/fs/filter, scarta quelli con estensione txt",
             "expect_executor_re": r"filter_entries"},
            {"q": "fra i file in /tmp/audit/fs/filter dammi solo quelli che iniziano per 'b'",
             "expect_executor_re": r"filter_entries"},
        ],
    },

    "describe": {
        "queries": [
            {"q": "descrivimi il file /tmp/audit/fs/describe/data.csv",
             "expect_executor_re": r"describe_(entries|numbers|files)|read_files"},
            {"q": "describe the contents of /tmp/audit/fs/describe/data.csv",
             "expect_executor_re": r"describe_(entries|numbers|files)|read_files"},
            {"q": "descrivimi le ultime 3 mail",
             "expect_executor_re": r"describe_entries|read_messages"},
            {"q": "che contiene /tmp/audit/fs/describe/notes.txt? riassumi in 1 frase",
             "expect_executor_re": r"describe_entries|read_files"},
            {"q": "summarize my last 5 emails",
             "expect_executor_re": r"describe_entries|read_messages"},
            {"q": "descrivi i numeri in /tmp/audit/fs/describe/numbers.csv",
             "expect_executor_re": r"describe_numbers|read_files"},
            {"q": "describe the files in /tmp/audit/fs/describe",
             "expect_executor_re": r"describe_entries|list_dirs"},
            {"q": "fammi un riassunto di /tmp/audit/fs/describe/article.txt",
             "expect_executor_re": r"describe_entries|read_files"},
            {"q": "describe the structure of /tmp/audit/fs/describe/data.json",
             "expect_executor_re": r"describe_entries|read_files"},
            {"q": "che pattern vedi nei numeri di /tmp/audit/fs/describe/series.csv",
             "expect_executor_re": r"describe_numbers|read_files"},
        ],
    },

    # `fetch` was a separate audit cluster until 2026-05-03; verb removed
    # and its queries merged under "get" (HTTP GET = get_urls).
    "_legacy_fetch_queries": {
        "queries": [
            {"q": "scarica https://httpbin.org/get e dimmi cosa contiene",
             "expect_executor_re": r"get_urls"},
            {"q": "fetch https://httpbin.org/json",
             "expect_executor_re": r"get_urls"},
            {"q": "scarica https://example.com",
             "expect_executor_re": r"get_urls"},
            {"q": "prendi https://api.github.com/zen",
             "expect_executor_re": r"get_urls"},
            {"q": "fetch the content of https://httpbin.org/headers",
             "expect_executor_re": r"get_urls"},
            {"q": "scarica https://httpbin.org/uuid",
             "expect_executor_re": r"get_urls"},
            {"q": "scarica https://httpbin.org/ip e dimmi che IP vedo",
             "expect_executor_re": r"get_urls"},
            {"q": "fetch https://httpbin.org/user-agent",
             "expect_executor_re": r"get_urls"},
            {"q": "vai a https://httpbin.org/anything e mostrami il body",
             "expect_executor_re": r"get_urls"},
            {"q": "scarica https://www.gutenberg.org/cache/epub/11/pg11.txt",
             "expect_executor_re": r"get_urls"},
        ],
    },

    "extract": {
        "queries": [
            {"q": "estrai le righe da /tmp/audit/fs/extract/log.txt che contengono ERROR",
             "expect_executor_re": r"filter_texts_lines|read_files"},
            {"q": "extract lines matching 'TODO' from /tmp/audit/fs/extract/code.py",
             "expect_executor_re": r"filter_texts_lines|read_files"},
            {"q": "estrai gli email da /tmp/audit/fs/extract/text.txt",
             "expect_executor_re": r"extract_(emails|lines_text)|read_files"},
            {"q": "extract URLs from /tmp/audit/fs/extract/text.txt",
             "expect_executor_re": r"extract_(urls|lines_text)|read_files"},
            {"q": "estrai le prime 5 righe di /tmp/audit/fs/extract/list.txt",
             "expect_executor_re": r"filter_texts_lines|read_files"},
            {"q": "trova le righe con 'WARN' in /tmp/audit/fs/extract/log.txt",
             "expect_executor_re": r"filter_texts_lines|read_files"},
            {"q": "estrai i numeri da /tmp/audit/fs/extract/numbers.txt",
             "expect_executor_re": r"extract_(numbers|lines_text)|read_files"},
            {"q": "extract dates from /tmp/audit/fs/extract/calendar.txt",
             "expect_executor_re": r"extract_(dates|lines_text)|read_files"},
            {"q": "estrai i numeri di telefono da /tmp/audit/fs/extract/contacts.txt",
             "expect_executor_re": r"extract_(phone_numbers|lines_text)|read_files"},
            {"q": "extract all unique words containing 'mail' from /tmp/audit/fs/extract/text.txt",
             "expect_executor_re": r"filter_texts_lines|read_files"},
        ],
    },

    "write": {
        "queries": [
            {"q": "scrivi 'hello world' nel file /tmp/audit/fs/write/hello.txt",
             "expect_executor_re": r"write_files"},
            {"q": "write 'test content' to /tmp/audit/fs/write/test.txt",
             "expect_executor_re": r"write_files"},
            {"q": "crea un file /tmp/audit/fs/write/note.txt con contenuto 'nota del 30/4'",
             "expect_executor_re": r"write_files"},
            {"q": "salva la stringa 'AAA\\nBBB' in /tmp/audit/fs/write/multi.txt",
             "expect_executor_re": r"write_files"},
            {"q": "scrivi un csv di 3 righe (header + 2 righe di dati) in /tmp/audit/fs/write/data.csv",
             "expect_executor_re": r"write_files"},
            {"q": "write a JSON object {\"a\": 1, \"b\": 2} to /tmp/audit/fs/write/d.json",
             "expect_executor_re": r"write_files"},
            {"q": "appendi la riga 'nuova riga' al file /tmp/audit/fs/write/log.txt",
             "expect_executor_re": r"write_files"},
            {"q": "scrivi nel file /tmp/audit/fs/write/now.txt l'ora attuale",
             "expect_executor_re": r"write_files"},
            {"q": "salva nel file /tmp/audit/fs/write/poem.txt una poesia di 4 versi",
             "expect_executor_re": r"write_files"},
            {"q": "create a file /tmp/audit/fs/write/readme.md with content '# Test'",
             "expect_executor_re": r"write_files"},
        ],
    },

    "create": {
        "queries": [
            {"q": "crea cartella /tmp/audit/fs/create/new1",
             "expect_executor_re": r"create_dirs"},
            {"q": "create directory /tmp/audit/fs/create/new2",
             "expect_executor_re": r"create_dirs"},
            {"q": "fai una directory /tmp/audit/fs/create/sub/nested",
             "expect_executor_re": r"create_dirs"},
            {"q": "make folder /tmp/audit/fs/create/folder3",
             "expect_executor_re": r"create_dirs"},
            {"q": "crea le directory /tmp/audit/fs/create/d4 e /tmp/audit/fs/create/d5",
             "expect_executor_re": r"create_dirs"},
            {"q": "crea una nuova cartella chiamata projects in /tmp/audit/fs/create",
             "expect_executor_re": r"create_dirs"},
            {"q": "create folder /tmp/audit/fs/create/with space",
             "expect_executor_re": r"create_dirs"},
            {"q": "crea le cartelle Q1 Q2 Q3 Q4 in /tmp/audit/fs/create",
             "expect_executor_re": r"create_dirs"},
            {"q": "create folder named 'Test 2026' in /tmp/audit/fs/create",
             "expect_executor_re": r"create_dirs"},
            {"q": "fai una directory annidata /tmp/audit/fs/create/a/b/c/d",
             "expect_executor_re": r"create_dirs"},
        ],
    },

    "move": {
        "queries": [
            {"q": "sposta /tmp/audit/fs/move/src/a.txt in /tmp/audit/fs/move/dst/",
             "expect_executor_re": r"move_files"},
            {"q": "move /tmp/audit/fs/move/src/b.txt to /tmp/audit/fs/move/dst/",
             "expect_executor_re": r"move_files"},
            {"q": "sposta tutti i .txt da /tmp/audit/fs/move/src a /tmp/audit/fs/move/dst",
             "expect_executor_re": r"move_files"},
            {"q": "move all .csv from /tmp/audit/fs/move/src to /tmp/audit/fs/move/dst",
             "expect_executor_re": r"move_files"},
            {"q": "rinomina /tmp/audit/fs/move/src/c.txt in /tmp/audit/fs/move/src/c_renamed.txt",
             "expect_executor_re": r"move_files"},
            {"q": "trasferisci /tmp/audit/fs/move/src/d.txt in /tmp/audit/fs/move/dst/",
             "expect_executor_re": r"move_files"},
            {"q": "sposta in Junk le mail di oggi con oggetto contenente 'audit test'",
             "expect_executor_re": r"move_messages"},
            {"q": "move messages from metnos@metnos.com to folder Archive",
             "expect_executor_re": r"move_messages"},
            {"q": "sposta nella cartella Archive le mail piu' vecchie di 30 giorni",
             "expect_executor_re": r"move_messages"},
            {"q": "metti in Archive le mail di test su metnos",
             "expect_executor_re": r"move_messages"},
        ],
    },

    "delete": {
        "queries": [
            {"q": "elimina /tmp/audit/fs/delete/d1.txt",
             "expect_executor_re": r"delete_files"},
            {"q": "delete /tmp/audit/fs/delete/d2.txt",
             "expect_executor_re": r"delete_files"},
            {"q": "cancella tutti i file .tmp in /tmp/audit/fs/delete",
             "expect_executor_re": r"delete_files"},
            {"q": "rimuovi /tmp/audit/fs/delete/d3.txt",
             "expect_executor_re": r"delete_files"},
            {"q": "delete all .log from /tmp/audit/fs/delete",
             "expect_executor_re": r"delete_files"},
            {"q": "elimina la mail piu' vecchia con oggetto 'audit test'",
             "expect_executor_re": r"delete_messages"},
            {"q": "rimuovi le mail di oggi con oggetto 'audit test'",
             "expect_executor_re": r"delete_messages"},
            {"q": "delete emails from metnos@metnos.com with subject 'audit test'",
             "expect_executor_re": r"delete_messages"},
            {"q": "elimina /tmp/audit/fs/delete/inesistente.txt",
             "expect_executor_re": r"delete_files"},
            {"q": "cancella i file vuoti in /tmp/audit/fs/delete",
             "expect_executor_re": r"delete_files"},
        ],
    },

    "send": {
        "queries": [
            {"q": "manda una mail a metnos@metnos.com con oggetto 'audit test 1' e corpo 'ciao 1'",
             "expect_executor_re": r"send_messages"},
            {"q": "send email to metnos@metnos.com subject 'audit test 2' body 'hello 2'",
             "expect_executor_re": r"send_messages"},
            {"q": "scrivi a metnos@metnos.com per dirgli che il test 3 e' partito (oggetto: audit test 3)",
             "expect_executor_re": r"send_messages"},
            {"q": "manda mail a metnos@metnos.com oggetto 'audit test 4' corpo 'corpo 4'",
             "expect_executor_re": r"send_messages"},
            {"q": "send a hello mail to metnos@metnos.com (subject 'audit test 5')",
             "expect_executor_re": r"send_messages"},
            {"q": "componi e invia a metnos@metnos.com (oggetto 'audit test 6') una breve mail di prova",
             "expect_executor_re": r"send_messages"},
            {"q": "manda una mail di test a metnos@metnos.com (oggetto: audit test 7)",
             "expect_executor_re": r"send_messages"},
            {"q": "scrivi a metnos@metnos.com (oggetto 'audit test 8') una poesia di 2 versi",
             "expect_executor_re": r"send_messages"},
            {"q": "send to metnos@metnos.com subject 'audit test 9' body 'final test'",
             "expect_executor_re": r"send_messages"},
            {"q": "manda mail a metnos@metnos.com con oggetto 'audit test 10' e corpo che riporti l'ora attuale",
             "expect_executor_re": r"send_messages"},
        ],
    },
}

assert len(VERBS) == 13, f"expected 13 verbs, got {len(VERBS)}"
for v, d in VERBS.items():
    assert len(d["queries"]) == 10, f"verb {v}: expected 10 queries, got {len(d['queries'])}"
