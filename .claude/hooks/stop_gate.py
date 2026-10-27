#!/usr/bin/env python3
"""Stop hook: keep the agent working until PLAN.md has no open required tasks.

Allows stopping when:
  - no unchecked non-optional tasks remain, or
  - a STOP_OK file exists (agent declared everything left is blocked), or
  - this hook already blocked many times in a row (safety valve against loops).
Otherwise it blocks the stop and tells the agent to continue.
"""
import json
import os
import sys
from pathlib import Path

root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
counter = root / ".claude" / ".stop_gate_count"
MAX_CONSECUTIVE_BLOCKS = 40

try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}

plan = root / "PLAN.md"
if not plan.exists() or (root / "STOP_OK").exists():
    sys.exit(0)

open_tasks = [
    line
    for line in plan.read_text(errors="replace").splitlines()
    if line.startswith("- [ ]") and "(optional)" not in line
]

if not open_tasks:
    counter.unlink(missing_ok=True)
    sys.exit(0)

try:
    n = int(counter.read_text()) + 1
except Exception:
    n = 1
counter.write_text(str(n))

if n > MAX_CONSECUTIVE_BLOCKS:
    counter.unlink(missing_ok=True)
    sys.exit(0)

print(
    json.dumps(
        {
            "decision": "block",
            "reason": (
                f"{len(open_tasks)} required task(s) remain in PLAN.md. Next: "
                f"{open_tasks[0][:200]}. Continue the loop from CLAUDE.md. "
                "Do not ask the user anything. If every remaining task is blocked, "
                "write BLOCKED.md and create an empty STOP_OK file."
            ),
        }
    )
)
