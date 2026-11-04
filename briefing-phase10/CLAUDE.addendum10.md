
## Phase 10 rules (added 2026-11-04)

- Phases 10 and 11 in `PLAN.md` are the current work, after Phase 9's T9.8 and T9.9 at minimum. Start with T10.0, and start Phase 11 only after Phase 10 is ticked.
- **Knowledge adapters never raise.** Every adapter under `knowledge/` returns `[]` on any failure (connect error, timeout, non-2xx, 429, bad JSON, unexpected shape) and logs at DEBUG. They have a timeout, and they honour a quota floor or a `backoff` field.
- **API text is untrusted data**, exactly like page text: it goes inside the delimited prompt block with the closing delimiter stripped, HTML is reduced to plain text, control characters and ANSI escapes are removed, it is rendered with `Text` and never as Rich markup, and a URL from a response is accepted only on the expected `https` host.
- **No email address, real name or key in any request header, URL, log line, fixture, doc or `--json` output.** The `User-Agent` is the generic `briefing/<version>` unless the owner sets `knowledge.user_agent`. The optional Stack Exchange key is read from its env var at call time and goes through the `SecretFilter`.
- CC BY-SA passages carry their license and, for Stack Overflow, their author, in the source list and in `--json`.
- `-r` and briefing mode must not change. Phase 10 touches ask mode only, and a test pins that.
- The three new hosts (`en.wikipedia.org`, `api.stackexchange.com`, `api.duckduckgo.com`) are the owner's to allow. If one is refused, log it in `DENIED.md`, put the exact command in `BLOCKED.md`, and do not escalate.
- No new dependency: `httpx` and the stdlib are enough.

## Phase 11 rules (added 2026-11-04)

- Phase 11 is an audit, not a feature phase. Use the project skills `ruthless-audit`, `ruthless-review`, `fix-loop` and `mutation-sweep` (in `.claude/skills/`) and the read-only `slice-auditor` agent, and read a skill's `SKILL.md` before using it.
- **A finding without `file:line` evidence and a quoted line is not a finding.** The orchestrator re-opens every cited line; a quote that does not match is discarded.
- **Auditors and reviewers never edit.** Only `fix-loop` changes code, one finding at a time, and every fix is seen red, green, and red again under sabotage before it counts.
- **Never make a test pass by weakening it**: no deleting or loosening an assertion, no new `skip`, `xfail`, `noqa` or `type: ignore`, no exemption in `tests/conftest.py`. Fix the code, or fix the test to assert more.
- **Nothing is "clean".** A round that finds nothing says "no findings in round N under these lenses, scope covered: ...".
- A fix that needs an owner decision (a behaviour or flag change, a dependency, an earlier phase's owner's-call item) goes to `BLOCKED.md` with the exact question and is not decided by the agent.
- The ledger is `AUDIT.md` at the repo root. Keep it current after every fix; it is how a returning owner sees what happened.
