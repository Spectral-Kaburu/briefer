# DENIED

Commands the sandbox denied, in `command — why it was needed` form.

## 2026-10-27

- `git -C /home/spectre/Documents/morning add -A` — T0.0 wanted to stage the four
  new note files for the first commit. Denied because the `-C <path>` prefix stops
  the command from matching the `Bash(git add *)` allowlist rule. Not re-tried as a
  variant; re-run in the permitted bare form from the repo root instead (the
  allowlist clearly intends `git add` to be allowed). **Fix if you want the `-C`
  form**: add `Bash(git -C *)` to `permissions.allow` in `.claude/settings.json`.

