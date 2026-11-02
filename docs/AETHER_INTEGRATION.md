# Aether ↔ briefing: the IPC contract

`briefing run` is meant to be driven by Aether on a schedule. This document is the
whole agreement between the two: what Aether writes, what it may read, and what
`briefing` guarantees in return.

Everything here is **files under the state dir** (`~/.local/state/briefing/`, or
`$BRIEFING_STATE_DIR`). There is no socket, no daemon, and no shared lock file.
Files were chosen over a socket on purpose: a missing file is a state every reader
already has to handle, whereas an unreachable socket is not — see
[Failure modes](#failure-modes) for why that distinction is the whole design.

The contract is deliberately tiny: **two files Aether writes, two `briefing` owns,
and one command that speaks.** If Aether writes nothing at all, the briefing still
works. Nothing in this contract is required for a briefing to happen; it only makes
the briefing *better informed*.

---

## The shape in one table

| File | Written by | Read by | Missing means |
|---|---|---|---|
| `audio-state.json` | **Aether** | `FileAudioState.speaking()` | not speaking — the briefing may fire |
| `session-summary.json` | **Aether** | `FileSessionSummary.text()` | no continuity from yesterday |
| `seen_headlines.json` | `briefing` | `DedupStore` | (briefing owns it) |
| `api-calls.jsonl` | `briefing` | `usage_today` | (briefing owns it) |

---

## 1. `audio-state.json` — may the briefing speak?

Aether records whether it is currently speaking, so `briefing run` does not talk
over it.

```json
{"state": "speaking"}
```

Aether writes `{"state": "speaking"}` when it starts speaking and
`{"state": "idle"}` when it finishes. The read side (`FileAudioState.speaking()`)
treats `"speaking"` — case-insensitively, whitespace-trimmed — as the **only**
positive value. Every other value is "not speaking".

**What `briefing` does with it.** `Gate.refusal` checks this *after* the 24-hour
schedule gate. When Aether is speaking, the run is refused with the message
`speaking` and nothing is fetched or spoken. When Aether is idle, the run proceeds.

**The failure mode that matters.** A missing, unreadable, or non-JSON file returns
`False` — "not speaking". This is the opposite of fail-safe, and it is deliberate:
Aether's audio state is a *courtesy* signal, and a stale file left over from a
crashed Aether would otherwise wedge the morning briefing silent forever. The
briefing is allowed to interrupt a silent Aether, because the alternative is no
briefing at all. A provider that **raises** is likewise treated as not speaking —
an unreachable Aether must not be able to silence the briefing permanently.

> If Aether would rather be deferred than interrupted, the contract it needs is a
> lock Aether itself honours, not a flag `briefing` reads once. Out of scope here.

## 2. `session-summary.json` — what happened yesterday?

Yesterday's summary, so this morning's briefing can refer back to it instead of
repeating itself.

```json
{"summary": "Discussed the rate decision and the shilling."}
```

`FileSessionSummary.text()` reads the `summary` key as a string, trimmed. A missing
key, a non-dict document, or an unreadable file all yield `""` — which the speaker
treats as "nothing to continue from", not an error.

The summary is handed to the Gemini speaker as context for this morning's briefing.
It is capped by whatever the speaker's prompt does with it; `briefing` does not
truncate or rewrite it.

**Suggested discipline for Aether:** keep it to a few sentences. It is re-sent to
Gemini verbatim on every run, so a long summary costs context on a schedule.

## 3. The schedule — why a run only fires once a day

`briefing` derives one schedule id per calendar day:

```
morning_briefing_2026-11-02
```

(`_SCHEDULE_PREFIX` + the date, ISO-formatted.) `DedupStore.should_fire`
refuses a second run for the same id inside `dedup.schedule_window_hours` of the
first, and `mark_fired` records the timestamp in `seen_headlines.json`.

`schedule_id` has no timezone of its own — it formats whatever `datetime` it is
handed, and the runner hands it `datetime.now(UTC)`, so the date baked into the id
is the **UTC** date. That is harmless for the scheduled case: 08:00 EAT is 05:00
UTC on the same calendar day, so one id is recorded per morning either way.

The one case worth knowing is a **manual run between 03:00 and 08:00 EAT**. Those
share the morning's UTC date and sit inside the 24-hour window, so they suppress
that morning's scheduled briefing — the run you asked for is the one you get. A
manual run at any other hour carries a different date and suppresses nothing; a
run between 00:00 and 03:00 EAT is tagged with *yesterday's* UTC date.

**Aether does not need to know this.** The gate is internal; a second `briefing
run` today is refused with `already fired today` and is harmless. This is what makes
the systemd timer safe to leave in place: a rebooted machine that fires twice in a
morning produces one briefing, not two.

If Aether wants to force a run (a "tell me again" request), it should *not* reach
into `seen_headlines.json`. The intended path is a different invocation, not a
hand-edited store.

---

## What `briefing` owns

These are `briefing`'s files. Aether **reads** them and never writes them.

### `seen_headlines.json`

```json
{
  "entries": [{"hash": "a1b2c3...", "seen_at": "2026-11-01T08:00:03+00:00"}],
  "fired": {"morning_briefing_2026-11-02": "2026-11-02T08:00:04+00:00"}
}
```

The list is `entries`, not `headlines`: `_load` reads `data.get("entries")` and
treats anything else as an empty list. A hand-written `headlines` key therefore
reads as a store with nothing in it — no suppression and no gate record — which is
why this file is Aether-**read**, never Aether-written.

Headline hashes expire after `dedup.headline_ttl_days` (default 7). **A corrupt
entry expires rather than suppressing:** an unparseable `seen_at` counts as
expired, so a broken store stops hiding headlines instead of silently hiding them
forever. That direction of failure is chosen because the cost of a repeated
headline is a slightly odd briefing, and the cost of a missing one is an
unnoticed hole in the news.

### `api-calls.jsonl`

One JSONL row per cloud API call: `ts`, `url`, `domain`, `reason`, `outcome`.
Deliberately **keyless** — the API key never appears here, in `briefing.log`, or in
`--json` output. `T6.2` enforces this at every log sink with a `SecretFilter`;
`tests/test_log.py` pins it.

Aether may read this to show the owner how much of the daily cap is left. Do not
write to it: `usage_today` counts today's rows, and fabricated rows would suppress
real fetches.

---

## Delivery: what actually speaks

`briefing run` speaks by running, as a subprocess:

```bash
voice-tts -s aether -p high "<the briefing text>"
```

- `-s aether` — the voice (hard-coded, per SPEC section 6).
- `-p high` — the priority.
- The text is the only thing that varies.

`voice-tts` is **not** run by the automated tests: delivery goes through an
injectable runner (`SubprocessRunner`), and `briefing run --dry-run` prints the
exact argv without running anything. Real audio is verified by the owner.

**Aether's role in delivery ends at the audio state.** `briefing` runs
`voice-tts` itself; Aether is not in the path. The only thing Aether contributes is
the `audio-state.json` flag that decides whether the run happens at all, and the
`session-summary.json` that shapes what is said.

---

## Scheduling

`systemd/briefing.service` and `systemd/briefing.timer` run the briefing at
**08:00 Africa/Nairobi**. The timer is the whole reason for the once-a-day gate:
systemd restarts, clock changes and double-fires are all absorbed by it.

Aether may also invoke `briefing run` directly whenever it likes — the same gate
applies, and a run inside the window is refused rather than duplicated. Neither
side needs to know about the other's scheduler.

---

## Failure modes

Every row is a state the readers already have to handle, and each one degrades
toward "the briefing still happens".

| What Aether does | What `briefing` does |
|---|---|
| Writes nothing, ever | Fires normally; no continuity, no gate delay |
| Writes no `audio-state.json` | Treats Aether as not speaking — fires |
| Writes a corrupt/unparseable file | Same as missing — fires |
| Writes `{"state": "weird"}` | Not `"speaking"`, so — fires |
| Writes `{"state": "speaking"}` | Refuses with `speaking`; nothing fetched or spoken |
| Is unreachable *and* the provider raises | Treated as not speaking — fires (D7) |
| Writes a huge `summary` | Sent to Gemini verbatim; costs context, no crash |
| Writes to a `briefing`-owned file | Undefined — do not; a corrupt store degrades, not crashes |

The one asymmetric case is worth stating plainly, because it looks like a bug: **a
crashed Aether that never wrote `"idle"` leaves no trace, and `briefing` reads that
as permission to talk.** If Aether is killed mid-sentence, the flag it would have
flipped never gets flipped. The briefing may then start while Aether is playing a
sentence of yesterday's. That is the accepted trade, recorded as D7 — same as a
missing file, by design, because the failure it prevents (a permanently silent
morning) is strictly worse.

---

## What is *not* in this contract

- No socket, no HTTP, no shared memory. Files only.
- No Aether dependency in `briefing`: it runs standalone, and `briefing run` works
  with this entire contract unimplemented.
- No authentication on the files — they live in the user's state dir, protected by
  the filesystem like everything else in `~/.local/state/`.
- No cleanup protocol. Aether may delete either file; both are self-healing on read.

## Verifying the contract

```bash
briefing status                       # health check, including the log file path
briefing run --dry-run                # print exactly what would be spoken
BRIEFING_STATE_DIR=/tmp/demo briefing run --dry-run   # a whole run against a scratch dir
```

`briefing run --dry-run` is the one command that exercises every reader above
without touching the network or the speaker, and it is what a change to this
contract should be checked against first.
