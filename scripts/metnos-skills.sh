#!/usr/bin/env bash
# metnos-skills — CLI entrypoint per l'importer skill agentskills.io
# (ADR 0123). Wrapper bash che chiama `python3 -m runtime.cli.skills_cli`
# col PYTHONPATH giusto per Metnos.
#
# Uso:
#   metnos-skills import <url_or_path> [--skip-l2] [--skip-l6] [--no-sign]
#   metnos-skills list
#   metnos-skills uninstall <skill_name>
#   metnos-skills status <skill_name>
#   metnos-skills evaluate <skill_name>
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-}:/opt/metnos"
exec /opt/suprastructure/.venv/bin/python -m runtime.cli.skills_cli "$@"
