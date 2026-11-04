"""Which `ddgs` region serves Kenyan news best? (T8.4)

Not a test, and not part of the CLI. This is the evidence behind
`search.region`'s default: for the one query that matters to this install — the
morning briefing's news mode — it asks `ddgs` once per candidate region and
reports how many hits each returned and how many of them were Kenyan outlets.

Run from the repo root:

    .venv/bin/python scripts/probe_region.py

or `make probe-region`, which is the same thing. It writes nothing and reads no
config; the only side effect is three searches against DuckDuckGo, which is what
`briefing` itself does on every run. A region that returns fewer but local
results is a better briefing than one that returns more international ones, so
the table reports both numbers rather than a single score.
"""

from __future__ import annotations

import sys
from typing import Any
from urllib.parse import urlparse

from ddgs import DDGS

#: The query, taken from SPEC section 6's own example. "Kenya" is in it because
#: the briefing is Kenyan, not because ddgs uses it as a location hint.
QUERY = "latest breaking news in kenya"

#: Candidate defaults, in the order T8.4 names them. `ke-en` is Kenya, `wt-wt`
#: is ddgs's "no region" escape hatch, `us-en` is the shipped default and ddgs's
#: own (`ddgs/ddgs.py:141`). Probed, all three, on 2026-11-04.
REGIONS = ("ke-en", "wt-wt", "us-en")

#: Hosts that are Kenyan without ending in `.ke`, kept in one place because the
#: next regional TLD somebody invents should not need a code change.
_EXACT_HOSTS = frozenset({"nation.africa", "citizen.digital", "kenyans.co.ke"})

#: How many results to ask for. Ten is the config default, so the comparison is
#: made with the same width a real run uses.
LIMIT = 10


def host_of(url: str) -> str:
    """The bare host, or an empty string for a hit with no URL."""
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def is_kenyan(url: str) -> bool:
    """True for a Kenyan outlet: a `.ke` host, or one of the named ones."""
    host = host_of(url)
    return bool(host) and (host.endswith(".ke") or host in _EXACT_HOSTS)


def probe(region: str) -> dict[str, Any]:
    """One news search against `region`, counted. Never raises."""
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.news(QUERY, region=region, max_results=LIMIT))
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not crash
        print(f"  {region}: failed ({exc!r})", file=sys.stderr)
        return {"found": 0, "kenyan": 0, "hosts": []}
    return {
        "found": len(hits),
        "kenyan": sum(1 for hit in hits if is_kenyan(str(hit.get("url") or ""))),
        "hosts": [host_of(str(hit.get("url") or "")) for hit in hits],
    }


def main() -> int:
    show_hosts = "--hosts" in sys.argv[1:]
    print(f'query: "{QUERY}" (news mode, max_results={LIMIT})\n')
    rows = [(region, probe(region)) for region in REGIONS]

    width = max(len(region) for region in REGIONS)
    print(f"{'region'.ljust(width)}  found  kenyan  share")
    print(f"{'-' * width}  -----  ------  -----")
    for region, counts in rows:
        found, kenyan = counts["found"], counts["kenyan"]
        share = f"{kenyan / found:.0%}" if found else "-"
        print(f"{region.ljust(width)}  {found:>5}  {kenyan:>6}  {share:>5}")

    # The recommendation is the region with the most Kenyan hits, and the count
    # of found results only breaks a tie — a wider result set that is all abroad
    # is a worse briefing than a smaller local one.
    scored = sorted(rows, key=lambda row: (row[1]["kenyan"], row[1]["found"]), reverse=True)
    best, best_counts = scored[0]
    if best_counts["kenyan"] == 0:
        # Saying "best: ke-en (0 of 10)" would dress a null result up as a
        # winner. When nothing is local, no region wins and the finding is that
        # the query — not the region — is what decides the country.
        print("\nNo region returned a Kenyan outlet for this query, so there is no")
        print("evidence for changing the default. The hosts are below; read them")
        print("before concluding anything about the region.")
    else:
        print(
            f"\nbest for this query: {best} "
            f"({best_counts['kenyan']} of {best_counts['found']} hits Kenyan)"
        )

    if show_hosts:
        for region, counts in rows:
            print(f"\n{region}: {', '.join(counts['hosts']) or '(none)'}")

    if all(counts["found"] == 0 for _region, counts in rows):
        print("\nEvery region returned nothing: ddgs is rate-limiting or the")
        print("network is unavailable, so this run proves nothing. Re-run later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
