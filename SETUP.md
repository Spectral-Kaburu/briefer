# SETUP: run the autonomous build (one-time, ~10 minutes)

Everything the agent needs is in this folder. Do these steps once, then start it and leave.

## 1. Put the kit in a fresh project folder

```bash
mkdir -p ~/projects/briefing
cp -r briefing-agent-kit/. ~/projects/briefing/
cd ~/projects/briefing
git init
git config user.name "Lee"
git config user.email "you@example.com"
python3 -m venv .venv
chmod +x run.sh
```

The `.claude/` folder (permissions, hooks, subagents) comes along because of the `.` in `cp -r briefing-agent-kit/.`.

## 2. Install the skills (once, inside Claude Code)

```
/plugin install superpowers@claude-plugins-official
```

Optional, only needed for the optional Phase 7 tasks:

```
/plugin marketplace add anthropics/skills
/plugin install example-skills@anthropic-agent-skills
```

Install **only one** of `document-skills` / `example-skills` (they ship identical skills, which duplicates context).

The plan names skills at each step. If one isn't installed the agent skips it and notes that in `PROGRESS.md`.

## 3. Keys and Docker

- Make sure local Firecrawl is running: `docker ps` should show it, and `curl -s http://localhost:3002/` should answer.
- **Do not put `FIRECRAWL_API_KEY` or a Gemini key in the agent's environment.** The agent is told to mock everything, and `~/.config/briefing/` is denied to it. You add keys yourself after the build.

## 4. Pick how to run it

**Recommended: `./run.sh`** (interactive session in `dontAsk` mode, you can watch or leave):

```bash
./run.sh
```

Inside a `tmux` session so it survives closing the terminal:

```bash
tmux new -s briefing
./run.sh
# detach: Ctrl-b then d      reattach: tmux attach -t briefing
```

**Fully headless alternative** (no UI; the Stop hook keeps it going until the plan is done):

```bash
./run.sh headless
```

## 5. Why the agent won't get stuck on denied commands

| Mechanism | What it does |
|---|---|
| `defaultMode: dontAsk` | Anything not on the allowlist is **auto-denied instead of prompting**, so nothing ever waits for you. |
| Allow list | venv tools, `make`, `pytest`, `ruff`, git (local only), file tools, read-only `docker ps/logs`, `curl` to `localhost:3002` only, web fetch from a fixed set of doc domains. |
| Deny list | `sudo`, `git push`/remote/reset --hard, `rm -*`, `wget`, `ssh`, docker lifecycle commands, `voice-tts`, secrets paths, and editing its own settings/hooks. Deny always beats allow. |
| `CLAUDE.md` rules | Use simple one-command calls (compound commands like `a && b` need a rule per part), use `.venv/bin/...`, and on a denial: log it in `DENIED.md` and move on, don't retry variants. |
| `Stop` hook | If the agent stops while required tasks remain, it is told to continue. It lets go when the plan is done, when `STOP_OK` exists, or after 40 consecutive nudges (safety valve). |
| `SessionStart` hook | After startup, resume or context compaction, re-injects next tasks + last progress notes so a long run doesn't lose its place. |
| `reviewer` / `verifier` subagents | Read-only, fresh context: the work isn't graded by itself. |

When you come back, check `DENIED.md`. Each line is a command the agent wanted but couldn't run. If a command is harmless, add it to `permissions.allow` in `.claude/settings.json` and resume.

## 6. When you return

1. `git log --oneline` for the history, `PROGRESS.md` for what was done, `DECISIONS.md` for judgment calls, `BLOCKED.md` for stuck items.
2. `.venv/bin/pytest` then `BRIEFING_LIVE=1 .venv/bin/pytest -m live` to run the live smoke tests yourself.
3. Add your keys (`export FIRECRAWL_API_KEY=...`, Gemini key) and try `briefing status`, then `briefing -r "latest breaking news in kenya"`.
4. Test `voice-tts` delivery yourself with `briefing run --dry-run`, then for real.

## Notes and limits

- The `dontAsk` mode and the allow/deny syntax follow current Claude Code docs. If your installed version rejects a rule, `/permissions` shows which one and why.
- `auto` mode would handle unforeseen commands better but is not honored from a project's `settings.json`; set it in `~/.claude/settings.json` if you prefer it, and keep the deny list.
- Your Claude plan's usage limits apply. A full-day build may hit them; re-run `./run.sh` and it resumes from `PLAN.md` / `PROGRESS.md`.
- `git add -A` in the loop commits everything not ignored. `.gitignore` is created in task T0.1, so make sure `.venv/` and `.env*` are in it (the plan covers this).
