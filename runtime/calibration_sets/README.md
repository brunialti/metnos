# Planner-split calibration data

This directory contains JSON data for `runtime/calibration_check.py`, a
deterministic three-level threshold lookup:

1. a user override in
   `~/.config/metnos/planner_split_calibration.json`;
2. a language file in this directory;
3. conservative in-memory defaults.

`ensure_calibration(lang)` always returns a complete dictionary and
`threshold_for(calibration, verb)` selects a verb-specific threshold when one
exists.

The current request engine does **not** call this module in its production
planning path. The files are retained as calibration data for tooling and
experiments; changing them does not change normal Metnos routing.

## Files

- `_schema.json`: JSON Schema for a calibration set.
- `it.json`, `en.json`: bundled sample sets.

An override must use the requested ISO language code. Malformed or mismatched
files are ignored and the lookup falls back safely. The `generated_at`, commit,
model, and corpus fields are descriptive metadata; the current implementation
does not use them to invalidate a set automatically.
