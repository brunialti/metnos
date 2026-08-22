from __future__ import annotations

from compound_decomposer import detect_chunk_action, split_query_chunks


def test_transform_predicate_domain_words_do_not_replace_entry_carrier():
    assert detect_chunk_action(
        "filtra i risultati mantenendo soltanto quelli con un indirizzo email"
    ) == ("filter", "entries")


def test_date_comma_does_not_detach_provider_from_create_clause():
    query = (
        "Create an event on January 15, 2030 in Google Calendar, "
        "then remove it"
    )
    chunks = split_query_chunks(query)
    assert chunks == [
        "Create an event on January 15, 2030 in Google Calendar",
        "remove it",
    ]
    assert detect_chunk_action(
        "filter results to keep only entries with an email address"
    ) == ("filter", "entries")
