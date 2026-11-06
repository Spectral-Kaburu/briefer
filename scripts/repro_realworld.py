#!/usr/bin/env python3
"""Run the owner's failing commands for real and report exactly what is wrong.

This is not a unit test. It uses the real network, the real local Firecrawl and,
when GEMINI_API_KEY is set, the real model. Run it from the repo root:

    .venv/bin/python scripts/repro_realworld.py                # --no-api: no cloud credits
    .venv/bin/python scripts/repro_realworld.py --spend-api    # faithful: may spend API calls

State goes to a temp directory, nothing is written to your real state, a key is
never printed, and the exit code is 1 when any defect is found.
`tests/test_live_realworld.py` runs the same checks under `make live`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ASK_QUESTION = "what are mach band effects in computer graphics"
RESEARCH_QUERY = "what are mach band effects"
TIMEOUT_S = 240

# Text that must never be the body of a result card (D13).
JUNK = {
    "bibliography line": re.compile(r"(?:^|\s)\d{1,2}\.\s+[A-Z][\w'’-]+ [A-Z]{1,3}[,.]"),
    "retrieved-date citation": re.compile(r"Retrieved\s+\w+\s+\d{1,2},\s+\d{4}"),
    "DOI/PMID/ISSN marker": re.compile(r"^(?:DOI|PMID|ISSN)\s*[:：]"),
    "escaped-bracket link fragment": re.compile(r"\[\\\[\d+\\\]\]\("),
    "page chrome": re.compile(
        r"Cite This Page|You are here|Article Information|Skip to (?:main )?content"
        r"|Mark as Completed",
        re.IGNORECASE,
    ),
}


def _bin() -> str:
    env = os.environ.get("BRIEFING_BIN")
    if env:
        return env
    sibling = Path(sys.executable).with_name("briefing")
    if sibling.exists():
        return str(sibling)
    found = shutil.which("briefing")
    if found:
        return found
    raise SystemExit("cannot find the briefing console script; set BRIEFING_BIN")


def _run(args: list[str], state_dir: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["BRIEFING_STATE_DIR"] = state_dir
    env["NO_COLOR"] = "1"
    done = subprocess.run(
        [_bin(), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=TIMEOUT_S,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                return None
    return None


def _clip(value: object, n: int = 90) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _dicts(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _dicts(value)


def _strings(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_strings(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_strings(v) for v in obj)
    return ""


def _results(obj: Any) -> list[dict[str, Any]]:
    seen: set[str] = set()
    found = []
    for d in _dicts(obj):
        url = d.get("url")
        if isinstance(url, str) and url not in seen:
            seen.add(url)
            found.append(d)
    return found


def _relevant(result: dict[str, Any]) -> bool:
    text = _strings(result).lower()
    return "mach" in text and re.search(r"\bbands?\b", text) is not None


def _ends_cleanly(answer: str) -> bool:
    return answer.rstrip()[-1:] in set('.!?)]}"”’»*…')


def check_ask(data: Any, say: Callable[[str], None]) -> list[str]:
    problems: list[str] = []
    answer = str(data.get("answer") or "").strip() if isinstance(data, dict) else ""
    used_llm = bool(data.get("used_llm")) if isinstance(data, dict) else False
    sources = _results(data.get("sources", data) if isinstance(data, dict) else data)
    say(f"  used_llm={used_llm}  sources={len(sources)}  answer={_clip(answer, 70)!r}")
    for i, s in enumerate(sources[:5], 1):
        mark = "relevant  " if _relevant(s) else "IRRELEVANT"
        say(f"  [{i}] {mark} {s.get('domain') or s.get('url')}  {_clip(s.get('title'), 60)}")
    if used_llm and not answer:
        problems.append("D11 the model answered but the answer is empty")
    elif used_llm and not _ends_cleanly(answer):
        problems.append(f"D11 the Answer is cut off mid-sentence: {_clip(answer, 70)!r}")
    if not sources:
        problems.append("D10 ask found no sources at all")
        return problems
    top = sources[:3]
    relevant = sum(_relevant(s) for s in top)
    if not _relevant(sources[0]):
        problems.append(
            f"D10 ask's first source is not about Mach bands: {_clip(sources[0].get('url'), 70)}"
        )
    if relevant < min(2, len(top)):
        problems.append(f"D10 only {relevant} of the top {len(top)} ask sources mention Mach bands")
    return problems


def check_research(data: Any, whitelist_text: str, say: Callable[[str], None]) -> list[str]:
    problems: list[str] = []
    results = _results(data)
    say(f"  results={len(results)}")
    for i, r in enumerate(results[:5], 1):
        mark = "relevant  " if _relevant(r) else "IRRELEVANT"
        say(f"  [{i}] {mark} {r.get('domain') or r.get('url')}  {_clip(r.get('title'), 60)}")
    if not results:
        return ["D10 research returned no results"]
    top = results[:5]
    relevant = sum(_relevant(r) for r in top)
    if not _relevant(results[0]):
        problems.append(
            f"D10 research's first result is not about Mach bands: {_clip(results[0].get('url'), 70)}"
        )
    if relevant < min(3, len(top)):
        problems.append(
            f"D10 only {relevant} of the top {len(top)} research results mention Mach bands"
        )
    for label, pattern in JUNK.items():
        hits = [
            r
            for r in results
            if pattern.search(_strings({k: v for k, v in r.items() if k != "url"}))
        ]
        if hits:
            sample = _clip(_strings({k: v for k, v in hits[0].items() if k != "url"}), 70)
            problems.append(f"D13 {len(hits)} card(s) show: {label}, e.g. {sample!r}")
    local_ok = any(str(d.get("method", "")).upper() == "LOCAL" for d in _dicts(data))
    degraded = [
        ln.strip() for ln in whitelist_text.splitlines() if "SCRAPE_ALL_ENGINES_FAILED" in ln
    ]
    say(
        f"  local successes this run: {'yes' if local_ok else 'none'}; whitelist entries from SCRAPE_ALL_ENGINES_FAILED: {len(degraded)}"
    )
    if degraded and not local_ok:
        problems.append(
            f"D12 every local scrape failed, yet {len(degraded)} domain(s) were whitelisted "
            f"for SCRAPE_ALL_ENGINES_FAILED: {_clip(degraded[0], 80)}"
        )
    return problems


def check_plain(text: str) -> list[str]:
    problems: list[str] = []
    if re.search(r"(?<!\d )days ago", text):
        problems.append("D14 a source line shows 'days ago' with no number")
    if re.search(r"·\s+·", text):
        problems.append("D14 the footer has an empty segment ('·  ·')")
    if "LOCAL" in text and "fetch: 0.0s" in text:
        problems.append("D14 the footer reports fetch: 0.0s although pages were fetched")
    return problems


def probe_gemini(model: str, say: Callable[[str], None]) -> list[str]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        say("  skipped: GEMINI_API_KEY is not set (the model checks did not run)")
        return []
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # noqa: BLE001
        say(f"  skipped: google-genai is not importable ({type(exc).__name__})")
        return []
    try:
        # Keep the client in a variable: a temporary Client is garbage-collected, and its
        # __del__ closes the HTTP client before the request is sent ("client has been closed").
        client = genai.Client(api_key=key)
        reply = client.models.generate_content(
            model=model,
            contents="Explain what Mach bands are in exactly three short sentences.",
            config=types.GenerateContentConfig(
                max_output_tokens=200,
                temperature=0,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        say(
            f"  model call failed: {type(exc).__name__}: {_clip(str(exc).replace(key, '<key>'), 150)}"
        )
        return []
    finish = reply.candidates[0].finish_reason if reply.candidates else None
    usage = reply.usage_metadata
    thoughts = getattr(usage, "thoughts_token_count", None)
    out_tokens = getattr(usage, "candidates_token_count", None)
    say(
        f"  model={model} finish_reason={finish} visible_tokens={out_tokens} thinking_tokens={thoughts}"
    )
    if "MAX_TOKENS" in str(finish):
        return [
            (
                f"D11 the model hit MAX_TOKENS at max_output_tokens=200 with {out_tokens} visible and "
                f"{thoughts} thinking tokens: thinking is eating the output budget"
            )
        ]
    return []


def diagnose(spend_api: bool = False, say: Callable[[str], None] = print) -> list[str]:
    problems: list[str] = []
    flags = [] if spend_api else ["--no-api"]
    with tempfile.TemporaryDirectory(prefix="briefing-repro-") as state:
        say(f"\n== ask --json {ASK_QUESTION!r} {' '.join(flags)}")
        code, out, err = _run(["ask", ASK_QUESTION, *flags, "--json"], state)
        data = _parse(out)
        if data is None:
            problems.append(f"ask --json gave no parseable JSON (exit {code}): {_clip(out or err)}")
        else:
            problems += check_ask(data, say)

        say("\n== ask --plain (the screen the owner sees)")
        _, plain, _ = _run(["ask", ASK_QUESTION, *flags, "--plain"], state)
        problems += check_plain(plain)

        say(f"\n== research --json {RESEARCH_QUERY!r} {' '.join(flags)} -n 5")
        code, out, err = _run(["research", RESEARCH_QUERY, *flags, "-n", "5", "--json"], state)
        data = _parse(out)
        _, wl, _ = _run(["whitelist", "list"], state)
        if data is None:
            problems.append(
                f"research --json gave no parseable JSON (exit {code}): {_clip(out or err)}"
            )
        else:
            problems += check_research(data, wl, say)

        say("\n== the model, called directly with the same limits")
        _, config_text, _ = _run(["config"], state)
        found = re.search(r"gemini-[\w.\-]+", config_text)
        model = os.environ.get("BRIEFING_PROBE_MODEL") or (found.group(0) if found else "")
        if model:
            problems += probe_gemini(model, say)
        else:
            say("  skipped: no model name found in `briefing config` (set BRIEFING_PROBE_MODEL)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--spend-api",
        action="store_true",
        help="allow cloud API calls (faithful repro)",
    )
    args = parser.parse_args()
    problems = diagnose(spend_api=args.spend_api)
    print("\n== result")
    if not problems:
        print("  no defects found by these checks")
        return 0
    for p in problems:
        print(f"  DEFECT {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
