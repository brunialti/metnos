"""Universal §7.9 field synonym fallback for placeholder resolution.

Quando un placeholder `${stepN.entries.M.<field>}` referenza un field che
non esiste nell'entry, prova synonim noti (image_path → path, etc).
Coerente con vocab compositivo §2.2: campi tecnici hanno spesso varianti
provider-specifiche (path vs file_path vs image_path).

Usato da runtime/engine/executor.py (resolver placeholder/from_step). Il vecchio
runtime/_legacy/praxis_executor.py è stato rimosso con il planner legacy.
"""

# Synonim chiusi §7.3. Estendere SOLO con varianti documentate da executor
# manifest realmente in catalog.
FIELD_SYNONYMS = {
    "image_path": ["path", "file_path", "image_url", "url"],
    "image_url":  ["url", "image_path", "web_view_url", "path"],
    "file_path":  ["path", "file_id", "url"],
    "url":        ["web_view_url", "image_url", "file_path", "path", "link"],
    "web_url":    ["web_view_url", "url", "link"],
    "name":       ["title", "summary", "label", "basename"],
    "title":      ["name", "summary", "subject"],
    "summary":    ["title", "subject", "name", "description"],
    "subject":    ["title", "summary", "name"],
    "id":         ["file_id", "doc_id", "spreadsheet_id", "uid"],
    "content":    ["body", "text", "description", "summary"],
    "body":       ["content", "text", "description"],
    "text":       ["content", "body"],
    "date":       ["start", "created_at", "modified_at", "ts"],
    "start":      ["date", "start_time", "begin"],
    "end":        ["end_time", "until", "finish"],
    "basename":   ["name", "filename"],
    "filename":   ["basename", "name"],
    # ADR 0141 (provider github): le entry issue/pull portano SIA `number`
    # SIA `issue_number` per contratto; gli store (github_issue_qa) hanno la
    # colonna `issue_number` (bug live 6/7: write_entries key=["number"] ->
    # «no such column»).
    "number":       ["issue_number", "pull_number", "id"],
    "issue_number": ["number", "id"],
}


def resolve_dotted_with_synonyms(obj, path: str, base_resolver):
    """Wrapper §7.9: prova path con base_resolver; se None, prova synonim
    dell'ultimo segment.

    Args:
      obj: target dict/list
      path: dotted path (es. "entries.0.image_path")
      base_resolver: callable(obj, path) -> value | None
    """
    direct = base_resolver(obj, path)
    if direct is not None:
        return direct
    parts = path.split(".")
    last = parts[-1]
    if last not in FIELD_SYNONYMS:
        return None
    prefix = ".".join(parts[:-1])
    for syn in FIELD_SYNONYMS[last]:
        syn_path = f"{prefix}.{syn}" if prefix else syn
        v = base_resolver(obj, syn_path)
        if v is not None:
            return v
    return None
