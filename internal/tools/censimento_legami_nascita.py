"""C3 — censisce che cosa, fuori dalla radice di nascita, nomina l'insieme corrente.

Gli identificativi si ACQUISISCONO dalla radice (mai copiati a mano); le radici
esaminate sono dichiarate; gli archivi SQLite si interrogano tabella per tabella
invece di cercare testo nei byte del file.
"""
import json, os, sqlite3, sys, pathlib

BIRTH = pathlib.Path(os.path.expanduser("~/.config/metnos/birth"))
prep = json.loads((BIRTH / "prepared-v1.json").read_text())
setdir = BIRTH / prep["authority_set"]
material = json.loads((setdir / "context" / "material-v1.json").read_text())
prods = sorted(p.name for p in (setdir / "producers").iterdir()) if (setdir/"producers").is_dir() else []

ident = {
    "set_id": prep["set_id"],
    "author_store_public_inventory_sha256": prep["author_store_public_inventory_sha256"],
    "context_material_sha256": prep["context_material_sha256"],
    "set_json_sha256": prep["set_json_sha256"],
    "transaction_id": prep["transaction_id"],
    "prepared_admission_context_id": material["prepared_admission_context_id"],
    "prepared_context_epoch": material["prepared_context_epoch"],
}
for i, p in enumerate(prods):
    ident[f"producer_{i}"] = p

print("== IDENTIFICATIVI ACQUISITI DALLA RADICE ==")
for k, v in ident.items():
    print(f"  {k:38} {v}")
valori = {v for v in ident.values()}
# anche le forme nude senza il prefisso sha256: e senza il prefisso p-
nudi = {v.split(":",1)[1] for v in valori if ":" in v} | {v[2:] for v in valori if v.startswith("p-")}
tutti = valori | nudi

RADICI = [
    pathlib.Path(os.path.expanduser("~/.local/state/metnos")),
    pathlib.Path(os.path.expanduser("~/.local/share/metnos")),
    pathlib.Path(os.path.expanduser("~/.config/metnos")),
    pathlib.Path("/opt/metnos"),
]
print("\n== RADICI ESAMINATE ==")
for r in RADICI: print("  ", r, "(esiste)" if r.exists() else "(ASSENTE)")

trovati = []

def esamina_sqlite(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tabelle = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    except Exception as e:
        return [("errore-apertura", str(e)[:60])]
    esiti = []
    for t in tabelle:
        try:
            cols = [d[1] for d in c.execute(f"PRAGMA table_info('{t}')")]
            for row in c.execute(f"select * from '{t}'"):
                for col, val in zip(cols, row):
                    if isinstance(val, bytes):
                        try: val = val.decode("utf-8", "ignore")
                        except Exception: continue
                    if not isinstance(val, str): continue
                    for needle in tutti:
                        if needle and needle in val:
                            esiti.append((f"{t}.{col}", needle))
        except Exception:
            continue
    c.close()
    return esiti

print("\n== ARCHIVI SQLITE INTERROGATI ==")
for r in RADICI:
    if not r.exists(): continue
    for p in sorted(r.rglob("*")):
        if p.is_file() and p.suffix in (".sqlite", ".db", ".sqlite3"):
            if BIRTH in p.parents: continue
            esiti = esamina_sqlite(p)
            marca = f"{len(esiti)} riscontri" if esiti else "nessun riscontro"
            print(f"  {str(p):70} {marca}")
            for e in esiti[:5]:
                print(f"      -> {e[0]}  contiene {e[1][:24]}…")
                trovati.append((str(p), e[0], e[1]))

print("\n== FILE DI TESTO/JSON CHE NOMINANO GLI IDENTIFICATIVI ==")
esclusi = {".git", "__pycache__", "node_modules", ".venv"}
n_scan = 0
for r in RADICI:
    if not r.exists(): continue
    for p in r.rglob("*"):
        if any(x in p.parts for x in esclusi): continue
        if BIRTH in p.parents or p == BIRTH: continue
        if not p.is_file() or p.is_symlink(): continue
        try:
            if p.stat().st_size > 8_000_000: continue
            raw = p.read_bytes()
        except Exception: continue
        n_scan += 1
        for needle in tutti:
            if needle.encode() in raw:
                print(f"  {p}  contiene {needle[:24]}…")
                trovati.append((str(p), "testo", needle))
                break
print(f"  (file letti: {n_scan})")

print("\n== ESITO ==")
if trovati:
    print(f"  {len(trovati)} legami trovati fuori dalla radice di nascita.")
    print("  I5 (\"rifare l'insieme costa poco\") NON regge senza esaminarli uno per uno.")
else:
    print("  Nessun legame fuori dalla radice di nascita.")
    print("  I5 regge PER LE RADICI ESAMINATE E GLI IDENTIFICATIVI ELENCATI, non in generale.")
