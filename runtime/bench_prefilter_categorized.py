"""bench_prefilter_categorized.py — bench prefilter su corpus sintetico categorizzato.

Corpus di 100+ query etichettate per intent type (time, file, mail, web,
processi, foto, location, shell, ecc.). Ground truth = nome dell'executor
che DOVREBBE essere top-1 (o nei top-3) per ciascuna query.

Confronta tre modalita' di ranking:
  1. token: prefilter attuale (rank_adaptive)
  2. embed: cosine MiniLM 384d su description
  3. hybrid: 0.4*token_score + 0.6*embed_cosine

Output: precision per categoria + per modalita', + lista regressioni.

Run:
  /opt/suprastructure/.venv/bin/python /opt/myclaw/runtime/bench_prefilter_categorized.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, "/opt/myclaw/runtime")
sys.path.insert(0, "/opt/suprastructure/src")

import numpy as np  # noqa: E402

from loader import load_catalog  # noqa: E402
from prefilter import rank_adaptive  # noqa: E402
from suprastructure.embedding.onnx_embedding import EmbeddingService  # noqa: E402

MODEL_DIR = "/opt/myclaw/models/embedding"
KS = (3, 5, 8)


# ── Corpus categorizzato ────────────────────────────────────────────────
# Ogni entry: (categoria, query, expected_top_executor_or_set)

CORPUS: list[tuple[str, str, set[str]]] = [
    # ── TIME/DATE (8) ─────────────────────────────────────────────────
    ("time", "che ore sono", {"get_now"}),
    ("time", "che ora è", {"get_now"}),
    ("time", "what time is it", {"get_now"}),
    ("time", "data corrente", {"get_now"}),
    ("time", "che giorno è oggi", {"get_now"}),
    ("time", "current timestamp", {"get_now"}),
    ("time", "in che fuso orario sono", {"get_now"}),
    ("time", "dimmi l'ora attuale", {"get_now"}),

    # ── FILE FIND (10) ────────────────────────────────────────────────
    ("file_find", "trova tutte le foto in /home/roberto/images", {"find_files"}),
    ("file_find", "cerca file pdf in ~/Documents", {"find_files"}),
    ("file_find", "find all *.py files in /opt/myclaw", {"find_files"}),
    ("file_find", "trova README.md in opt/myclaw", {"find_files"}),
    ("file_find", "lista i file .jpg sul NAS", {"find_files"}),
    ("file_find", "search files matching *.log", {"find_files"}),
    ("file_find", "tutti i documenti pdf in Scaricati", {"find_files"}),
    ("file_find", "elencami i csv in /tmp", {"find_files"}),
    ("file_find", "find images recursively in pictures", {"find_files"}),
    ("file_find", "trova file con estensione .heic", {"find_files"}),

    # ── MAIL (10) ─────────────────────────────────────────────────────
    ("mail", "leggi le mail di oggi", {"read_messages"}),
    ("mail", "ultime email da Amazon", {"read_messages"}),
    ("mail", "mostrami i messaggi non letti", {"read_messages"}),
    ("mail", "show me emails from yesterday", {"read_messages"}),
    ("mail", "mail nella cartella Junk", {"read_messages"}),
    ("mail", "manda una mail a Lucia", {"send_messages"}),
    ("mail", "send email with subject test", {"send_messages"}),
    ("mail", "scrivi a metnos@metnos.com", {"send_messages"}),
    ("mail", "sposta nello spam le mail di amazon", {"move_messages"}),
    ("mail", "cancella le email di newsletter", {"delete_messages"}),

    # ── WEB SEARCH/CRAWL (8) ──────────────────────────────────────────
    ("web", "trova articoli su roma su repubblica.it", {"find_urls"}),
    ("web", "esplora il sito icsmargheritahack.edu.it", {"find_urls"}),
    ("web", "cerca PDF organico su scuola.edu.it", {"find_urls", "read_urls_pdf"}),
    ("web", "scarica il pdf da questo url", {"read_urls_pdf", "get_urls"}),
    ("web", "leggi il contenuto di https://example.com", {"read_urls_html", "get_urls"}),
    ("web", "elenca le pagine del blog X", {"find_urls"}),
    ("web", "fetch the content at https://news.example.org", {"get_urls", "read_urls_html"}),
    ("web", "what's on the homepage of repubblica.it", {"find_urls", "read_urls_html"}),

    # ── PROCESSI (10) ─────────────────────────────────────────────────
    ("process", "elenca i processi", {"get_processes"}),
    ("process", "lista i processi attivi", {"get_processes"}),
    ("process", "list running processes", {"get_processes"}),
    ("process", "i processi che usano più cpu", {"get_processes"}),
    ("process", "top 10 processi", {"get_processes"}),
    ("process", "show processes by memory usage", {"get_processes"}),
    ("process", "stato del server", {"get_processes"}),
    ("process", "salute sistema", {"get_processes"}),
    ("process", "system health", {"get_processes"}),
    ("process", "uptime e carico del server", {"get_processes"}),

    # ── FOTO/IMMAGINI (8) ─────────────────────────────────────────────
    ("photo", "trova foto simili a queste", {"find_images_indices"}),
    ("photo", "cerca le mie foto al mare", {"find_images_indices"}),
    ("photo", "find photos of family", {"find_images_indices"}),
    ("photo", "foto scattate a Roma", {"find_images_indices"}),
    ("photo", "indicizza le immagini in /home/roberto/images", {"create_images_indices"}),
    ("photo", "build the image index", {"create_images_indices"}),
    ("photo", "dimmi chi è in questa foto", {"find_images_indices"}),
    ("photo", "scene di mare nelle foto", {"find_images_indices"}),

    # ── LOCATION (6) ──────────────────────────────────────────────────
    ("location", "dove sono", {"get_location"}),
    ("location", "qual è la mia posizione", {"get_location"}),
    ("location", "where am i", {"get_location"}),
    ("location", "trova la farmacia più vicina", {"find_places"}),
    ("location", "ristoranti nelle vicinanze", {"find_places"}),
    ("location", "find restaurants near me", {"find_places"}),

    # ── SHELL/ADMIN (10) ──────────────────────────────────────────────
    ("admin", "monta il NAS condiviso", {"admin"}),
    ("admin", "smonta /mnt/nas", {"admin"}),
    ("admin", "kill il processo 1234", {"admin"}),
    ("admin", "termina firefox", {"admin"}),
    ("admin", "riavvia il servizio metnos-http", {"admin"}),
    ("admin", "systemctl restart nginx", {"admin"}),
    ("admin", "chmod 644 /etc/foo", {"admin"}),
    ("admin", "installa il pacchetto vim", {"admin"}),
    ("admin", "controlla journalctl per errori", {"admin"}),
    ("admin", "esegui un comando shell come root", {"admin"}),

    # ── COMPUTE (5) ───────────────────────────────────────────────────
    ("compute", "linee di codice di metnos", {"compute_files_loc"}),
    ("compute", "count lines of code in /opt/myclaw", {"compute_files_loc"}),
    ("compute", "calcola sha256 di /tmp/test.txt", {"compute_signatures"}),
    ("compute", "compute hash of file foo.py", {"compute_signatures"}),
    ("compute", "loc del progetto", {"compute_files_loc"}),

    # ── WRITE/MOVE (8) ────────────────────────────────────────────────
    ("write", "scrivi 'ciao' nel file /tmp/test.txt", {"write_files"}),
    ("write", "create file foo.txt with content bar", {"write_files"}),
    ("write", "sposta /tmp/x.txt in /tmp/archive/", {"move_files"}),
    ("write", "move all jpg from Downloads to Pictures", {"move_files"}),
    ("write", "cancella /tmp/test.txt", {"delete_files"}),
    ("write", "rimuovi i file vecchi", {"delete_files"}),
    ("write", "crea la cartella /tmp/foo", {"create_dirs"}),
    ("write", "create directory backup", {"create_dirs"}),

    # ── DESCRIBE/CLASSIFY (5) ─────────────────────────────────────────
    ("describe", "raggruppa per dominio i risultati", {"group_entries"}),
    ("describe", "filtra solo i pdf", {"filter_entries"}),
    ("describe", "ordina per data", {"sort_entries"}),
    ("describe", "classifica per categoria", {"classify_entries"}),
    ("describe", "descrivi questa lista in 3 righe", {"describe_entries"}),

    # ── UNDO (3) ──────────────────────────────────────────────────────
    ("undo", "annulla l'ultima operazione", {"undo_last_turn"}),
    ("undo", "undo", {"undo_last_turn"}),
    ("undo", "ripristina lo stato precedente", {"undo_last_turn"}),

    # ── EDGE CASES (10) ──────────────────────────────────────────────
    ("edge", "ciao", set()),  # nessun tool — saluto
    ("edge", "grazie", set()),
    ("edge", "ok", set()),
    ("edge", "che cosa puoi fare?", set()),  # meta-query
    ("edge", "aiutami con qualcosa", set()),
    ("edge", "non lo so", set()),
    ("edge", "un attimo", set()),
    ("edge", "perfetto", set()),
    ("edge", "test 123", set()),
    ("edge", "asd asd asd", set()),
]


def build_executor_text(e) -> str:
    """Testo da embeddare per ogni executor: name + description + affinity flat."""
    desc = (e.description or "").strip()
    aff = getattr(e, "affinity", None) or []
    if isinstance(aff, list):
        kws = " ".join(str(x) for x in aff)
    else:
        kws = ""
    return f"{e.name}: {desc} [{kws}]"


def main():
    print("loading catalog...", flush=True)
    cat = load_catalog(verify=True)
    execs = list(cat.executors.values())
    name_to_idx = {e.name: i for i, e in enumerate(execs)}
    print(f"  catalog: {len(execs)} executors")

    print("loading embedding model...", flush=True)
    t0 = time.perf_counter()
    emb = EmbeddingService(model_dir=MODEL_DIR)
    _ = emb.embed_query("warmup")
    print(f"  load+warmup: {(time.perf_counter()-t0)*1000:.0f} ms")

    print("embedding catalog...", flush=True)
    t0 = time.perf_counter()
    cat_texts = [build_executor_text(e) for e in execs]
    cat_emb = emb.embed_texts(cat_texts)
    print(f"  embed {len(execs)} executors: {(time.perf_counter()-t0)*1000:.0f} ms")

    # ── per categoria + globale ─────────────────────────────────────
    cat_names = set(name_to_idx)
    by_cat: dict[str, list[tuple[str, set[str]]]] = {}
    for category, q, gt in CORPUS:
        gt_in_cat = gt & cat_names if gt else set()
        by_cat.setdefault(category, []).append((q, gt_in_cat))

    # rank ogni query con tutte le 3 modalita'
    results = {
        "token": {k: {c: 0 for c in by_cat} for k in KS},
        "embed": {k: {c: 0 for c in by_cat} for k in KS},
        "hybrid": {k: {c: 0 for c in by_cat} for k in KS},
    }
    totals = {c: 0 for c in by_cat}
    failures = []  # (cat, query, gt, top5 per modalita')

    for category, queries in by_cat.items():
        for q, gt in queries:
            if not gt:
                # edge case: nessun tool richiesto. Skippa metric, solo
                # registra un check che il top-1 NON sia un tool d'azione.
                totals[category] += 1
                continue
            totals[category] += 1

            # token
            out_token, _ = rank_adaptive(q, cat, k_min=5, k_max=10)
            token_top = [e.name for e in out_token]

            # embed (cosine on description+affinity text)
            qv = emb.embed_query(q)
            scores_emb = cat_emb @ qv
            order_emb = np.argsort(-scores_emb)[:max(KS)]
            emb_top = [execs[idx].name for idx in order_emb]

            # hybrid: combina i due ranking. Strategia semplice: per ogni
            # executor calcola hybrid_score = 0.4*norm(token_rank_score)
            # + 0.6*norm(embed_score). Poi argsort.
            token_rank_inv = {n: 1.0 - (i / len(token_top))
                              for i, n in enumerate(token_top)}
            hybrid_scores = []
            for j, e in enumerate(execs):
                t = token_rank_inv.get(e.name, 0.0)
                em = float(scores_emb[j])
                hybrid_scores.append((0.4 * t + 0.6 * em, e.name))
            hybrid_top = [n for _, n in sorted(hybrid_scores, reverse=True)[:max(KS)]]

            for k in KS:
                if gt & set(token_top[:k]):
                    results["token"][k][category] += 1
                if gt & set(emb_top[:k]):
                    results["embed"][k][category] += 1
                if gt & set(hybrid_top[:k]):
                    results["hybrid"][k][category] += 1

            # registra fallimenti a K=3 per debug
            if not (gt & set(token_top[:3])) and not (gt & set(emb_top[:3])):
                failures.append({
                    "category": category, "query": q, "gt": sorted(gt),
                    "token3": token_top[:3], "emb3": emb_top[:3],
                    "hybrid3": hybrid_top[:3],
                })

    # ── stampa risultati ────────────────────────────────────────────
    print()
    print("═" * 75)
    print(f"BENCH categorizzato — {sum(totals.values())} query · {len(execs)} executor")
    print("═" * 75)
    print()
    print(f"{'categoria':<14} {'#':<4}  {'token@3':>9} {'embed@3':>9} {'hybrid@3':>10}")
    for c in sorted(by_cat.keys()):
        n_with_gt = sum(1 for _, gt in by_cat[c] if gt)
        if n_with_gt == 0:
            continue
        t3 = 100.0 * results["token"][3][c] / n_with_gt
        e3 = 100.0 * results["embed"][3][c] / n_with_gt
        h3 = 100.0 * results["hybrid"][3][c] / n_with_gt
        print(f"  {c:<12} {n_with_gt:<4}  {t3:>8.1f}% {e3:>8.1f}% {h3:>9.1f}%")

    n_total = sum(1 for _, _, gt in CORPUS if gt)
    print()
    print(f"AGGREGATO ({n_total} query con GT):")
    for k in KS:
        t = sum(results["token"][k].values())
        e = sum(results["embed"][k].values())
        h = sum(results["hybrid"][k].values())
        print(f"  @{k}  token {100*t/n_total:5.1f}%  embed {100*e/n_total:5.1f}%  hybrid {100*h/n_total:5.1f}%")

    print()
    print(f"─── fallimenti totali (no hit a K=3 in nessuna modalita'): {len(failures)} ───")
    for f in failures[:10]:
        print(f"\n  [{f['category']}] q: {f['query']}")
        print(f"     GT: {f['gt']}")
        print(f"   token: {f['token3']}")
        print(f"    emb : {f['emb3']}")
        print(f"  hybrid: {f['hybrid3']}")

    out = {
        "n_executors": len(execs),
        "n_queries": sum(totals.values()),
        "n_with_gt": n_total,
        "by_category": {
            c: {
                "n": len(by_cat[c]),
                "n_with_gt": sum(1 for _, gt in by_cat[c] if gt),
                "scores": {
                    mode: {str(k): results[mode][k][c] for k in KS}
                    for mode in ("token", "embed", "hybrid")
                },
            } for c in by_cat
        },
        "failures": failures,
    }
    Path("/opt/myclaw/runtime/bench_prefilter_categorized.result.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )
    print("\n  → JSON → /opt/myclaw/runtime/bench_prefilter_categorized.result.json")


if __name__ == "__main__":
    main()
