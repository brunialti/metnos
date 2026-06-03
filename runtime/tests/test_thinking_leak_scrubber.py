"""Test scrubber anti thinking-leak (ADR 0102, 7/5/2026).

Convergenza: il PLANNER (Gemma 4 26B think=true) a volte emette il
proprio reasoning interno nel canale `text` invece che nel canale
`thinking` separato. Lo scrubber rimuove le righe di reasoning leak
preservando il contenuto legittimo.
"""
from __future__ import annotations

import pytest

from runtime.agent_runtime import _scrub_thinking_leak, _scrub_raw_html_leak


def test_scrub_wait_actually_lines():
    """Rimuove righe standalone che iniziano con Wait, Actually."""
    text = (
        "Il primo URL contiene la mitologia di Metis.\n"
        "Wait, I'll check if I should use describe_entries to be more "
        "Metnos-like.\n"
        "Actually, I'll respond directly to the user.\n"
        "Il secondo URL non e' raggiungibile."
    )
    out = _scrub_thinking_leak(text)
    assert "Wait, I'll check" not in out
    assert "Actually, I'll respond" not in out
    assert "Il primo URL contiene la mitologia di Metis." in out
    assert "Il secondo URL non e' raggiungibile." in out


def test_scrub_let_me_check():
    text = (
        "Risposta: la pagina parla di Metis.\n"
        "Let me check if I missed something.\n"
        "Hmm, the user asked for comparison."
    )
    out = _scrub_thinking_leak(text)
    assert "Let me check" not in out
    assert "Hmm," not in out
    assert "Risposta: la pagina parla di Metis." in out


def test_scrub_final_answer_marker():
    text = (
        "Final Answer construction:\n"
        "La pagina contiene la mitologia di Metis.\n"
        "Final Answer:"
    )
    out = _scrub_thinking_leak(text)
    assert "Final Answer construction" not in out
    assert "Final Answer:" not in out
    assert "La pagina contiene la mitologia di Metis." in out


def test_preserve_legitimate_substring():
    """Substring 'Wait' in mezzo a paragrafo legittimo e' preservata."""
    text = (
        "Il documento dice: 'Wait, this is important' nella sezione 3.\n"
        "Quindi la conclusione e' chiara."
    )
    out = _scrub_thinking_leak(text)
    # La riga inizia con "Il documento", non con "Wait". E' preservata.
    assert "'Wait, this is important'" in out
    assert "Quindi la conclusione e' chiara." in out


def test_preserve_legitimate_paragraph():
    text = (
        "Metis era una titanide della saggezza.\n\n"
        "Era figlia di Oceano e Teti, secondo Esiodo.\n"
        "Zeus la inghiotti per evitare la profezia."
    )
    out = _scrub_thinking_leak(text)
    assert "Metis era una titanide della saggezza." in out
    assert "Era figlia di Oceano e Teti" in out
    assert "Zeus la inghiotti" in out


def test_empty_input():
    assert _scrub_thinking_leak("") == ""
    assert _scrub_thinking_leak(None) is None


def test_non_string_input():
    assert _scrub_thinking_leak(42) == 42
    assert _scrub_thinking_leak({"x": 1}) == {"x": 1}


def test_idempotent():
    text = (
        "Wait, I should think.\n"
        "Risposta vera qui.\n"
        "Actually, let me reconsider."
    )
    once = _scrub_thinking_leak(text)
    twice = _scrub_thinking_leak(once)
    assert once == twice
    assert once == "Risposta vera qui."


def test_collapse_multi_blank_lines():
    text = (
        "Riga uno.\n"
        "Wait, thinking.\n"
        "Actually, more.\n"
        "Hmm, more thinking.\n"
        "Riga due."
    )
    out = _scrub_thinking_leak(text)
    # Niente catene di 3+ \n consecutivi nel risultato.
    assert "\n\n\n" not in out
    assert "Riga uno." in out and "Riga due." in out


def test_real_world_leak_from_bug_report():
    """Caso live 7/5 18:22 — turn Metis mythology comparison."""
    text = (
        "Per quanto riguarda https://en.wikipedia.org/wiki/Metis_(mythology), "
        "la pagina descrive Metis come titanide della saggezza.\n"
        "Actually, I'll check if I should use describe_entries to be more "
        "Metnos-like.\n"
        "Rule: \"DEVI: chiamare describe_entries ... oppure direttamente "
        "final_answer\".\n"
        "Given the user wants a \"comparison\", and one is dead, a direct "
        "answer is best.\n"
        "One detail: the user asked to \"compare\". I will explain that "
        "comparison is impossible.\n"
        "Wait, I'll check if I can use describe_entries to get a better "
        "summary.\n"
        "Actually, I'll"
    )
    out = _scrub_thinking_leak(text)
    assert "la pagina descrive Metis come titanide della saggezza." in out
    # Tutti i marker leak sono andati.
    for marker in ("Actually, I'll check", "Rule: \"DEVI",
                   "Given the user", "One detail:",
                   "Wait, I'll check", "Actually, I'll"):
        assert marker not in out, f"leak residuo: {marker!r}"


def test_now_ill_marker():
    text = "Now I'll proceed.\nLa risposta e' chiara."
    out = _scrub_thinking_leak(text)
    assert "Now I'll" not in out
    assert "La risposta e' chiara." in out


def test_so_the_answer_marker():
    text = "So, the answer is...\nIl risultato finale: 42."
    out = _scrub_thinking_leak(text)
    assert "So, the answer" not in out
    assert "Il risultato finale: 42." in out


# ─────────────────────────────────────────────────────────────────────────
# Pattern italiani (estensione 7/5/2026 — caso live federvolley Test 5).
# Patch chirurgica: rimuove SOLO righe ENTIRE-paren che chiedono permesso
# e righe standalone meta-permission. NON tocca "Riassumendo:", "Ti
# suggerisco" in mezzo a contenuto reale (possibili falsi positivi).
# ─────────────────────────────────────────────────────────────────────────


def test_it_paren_permission_standalone_removed():
    """Riga ENTIRE in parentesi che chiede permesso — caso federvolley."""
    text = (
        "Ho letto la pagina del torneo.\n"
        "(posso provare a cercare i PDF se mi dai il via libera)\n"
        "Risultati pubblicati: 3 partite."
    )
    out = _scrub_thinking_leak(text)
    assert "posso provare a cercare" not in out
    assert "Ho letto la pagina del torneo." in out
    assert "Risultati pubblicati: 3 partite." in out


def test_it_paren_se_vuoi_standalone_removed():
    text = (
        "Risposta principale qui.\n"
        "(se vuoi ti aggiungo le statistiche dettagliate)\n"
        "Dati totali: 42."
    )
    out = _scrub_thinking_leak(text)
    assert "se vuoi ti aggiungo" not in out
    assert "Dati totali: 42." in out


def test_it_paren_legitimate_aside_preserved():
    """Parentetica NON di permesso (citazione legit) resta — su riga propria."""
    text = (
        "I risultati sono i seguenti.\n"
        "(fonte: federvolley.it ufficiale)\n"
        "Squadra A: 3."
    )
    out = _scrub_thinking_leak(text)
    assert "(fonte: federvolley.it ufficiale)" in out


def test_it_se_vuoi_posso_standalone_removed():
    text = (
        "Ho recuperato i dati richiesti.\n"
        "se vuoi posso scaricare anche il PDF.\n"
        "Numero totale: 27."
    )
    out = _scrub_thinking_leak(text)
    assert "se vuoi posso scaricare" not in out
    assert "Numero totale: 27." in out


def test_it_fammi_sapere_se_standalone_removed():
    text = (
        "Dati estratti: 100.\n"
        "fammi sapere se serve altro.\n"
        "Fine."
    )
    out = _scrub_thinking_leak(text)
    assert "fammi sapere se serve" not in out
    assert "Dati estratti: 100." in out


def test_it_vuoi_che_lo_faccia_standalone_removed():
    text = (
        "Ecco la lista delle squadre.\n"
        "vuoi che lo faccia adesso?\n"
        "Riepilogo: 3 elementi."
    )
    out = _scrub_thinking_leak(text)
    assert "vuoi che lo faccia" not in out
    assert "Riepilogo: 3 elementi." in out


def test_it_riassumi_request_preserves_riassumendo():
    """Falso positivo: "Riassumendo: ..." e' legittimo come incipit
    di sintesi quando l'utente ha chiesto un riassunto. NON scrubare."""
    text = (
        "Riassumendo: il torneo ha 27 squadre divise in 3 gironi. "
        "I risultati della giornata 22 sono pubblicati."
    )
    out = _scrub_thinking_leak(text)
    assert "Riassumendo" in out
    assert "27 squadre" in out


def test_it_ti_suggerisco_real_content_preserved():
    """Falso positivo: "Ti suggerisco di guardare ..." con contenuto reale
    non e' meta-talk, e' un consiglio. NON scrubare."""
    text = (
        "Ti suggerisco di consultare la pagina ufficiale federvolley.it "
        "per il calendario aggiornato."
    )
    out = _scrub_thinking_leak(text)
    assert "Ti suggerisco" in out
    assert "federvolley" in out


def test_it_paren_in_middle_of_line_preserved():
    """Parentetica embedded NEL MEZZO di una riga (non standalone) →
    preservata. Pattern chirurgico per evitare di mutilare liste numerate
    o paragrafi reali."""
    text = (
        "I dati sono parziali (posso provare a cercarli se mi dai il via "
        "libera) ma utilizzabili comunque."
    )
    out = _scrub_thinking_leak(text)
    # La riga sopravvive intera perche' non e' standalone-paren.
    assert "I dati sono parziali" in out
    assert "ma utilizzabili comunque" in out


def test_it_idempotent_paren_permission():
    text = (
        "Ho letto la pagina.\n"
        "(posso provare a cercare i PDF se mi dai il via libera)\n"
        "Risultati pubblicati."
    )
    once = _scrub_thinking_leak(text)
    twice = _scrub_thinking_leak(once)
    assert once == twice


def test_it_idempotent_standalone():
    text = (
        "Tabella estratta.\n"
        "se vuoi posso esportarla in CSV.\n"
        "27 righe totali."
    )
    once = _scrub_thinking_leak(text)
    twice = _scrub_thinking_leak(once)
    assert once == twice


# ─────────────────────────────────────────────────────────────────────────
# Convergenza 5 query (3 base EN + Test 4 partial-fail IT + Test 5
# federvolley IT). Tutte devono avere leak_count==0 e contenuto utile.
# ─────────────────────────────────────────────────────────────────────────


def _assert_clean_and_useful(raw, must_contain, must_not_contain):
    out = _scrub_thinking_leak(raw)
    for s in must_contain:
        assert s in out, f"missing useful content: {s!r} in {out!r}"
    for s in must_not_contain:
        assert s not in out, f"leak still present: {s!r} in {out!r}"
    assert len(out.strip()) > 20


def test_convergence_q1_wikipedia_metis_en_leak():
    raw = (
        "Metis e' una figura della mitologia greca, prima moglie di Zeus.\n"
        "Wait, I should also mention she was a Titaness.\n"
        "Era considerata personificazione della saggezza."
    )
    _assert_clean_and_useful(
        raw,
        must_contain=["Metis", "mitologia greca", "saggezza"],
        must_not_contain=["Wait, I should"],
    )


def test_convergence_q2_python_org_en_leak():
    raw = (
        "La pagina /about/ descrive Python come linguaggio ad alto livello.\n"
        "Actually, let me check the version info too.\n"
        "Caratteristiche: dynamic typing, garbage collection."
    )
    _assert_clean_and_useful(
        raw,
        must_contain=["Python", "alto livello", "garbage collection"],
        must_not_contain=["Actually, let me"],
    )


def test_convergence_q3_example_com_en_leak():
    raw = (
        "Il sito example.com e' un dominio riservato per documentazione.\n"
        "I'll explain the RFC reference.\n"
        "RFC 2606 lo definisce come reserved TLD."
    )
    _assert_clean_and_useful(
        raw,
        must_contain=["example.com", "documentazione", "RFC 2606"],
        must_not_contain=["I'll explain"],
    )


def test_convergence_q4_partial_fail_it_leak():
    """Test 4 partial-fail: meta-permission IT standalone."""
    raw = (
        "Ho recuperato 2 risultati su 3 richiesti.\n"
        "se vuoi posso ritentare la terza chiamata.\n"
        "Riepilogo: A=10, B=20."
    )
    _assert_clean_and_useful(
        raw,
        must_contain=["2 risultati su 3", "Riepilogo", "A=10"],
        must_not_contain=["se vuoi posso ritentare"],
    )


def test_convergence_q5_federvolley_it_paren_leak():
    """Test 5 federvolley: meta-permission in parens standalone."""
    raw = (
        "I risultati del girone F della giornata 22 non sono ancora "
        "pubblicati sulla pagina HTML.\n"
        "(posso provare a cercarli nei PDF se mi dai il via libera)\n"
        "Pagina aggiornata al: 2026-05-06."
    )
    _assert_clean_and_useful(
        raw,
        must_contain=["girone F", "giornata 22", "2026-05-06"],
        must_not_contain=["posso provare a cercarli nei PDF"],
    )


# --- raw-HTML leak backstop (bug "azione schedulata invia messaggio errato") -

def test_raw_html_doctype_leak_scrubbed():
    """HTML document-level (<!DOCTYPE/<html) trapelato in un messaggio viene
    rimosso; resta solo il testo. Caso reale: task schedulato web-search."""
    text = (
        "Ricerca interrotta prima di una risposta diretta.\n"
        "- https://rocm.docs.amd.com/versions.html\n"
        '  <!DOCTYPE html> <html lang="en"><head><title>ROCm 6.2</title>'
        "</head><body>x</body></html>"
    )
    out = _scrub_raw_html_leak(text)
    assert "<!DOCTYPE" not in out and "<html" not in out
    assert "ROCm 6.2" in out          # il testo utile sopravvive
    assert "rocm.docs.amd.com" in out  # l'URL resta


def test_raw_html_script_style_removed():
    out = _scrub_raw_html_leak("ciao <script>alert(1)</script> mondo <html>")
    assert "alert(1)" not in out and "<script" not in out
    assert "ciao" in out and "mondo" in out


def test_legit_markdown_with_autolink_untouched():
    """Senza marker di documento, il backstop e' no-op: markdown e autolink
    `<url>` legittimi NON vengono toccati."""
    md = (
        "Versione **6.2**. Vedi <https://rocm.docs.amd.com/v.html> per i "
        "dettagli.\n- [Release notes](https://x/rel)"
    )
    assert _scrub_raw_html_leak(md) == md


def test_scrub_raw_html_idempotent():
    text = "Testo <body><p>ciao</p></body>"
    once = _scrub_raw_html_leak(text)
    assert _scrub_raw_html_leak(once) == once


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
