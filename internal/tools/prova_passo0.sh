#!/usr/bin/env bash
# Targeted tests for step 0: the green branch and every red branch.
#
# Everything runs on throwaway git repositories and an injected stack probe, so
# no test ever stops, starts or inspects the real stack.
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/passo0_punto_di_partenza.sh"
FALLIMENTI=0

verifica() {  # $1 nome  $2 uscita attesa  $3 frammento atteso  (resto: comando)
  local nome="$1" atteso="$2" frammento="$3"; shift 3
  local out rc
  out=$("$@" 2>&1); rc=$?
  local errori=""
  [ "$rc" -eq "$atteso" ] || errori="uscita $rc, attesa $atteso"
  case "$out" in
    *"$frammento"*) ;;
    *) errori="$errori; manca '$frammento' (uscita: $out)" ;;
  esac
  if [ -n "$errori" ]; then
    echo "ROSSO  $nome  -> $errori"; FALLIMENTI=$((FALLIMENTI+1))
  else
    echo "verde  $nome"
  fi
}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/prova-passo0-XXXXXXXX")
trap 'rm -rf "$TMP"' EXIT
STUB_ATTIVO="$TMP/attivo.sh";   printf '#!/bin/sh\necho active\n'   > "$STUB_ATTIVO";   chmod +x "$STUB_ATTIVO"
STUB_SPENTO="$TMP/spento.sh";   printf '#!/bin/sh\necho inactive\n' > "$STUB_SPENTO";   chmod +x "$STUB_SPENTO"

# A fixture repository with one commit and one tag.
crea_repo() {  # $1 = directory
  local d="$1"
  mkdir -p "$d" && git -C "$d" init -q
  git -C "$d" config user.email prova@metnos && git -C "$d" config user.name prova
  echo x > "$d/file.txt" && git -C "$d" add file.txt
  git -C "$d" commit -qm base
  git -C "$d" tag punto-di-ritorno
  git -C "$d" rev-parse --short=8 HEAD
}

REPO="$TMP/repo"; TESTA=$(crea_repo "$REPO")

# --- verde ---
verifica "verde: tutto come atteso" 0 "PASSO 0 VERDE" \
  env PASSO0_TESTA_ATTESA="$TESTA" PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$REPO"

# --- 2: non e' un repository ---
verifica "rosso 2: percorso inesistente" 2 "non e' un repository git" \
  env PASSO0_TESTA_ATTESA="$TESTA" PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$TMP/non-esiste"

# --- 3: radice diversa (una sottodirectory del repository) ---
mkdir -p "$REPO/sotto/.git"   # ha un .git ma non e' la radice del lavoro
verifica "rosso 3: radice del repository diversa" 3 "la radice del repository e'" \
  env PASSO0_TESTA_ATTESA="$TESTA" PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$REPO/sotto"
rm -rf "$REPO/sotto"

# --- 4: albero sporco ---
SPORCO="$TMP/sporco"; TESTA_S=$(crea_repo "$SPORCO"); echo modificato > "$SPORCO/file.txt"
verifica "rosso 4: albero sporco" 4 "file non committati" \
  env PASSO0_TESTA_ATTESA="$TESTA_S" PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$SPORCO"

# --- 5: testa inattesa ---
verifica "rosso 5: testa inattesa" 5 "attesa 00000000" \
  env PASSO0_TESTA_ATTESA=00000000 PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$REPO"

# --- 6: tag assente ---
verifica "rosso 6: tag assente" 6 "manca il tag" \
  env PASSO0_TESTA_ATTESA="$TESTA" PASSO0_TAG_ATTESO=tag-che-non-esiste \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$REPO"

# --- 7: stack non attivo (sonda iniettata, la vera non viene toccata) ---
verifica "rosso 7: stack non attivo (sonda iniettata)" 7 "non 'active'" \
  env PASSO0_TESTA_ATTESA="$TESTA" PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_SPENTO" "$SCRIPT" "$REPO"

# --- il falso verde storico: git che fallisce dentro una pipeline ---
ROTTO="$TMP/rotto"; mkdir -p "$ROTTO/.git"   # .git c'e' ma non e' un repository valido
verifica "rosso: .git presente ma repository non valido" 128 "" \
  env PASSO0_TESTA_ATTESA="$TESTA" PASSO0_TAG_ATTESO=punto-di-ritorno \
      PASSO0_STATO_CMD="$STUB_ATTIVO" "$SCRIPT" "$ROTTO"

echo
if [ "$FALLIMENTI" -eq 0 ]; then echo "ESITO: tutte verdi"; else echo "ESITO: $FALLIMENTI PROVE ROSSE"; fi
exit $([ "$FALLIMENTI" -eq 0 ] && echo 0 || echo 1)
