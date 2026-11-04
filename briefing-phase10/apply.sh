#!/usr/bin/env bash
# Append Phases 10 and 11 to the briefing project's PLAN.md and CLAUDE.md, and install the
# audit skills and agent into .claude/. Safe to run twice.
# Usage: ./apply.sh /path/to/briefing-project     (default: current directory)
#        FORCE=1 ./apply.sh ...                    overwrite skills/agents that differ
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
cd "${1:-.}"

for f in PLAN.md CLAUDE.md; do
  [ -f "$f" ] || { echo "error: $f not found. Run this against the project root." >&2; exit 1; }
done

if grep -q '^## Phase 10' PLAN.md; then
  echo "PLAN.md: Phase 10 already present, skipped"
else
  cat "$here/PLAN.phase10.md" >> PLAN.md
  echo "PLAN.md: Phases 10 and 11 appended"
fi

if grep -q '^## Phase 10 rules' CLAUDE.md; then
  echo "CLAUDE.md: Phase 10 rules already present, skipped"
else
  cat "$here/CLAUDE.addendum10.md" >> CLAUDE.md
  echo "CLAUDE.md: Phase 10 and 11 rules appended"
fi

# install_tree SRC DEST: copy a file or directory unless it exists and differs (FORCE=1 overrides)
install_tree() {
  local src="$1" dest="$2" name
  name="${dest#.claude/}"
  if [ -e "$dest" ]; then
    if diff -rq "$src" "$dest" >/dev/null 2>&1; then
      echo ".claude/$name: up to date"; return
    elif [ "${FORCE:-0}" != "1" ]; then
      echo ".claude/$name: exists and differs, left alone (FORCE=1 to overwrite)"; return
    fi
    rm -rf "$dest"
  fi
  mkdir -p "$(dirname "$dest")"
  cp -R "$src" "$dest"
  echo ".claude/$name: installed"
}

for d in "$here"/skills/*/; do
  install_tree "${d%/}" ".claude/skills/$(basename "$d")"
done
for f in "$here"/agents/*.md; do
  install_tree "$f" ".claude/agents/$(basename "$f")"
done

rm -f STOP_OK .claude/.stop_gate_count
echo "Done. Owner step: allow en.wikipedia.org, api.stackexchange.com and api.duckduckgo.com in the sandbox settings before T10.0."
echo "Review with: git diff --stat   then start the agent with ./run.sh"
