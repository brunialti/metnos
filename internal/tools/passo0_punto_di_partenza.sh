#!/usr/bin/env bash
# Step 0 of the RM-0008 procedure: refuse to start from an unexpected state.
#
# The first version of this check lived in the document as a snippet, and the
# evidence quoted for it could not have come from the snippet itself: the
# snippet hard-coded the tree it inspected while the evidence passed one as an
# argument.  A check whose negative branch cannot be reproduced is not a check.
#
# Every expectation is therefore a parameter, and the stack probe is injectable,
# so the red branches can be exercised on fixtures without ever stopping the
# real stack.
#
#   uso:  passo0_punto_di_partenza.sh [ALBERO]
#
#   PASSO0_TESTA_ATTESA   testa corta attesa           (difetto: 14ea7179)
#   PASSO0_TAG_ATTESO     tag del punto di ritorno     (difetto: pre-fusione-31-8-2026)
#   PASSO0_STATO_CMD      comando che stampa lo stato  (difetto: systemctl --user is-active metnos.target)
#
# uscite:
#   0 verde     3 radice diversa    5 testa inattesa   7 stack non attivo
#   2 non e' un repository          4 albero sporco    6 tag assente
set -euo pipefail

ALBERO="${1:-/opt/metnos}"
TESTA_ATTESA="${PASSO0_TESTA_ATTESA:-14ea7179}"
TAG_ATTESO="${PASSO0_TAG_ATTESO:-pre-fusione-31-8-2026}"
STATO_CMD="${PASSO0_STATO_CMD:-systemctl --user is-active metnos.target}"

fermo() { echo "FERMO: $2" >&2; exit "$1"; }

[ -d "$ALBERO/.git" ] || fermo 2 "$ALBERO non e' un repository git"

radice=$(git -C "$ALBERO" rev-parse --show-toplevel)
[ "$radice" = "$ALBERO" ] || fermo 3 "la radice del repository e' $radice, non $ALBERO"

# The pipeline is inside the guarded block, so a failing git is a failing check
# and never a reassuring zero.
sporchi=$(git -C "$ALBERO" status --porcelain | wc -l)
[ "$sporchi" -eq 0 ] || fermo 4 "$sporchi file non committati: mettili al riparo prima"

testa=$(git -C "$ALBERO" rev-parse --short=8 HEAD)
[ "$testa" = "$TESTA_ATTESA" ] || fermo 5 "testa $testa, attesa $TESTA_ATTESA"

git -C "$ALBERO" rev-parse -q --verify "refs/tags/$TAG_ATTESO" >/dev/null \
  || fermo 6 "manca il tag $TAG_ATTESO"

stato=$($STATO_CMD 2>/dev/null || true)
[ "$stato" = "active" ] || fermo 7 "lo stack e' '$stato', non 'active'"

echo "PASSO 0 VERDE — $ALBERO a $testa, albero pulito, tag $TAG_ATTESO, stack attivo"
