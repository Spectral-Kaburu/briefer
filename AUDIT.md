# AUDIT ledger

## Baseline

| Field | Value |
|---|---|
| Commit | 8334516 - T10.9: docs and close-out for knowledge sources |
| `make check` | 762 passed, 9 skipped, ruff clean |
| `make live` | Skipped (requires BRIEFING_LIVE=1 and live services) |
| Date | 2026-11-05 |
| Assumed load | Single user running CLI, plus scheduled run at 08:00 |

## Inventory

| File | Lines | Test File |
|---|---|---|
| src/briefing/ask.py | ~430 | tests/test_ask.py, tests/test_ask_cli.py |
| src/briefing/cli.py | ~600 | tests/test_cli.py |
| src/briefing/config.py | ~120 | tests/test_config.py |
| src/briefing/extract.py | ~100 | tests/test_extract.py |
| src/briefing/fetch/local.py | ~200 | tests/test_backup_search.py |
| src/briefing/fetch/router.py | ~130 | tests/test_router.py |
| src/briefing/fetch/errors.py | ~50 | tests/test_errors.py |
| src/briefing/headline_hash.py | ~40 | tests/test_dedup.py |
| src/briefing/housekeeping.py | ~60 | tests/test_schedule.py |
| src/briefing/prompts/briefing.txt | 15 | (embedded) |
| src/briefing/deliver.py | ~80 | tests/test_deliver.py |
| src/briefing/synth.py | ~50 | tests/test_synth.py |
| src/briefing/pipeline.py | ~150 | tests/test_pipeline.py |
| src/briefing/ui.py | ~120 | tests/test_ui.py |
| src/briefing/whitelist.py | ~120 | tests/test_whitelist.py |
| src/briefing/__init__.py | ~10 | - |
| src/briefing/main.py | ~100 | tests/test_main.py |
| src/briefing/knowledge/base.py | ~50 | tests/test_knowledge_base.py |
| src/briefing/knowledge/route.py | ~40 | tests/test_route.py |
| src/briefing/knowledge/wikipedia.py | ~60 | tests/test_wikipedia.py |
| src/briefing/knowledge/stackexchange.py | ~80 | tests/test_stackexchange.py |
| src/briefing/knowledge/ddg_instant.py | deleted | (not worth it per T10.4) |
| src/briefing/search.py | ~80 | tests/test_search.py |
| src/briefing/badge.py | ~20 | tests/test_ui.py |
| src/briefing/news.py | ~30 | tests/test_cli.py |
| src/briefing/Score | - | tests/test_score.py |
| src/briefing/clean_markdown.py | ~100 | tests/test_clean.py |
| scripts/render_samples.py | ~520 | - |
| scripts/probe_region.py | ~30 | - |

## Findings

| ID | Sev | Conf | Lens | Where | Problem | Status | Fix |
|---|---|---|---|---|---|---|---|

## Checked fine

**Round 1 Mechanical sweeps:**

| Lens | What | Result |
|---|---|---|
| L1 (exceptions) | `except BaseException`, bare `except:`, `except Exception: pass` | No findings - all broad excepts have specific comments explaining why |
| L1 (CancelledError) | Check for swallowed CancelledError | No findings - no CancelledError handling found |
| L1 (subprocess/shell) | `shell=True`, `os.system`, `subprocess`, `create_subprocess` | Found 1 hit: `src/briefing/deliver.py:114` - async subprocess for voice-tts, properly isolated via `_RUNNER` seam |
| L2 (yaml.load) | Unsafe `yaml.load()` | No findings - using `yaml.safe_load` |
| L2 (secrets) | API keys, tokens in code | Found 1 hit: test fixture with fake key name (acceptable for tests) |
| L2 (email) | Email addresses in code | No findings |
| L3 (blocking calls) | Blocking sync code in async | No findings |
| L3 (gather) | `asyncio.gather` usage | Checked: uses `return_exceptions=True` correctly in pipeline.py:196 |
| L3 (create_task) | `create_task` usage | Checked: proper task management in ask.py and pipeline.py |
| L5 (resources) | Timeout config | All httpx clients have timeout set via `httpx.Timeout(config.timeout_s)` |
| L9 (skips) | `pytest.mark.skip/xfail` | Found live tests with proper `@requires_live` decorator; xfail markers at T9.x tasks |
| L11 (knowledge contract) | Adapters never raise, timeout, no secrets | Checked against knowledge adapter contract (see FINDINGS S1) |

## Findings

| ID | Sev | Conf | Lens | Where | Problem | Status | Fix |
|---|---|---|---|---|---|---|---|
| A-001 | P0 | certain | L2 | src/briefing/ask.py:372-379, 379 | Closing delimiter `\n``` ` not stripped from source text, allowing prompt injection via malicious page content. Any page with `\n``` ` in extracted text escapes its source block and becomes model instructions. | fixed | `test_prompt_injection_prevented_by_stripping_delimiter`, ask.py:374 `text.replace(_SOURCE_BLOCK_CLOSE, "")` |
| A-002 | P1 | likely | L11 | src/briefing/knowledge/wikipedia.py:144 | Wikipedia adapter returns raw `extract` field without `strip_control_chars()`. Control chars and ANSI escapes flow into prompt/JSON output. | fixed | `test_control_characters_stripped_from_extract` in test_wikipedia.py, adapter imports strip_control_chars and applies at line 143 |
| A-003 | P1 | likely | L11 | src/briefing/knowledge/stackexchange.py:209 | Stack Exchange adapter returns `_html_to_text` output without `strip_control_chars()`. Control chars flow into prompt/JSON output. | fixed | `test_control_characters_stripped_from_answer` in test_stackexchange.py, adapter imports strip_control_chars and applies at line 209 |
| A-004 | P2 | certain | L8 | tests/test_ask.py:98-102 | `test_sources_are_numbered_from_one` has empty body, no assertions, passes trivially. | open | fix-loop |
| A-005 | P2 | certain | L8 | tests/test_ask.py:171-211 | `test_deadline_cancels_slow_fetch` only asserts fast.com present, never asserts slow.com absent. Cannot detect broken cancellation. | open | fix-loop |
| A-006 | P2 | likely | L1 | src/briefing/ask.py:160-162, 354-355 | Cancelled tasks at deadlines not awaited; only `task.cancel()` called. Can leave "Task exception was never retrieved" warnings. | fixed | ask.py:161-167 and :356-362 now `await task` with `except asyncio.CancelledError: pass` |
| A-007 | P2 | certain | L4 | src/briefing/ask.py:398-399 | `_render_passages` always appends "Sources disagree" even for single source or no disagreement. | open | fix-loop |
| A-008 | P2 | certain | L8 | tests/test_stackexchange.py:353 | Mock URL `.../answers/` doesn't match actual batched URL `.../answers/1;2;3` - answers endpoint never exercised. | open | fix-loop |

## Rounds

| Round | Scope | Slices | New P0 | P1 | P2 | P3 | Discarded | Fixed | Stop? |
|---|---|---|---|---|---|---|---|---|---|