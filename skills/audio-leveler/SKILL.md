---
name: audio-leveler
description: Fix audio where the volume keeps going up and down — podcasts, lectures, interviews, meeting recordings. Measures the loudness first, then applies the filter that matches what is actually wrong. Use when the user says a recording is too quiet in places, keeps needing the volume adjusted, has uneven speaker levels, or asks to normalize, level, or fix the loudness of an audio or video file or a podcast link.
---

# audio-leveler

Speech audio whose volume keeps moving is not one problem, it is several, and
the fix for one does nothing for the others. This skill measures first, then
you decide which filter matches the measurement, then the tool applies it and
re-measures to show whether it actually worked.

The scripts contain no judgement at all. `measure.py` produces numbers,
`apply.py` executes explicit parameters. Deciding what the numbers mean is your
job, and this file is the guidance for it.

## Two steps, always in this order

```bash
python3 <skill-dir>/scripts/cli.py measure <source> --json
# read the numbers, decide, then:
python3 <skill-dir>/scripts/cli.py apply <source> --filter speech|segmented|loudness
```

`<source>` is a local audio or video file, an Apple Podcasts episode link, or
any URL yt-dlp can fetch. Downloads are cached by episode identity, so running
`measure` and then `apply` on the same link downloads once.

Drop `--json` for a human-readable report. Use `--json` when you are the one
reading it.

## What the numbers mean

The contract from `measure --json`:

| Field | Meaning |
|---|---|
| `spread_lu` | p95 − p5 of short-term loudness. How wide the swing is overall. |
| `drift_lu` | Spread of the per-6-minute medians. Movement **between** sections. |
| `intra_lu` | Median of the per-6-minute max−min. Movement **within** a section. |
| `integrated_lufs` | Overall loudness. −16 is the podcast convention. |
| `dual_mono` | True when the two channels carry the same signal. |
| `speech_ratio` | Fraction of samples above the silence gate. |

### Reading them

- **`spread` below about 6 LU** — the source is probably fine as it is. Say so
  and stop. Processing it anyway is how a good recording gets made worse.
- **`intra` clearly larger than `drift`** — the level moves around inside each
  section: a speaker shifting distance from the microphone, or several people
  at different volumes. `--filter speech`.
- **`drift` clearly larger than `intra`** — each section is internally steady
  but the sections sit at different levels: recorded across several sittings,
  or a change of room. `--filter segmented`.
- **Both small but `integrated_lufs` far from the target** — nothing is
  unstable, the whole thing is simply at the wrong level. `--filter loudness`.

These are directions for reading, not thresholds to apply mechanically. A
distribution that resembles none of the shapes above is a signal to say what is
puzzling about it and suggest the user listen, not to force it into a category.

### A worked example

The episode this tool was built from measured `spread` 10.0, `intra` 16.2,
`drift` 3.5, `integrated_lufs` −16.8.

The integrated loudness was already exactly right — which is why every
copy-paste `loudnorm` recipe on the internet did nothing for it. `intra` far
above `drift` says the problem is inside each section. `--filter speech` took
the spread from 10.0 to 5.8 LU, confirmed by ear.

## After applying

`apply` re-measures the output and reports one of three outcomes. Pass on
whichever one you get, unchanged:

- **converged N%** — it worked.
- **essentially unchanged** — this filter did not address what is wrong with
  this source. Consider a different one, or leave the source alone.
- **got worse** — say so plainly and recommend keeping the original.

Never describe an unchanged or worse result as a success. The re-measurement
exists precisely so that nobody has to take the tool's word for it.

## Limits worth stating up front

- **Speech only.** In music, dynamic range is the intent; flattening it is
  damage. This tool is not for music.
- **No `auto`.** `--filter` is required and has no automatic mode on purpose.
  Without a model reading the numbers, the tool does not guess.
- **No noise reduction** in this version.
- More than two channels: the dual-mono check is skipped and the channel layout
  is preserved.
