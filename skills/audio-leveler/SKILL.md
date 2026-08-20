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

**The interpreter name is not the same everywhere.** `python3` is right on macOS and
most Linux; on Windows it is usually `python`, and `py -3` where the launcher is
installed. Check which one exists before running the command rather than assuming,
and if none does, say Python 3.10+ needs installing instead of retrying.

`<source>` is a local audio or video file, an Apple Podcasts episode link, or
any URL yt-dlp can fetch. Downloads are cached by episode identity, so running
`measure` and then `apply` on the same link downloads once. The cache lives in
`~/.cache/audio-leveler` and is never pruned — mention it if disk space matters.

Other flags on `apply`:

| Flag | Use it when |
|---|---|
| `--target-lufs` | The default −16 is unreachable (see exit 8 below), or the listener wants a different level. Accepts −40 to −5. |
| `--mono auto\|force\|never` | `auto` downmixes only on detected dual mono. `force` is worth suggesting when `channel_separation_db` is high but not conclusive and speed matters — it roughly halves the render time and halves the file size. |
| `--out` / `--force` | A specific destination, or replacing an existing file. |

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
| `channel_separation_db` | How far the L−R difference sits below the programme. `null` means either a silent difference (true dual mono) or a source where the question does not apply. Around 25 dB usually means a mostly-dual-mono recording with one genuinely stereo section, such as an intro. |
| `speech_ratio` | Fraction of samples above the silence gate. |
| `window_sec` / `windows` | The statistics window used, and one entry per window. The window scales down for short sources. |

### Reading them

- **`spread` below about 6 LU** — the source is probably fine as it is. Say so
  and stop. Processing it anyway is how a good recording gets made worse.
- **`intra` clearly larger than `drift`** — the level moves around inside each
  section: a speaker shifting distance from the microphone, or several people
  at different volumes. `--filter speech`.
- **`drift` clearly larger than `intra`** — each section is internally steady
  but the sections sit at different levels: recorded across several sittings,
  or a change of room. `--filter segmented`.
- **Both large** — the level moves between sections *and* within them. Compose
  the stages: `--filter segmented,speech`. They fix different things and the
  order matters (between-section first).
- **`drift` is only meaningful with more than one window.** Check
  `len(windows)`; with a single window `drift` is 0 by construction and says
  nothing about the source.
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

### When apply exits non-zero

| Exit | Meaning | What to do |
|---|---|---|
| 8 | The requested loudness cannot be reached without clipping, **or** the source is already perfectly flat. The message carries a target that does work. | Rerun with the `--target-lufs` value from the message. Do not silently pick a different number. |
| 6 | Verification failed after rendering. | Report it; the output was discarded and the original is untouched. |
| 7 | The output path is taken. | Ask before passing `--force`. |
| 3 | ffmpeg or yt-dlp is missing. | Pass on the install command from the message. Do not install anything. |
| 2 | Bad input: the source is missing, a URL would not resolve, or the `--out` extension is not one this tool writes (`mp3`, `m4a`, `wav`, `flac`). | The message names the problem. The output format follows the extension, so `--out x.wav` really does write WAV. |
| 4 | The source is silent or too short to diagnose. | Say so; there is nothing to level. |

## The stages

| Stage | Fixes | How |
|---|---|---|
| `speech` | Swings **within** a section | `speechnorm`, then two-pass linear `loudnorm` |
| `segmented` | Level differences **between** sections | A gain curve computed from the source's own per-window loudness, interpolated between window centres so the level slides rather than steps, then two-pass linear `loudnorm` |
| `loudness` | Nothing unstable; the level is simply wrong | Two-pass linear `loudnorm` only |

`speech` and `segmented` compose — `--filter segmented,speech`. `loudness` means
"no pre-stage" and cannot be combined.

Measured on a source whose two halves differ by 18 LU (spread 18.5 LU):

| | spread after | drift after |
|---|---|---|
| `speech` | 17.5 | 17.1 |
| `segmented` | 4.0 | 0.0 |
| `segmented,speech` | 3.9 | 0.0 |

`segmented` boosts quiet passages, so exit 8 (target unreachable) is more likely
with it than without. That is not a failure — follow the target in the message.

One caveat when reading its numbers: the gain ramps between windows rather than
stepping, which costs a little measured convergence (5.1 LU rather than 3.9 on the
reference step material) because the ramp itself sits inside the measurement. The
stepped version scored better and sounded worse — it jumped 6 dB at a window edge.
Do not read the smaller number as the better result here.

## Limits worth stating up front

- **Speech only.** In music, dynamic range is the intent; flattening it is
  damage. This tool is not for music.
- **No `auto`.** `--filter` is required and has no automatic mode on purpose.
  Without a model reading the numbers, the tool does not guess.
- **No noise reduction** in this version.
- More than two channels: the dual-mono check is skipped and the channel layout
  is preserved.
