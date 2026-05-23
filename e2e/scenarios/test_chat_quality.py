"""E2E chat quality — parametrize da corpus.sqlite estratto dai turn log.

Esegue ogni query del corpus contro il server, assertando:
  - Strato A (lint deterministico): clean, no <missing:>, no Traceback,
    lang match, pipeline shape valida ADR 0154.
  - Strato B (LLM judge, default ON): coerenza semantica query/risposta
    via Gemma 4 26B locale + cache disk. Opt-out METNOS_E2E_LLM_JUDGE=0.

Categorie corpus testate:
  - positives: query con feedback ok / inferred success=1
  - negatives: query con feedback ✗ / failure → regression guard
  - retry: query rilanciate dopo failure → policy reject_pattern OK

NB SLOW: ogni query LLM call ~30-60s. Tot test ~10-15min per 12 query.
Default OFF (skip). Abilita con `METNOS_E2E_RUN_SLOW=1`.

Server isolato vs live: il server e2e tmp ha catalog ma storage vuoto.
Query che richiedono dati utente (credenziali google, turns history,
indici) timeout. Per quelle query usa server live via
`METNOS_E2E_USE_LIVE_SERVER=http://localhost:8770` (con admin_key da
~/.config/metnos/admin.key letta dal driver).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver import judge, lint
from driver.corpus import Corpus, QueryRow

# Chat quality e' SLOW: ogni query e' ~30-60s di LLM call. Default skip.
# Abilita esplicitamente con METNOS_E2E_RUN_SLOW=1 (es. nightly).
_RUN_SLOW = os.environ.get("METNOS_E2E_RUN_SLOW", "0") == "1"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _RUN_SLOW,
                        reason="chat quality e' slow (~15min). "
                               "Abilita con METNOS_E2E_RUN_SLOW=1"),
]


_CORPUS_PATH = Path(__file__).resolve().parent.parent / "corpus" / "corpus.sqlite"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    if not _CORPUS_PATH.exists():
        pytest.skip(
            f"corpus.sqlite missing: {_CORPUS_PATH}. "
            "Run `python3 e2e/corpus/extract.py` first."
        )
    return Corpus(_CORPUS_PATH)


def _id(row: QueryRow) -> str:
    return f"{row.category}/{row.lang}/{row.query[:30]}"


# --- Strato A: lint deterministico (sempre attivo) -------------------------

async def _assert_lint_clean(driver, query: QueryRow):
    r = await driver.chat(query.query, lang=query.lang)
    # Skip se planner ha timeout / network down (situazione test, non bug)
    if r.error and "unreachable" in (r.error or "").lower():
        pytest.skip(f"network/LLM unreachable: {r.error}")
    text = r.final_text or (r.final_html or "")
    lint_result = lint.check_response(
        text, expected_lang=query.lang, min_length=1,
    )
    if not lint_result.ok:
        pytest.fail(
            f"lint failed for «{query.query}»:\n{lint_result.fail_message()}\n"
            f"answer: {text[:300]}"
        )
    # Pipeline shape (ADR 0154)
    shape_result = lint.check_pipeline_shape(r.steps)
    if not shape_result.ok:
        pytest.fail(
            f"pipeline shape invalid for «{query.query}»:\n"
            f"{shape_result.fail_message()}"
        )
    return r, text


# --- Tests parametrizzati (corpus loaded at module level) ------------------

def _load_positives() -> list[QueryRow]:
    if not _CORPUS_PATH.exists():
        return []
    c = Corpus(_CORPUS_PATH)
    # 3 per categoria per non esplodere il tempo test
    out = []
    for cat in ("fast_path_L0", "fast_path_L1", "planner"):
        out.extend(c.positives(category=cat, limit=3))
    return out


def _load_negatives() -> list[QueryRow]:
    if not _CORPUS_PATH.exists():
        return []
    return Corpus(_CORPUS_PATH).negatives(limit=3)


_POSITIVES = _load_positives()
_NEGATIVES = _load_negatives()


@pytest.mark.parametrize("query_row", _POSITIVES,
                          ids=[_id(q) for q in _POSITIVES] if _POSITIVES else [])
async def test_positive_lint_and_judge(driver, query_row: QueryRow):
    """Positive query: lint clean + judge ok (se enabled)."""
    if not _POSITIVES:
        pytest.skip("no positives in corpus")
    r, text = await _assert_lint_clean(driver, query_row)

    # Judge LLM (default ON)
    if judge.is_judge_enabled():
        verdict = await judge.evaluate(query_row.query, text, lang=query_row.lang)
        if not verdict.ok:
            pytest.fail(
                f"judge rejected positive «{query_row.query}»:\n"
                f"  score={verdict.score:.2f} reason={verdict.reason}\n"
                f"  answer: {text[:300]}"
            )


@pytest.mark.parametrize("query_row", _NEGATIVES,
                          ids=[_id(q) for q in _NEGATIVES] if _NEGATIVES else [])
async def test_negative_does_not_regress(driver, query_row: QueryRow):
    """Negative query (feedback ✗ storico): non deve loop_break ne'
    Traceback ne' <missing:>. Lint clean. Il judge puo' essere ok=False
    legittimamente (la query era 'sbagliata' come intent)."""
    if not _NEGATIVES:
        pytest.skip("no negatives in corpus")
    await _assert_lint_clean(driver, query_row)
