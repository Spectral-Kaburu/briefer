#!/usr/bin/env zsh
# Start the autonomous build. Usage: ./run.sh [headless]
set -euo pipefail
cd "$(dirname "$0")"

[ -d .git ] || git init -q
[ -d .venv ] || python3 -m venv .venv
rm -f STOP_OK .claude/.stop_gate_count

PROMPT="Start the autonomous build. Read CLAUDE.md, SPEC.md and PLAN
.md, then follow the loop in CLAUDE.md until no required tasks remai
n. Never ask me questions; record decisions in DECISIONS.md."

if [ "${1:-}" = "headless" ]; then
  exec claude-nvidia-bin -p "$PROMPT" --permission-mode dontAsk --ou
tput-format text
else
  exec claude-nvidia-bin --permission-mode dontAsk "$PROMPT"
fi