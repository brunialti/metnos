#!/usr/bin/env bash
# Metnos pre-commit — anti-drift IT/EN sui prompt .j2.
#
# Verifica: per ogni file modificato in `runtime/prompts/it/<role>.j2`, deve
# essere modificato/stagiato anche `runtime/prompts/en/<role>.j2` (e viceversa).
#
# Razionale: ADR 0092 multilang, drift documentato (planner.j2 695 IT vs 406
# EN). Un singolo lato in commit = silently breaking l'altra lingua.
#
# Bypass legittimo (NON --no-verify silente):
#   METNOS_LANG_DEFER=en  → committi solo IT, EN seguira' in commit dedicato
#   METNOS_LANG_DEFER=it  → simmetrico
# Lo script logga la deroga su stderr, l'utente sa cosa fa.

set -euo pipefail

DEFER="${METNOS_LANG_DEFER:-}"
# Cattura `runtime/prompts/{it,en}/<path>.j2` a qualunque profondita' (Fase C
# split: planner/_core.j2, planner/_footer.j2, planner/sections/*.j2). Il role
# canonico e' il path relativo dopo `<lang>/`, cosi' IT e EN si confrontano
# byte-per-byte sullo stesso path.
PROMPTS_RE='^runtime/prompts/(it|en)/(.+)\.j2$'

staged="$(git diff --cached --name-only --diff-filter=ACMRT 2>/dev/null || true)"
if [[ -z "$staged" ]]; then
    exit 0
fi

declare -A touched_it touched_en
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if [[ "$f" =~ $PROMPTS_RE ]]; then
        lang="${BASH_REMATCH[1]}"
        role="${f#runtime/prompts/$lang/}"
        if [[ "$lang" == "it" ]]; then
            touched_it["$role"]=1
        else
            touched_en["$role"]=1
        fi
    fi
done <<< "$staged"

missing_en=()
for role in "${!touched_it[@]}"; do
    if [[ -z "${touched_en[$role]:-}" ]]; then
        missing_en+=("$role")
    fi
done

missing_it=()
for role in "${!touched_en[@]}"; do
    if [[ -z "${touched_it[$role]:-}" ]]; then
        missing_it+=("$role")
    fi
done

# Bypass dichiarato: l'utente accetta consapevolmente che l'altra lingua
# rimanga indietro. Verra' raccolta in un commit successivo.
if [[ "$DEFER" == "en" && ${#missing_en[@]} -gt 0 && ${#missing_it[@]} -eq 0 ]]; then
    echo "metnos-pre-commit: deferral EN attivo (METNOS_LANG_DEFER=en)." >&2
    echo "  Role(s) IT-only accettati: ${missing_en[*]}" >&2
    exit 0
fi
if [[ "$DEFER" == "it" && ${#missing_it[@]} -gt 0 && ${#missing_en[@]} -eq 0 ]]; then
    echo "metnos-pre-commit: deferral IT attivo (METNOS_LANG_DEFER=it)." >&2
    echo "  Role(s) EN-only accettati: ${missing_it[*]}" >&2
    exit 0
fi

if [[ ${#missing_en[@]} -eq 0 && ${#missing_it[@]} -eq 0 ]]; then
    exit 0
fi

cat >&2 <<EOF
metnos-pre-commit: drift IT/EN bloccante.

Hai modificato uno solo dei due rami linguistici dei prompt .j2 (ADR 0092).
Tutti i .j2 devono restare simmetrici per ruolo fra runtime/prompts/it/ e
runtime/prompts/en/.

EOF
if [[ ${#missing_en[@]} -gt 0 ]]; then
    echo "  Mancano in EN (modificati solo in IT):" >&2
    for r in "${missing_en[@]}"; do
        echo "    runtime/prompts/en/$r" >&2
    done
fi
if [[ ${#missing_it[@]} -gt 0 ]]; then
    echo "  Mancano in IT (modificati solo in EN):" >&2
    for r in "${missing_it[@]}"; do
        echo "    runtime/prompts/it/$r" >&2
    done
fi

cat >&2 <<EOF

Come procedere (in ordine di preferenza):
  1. Allinea l'altra lingua nello stesso commit e ri-prova.
  2. Se l'aggiornamento dell'altra lingua e' INTENZIONALMENTE differito:
       METNOS_LANG_DEFER=en git commit ...   # IT ora, EN dopo
       METNOS_LANG_DEFER=it git commit ...   # EN ora, IT dopo
  3. SOLO in emergenza: git commit --no-verify (bypass silente, sconsigliato).

EOF
exit 1
