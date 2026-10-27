#!/usr/bin/env python3
"""SessionStart hook: re-orient the agent after startup, resume or context compaction.

Whatever this prints to stdout is injected into the session context.
"""
import os
from pathlib import Path

root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))


def tail(name: str, lines: int) -> str:
    p = root / name
    if not p.exists():
        return f"({name} not created yet)"
    return "\n".join(p.read_text(errors="replace").splitlines()[-lines:])


plan = root / "PLAN.md"
open_tasks = []
if plan.exists():
    for line in plan.read_text(errors="replace").splitlines():
        if line.startswith("- [ ]") and "(optional)" not in line:
            open_tasks.append(line)

print("## Briefing build: session context")
print("Read CLAUDE.md, SPEC.md and PLAN.md. Work autonomously; never ask the user.")
print()
print(f"Open required tasks: {len(open_tasks)}")
for t in open_tasks[:3]:
    print("  NEXT:" if t is open_tasks[0] else "  then:", t[:160])
print()
print("### Last progress notes")
print(tail("PROGRESS.md", 15))
print()
print("### Blocked / denied")
print(tail("BLOCKED.md", 5))
print(tail("DENIED.md", 5))
