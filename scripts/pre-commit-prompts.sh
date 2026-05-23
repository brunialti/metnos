#!/bin/bash
# Pre-commit hook (ADR 0092): lint sintassi MiniJinja per ogni .j2 modificato
# in runtime/prompts/. Installabile via:
#     ln -s /opt/metnos/scripts/pre-commit-prompts.sh /opt/metnos/.git/hooks/pre-commit
# (oppure copiare il file).
set -e
cd /opt/metnos
files=$(git diff --cached --name-only --diff-filter=ACM | grep '^runtime/prompts/.*\.j2$' || true)
if [ -z "$files" ]; then exit 0; fi
/opt/suprastructure/.venv/bin/python - "$files" <<'PYEOF'
import sys
import minijinja
env = minijinja.Environment()
files = sys.argv[1].split()
n_err = 0
for f in files:
    try:
        env.add_template(f, open(f).read())
    except Exception as e:
        print(f'PROMPT LINT FAIL: {f}: {e}', file=sys.stderr)
        n_err += 1
if n_err:
    sys.exit(1)
print('prompt syntax OK')
PYEOF
