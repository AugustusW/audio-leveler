# audio-leveler

> **Speech audio whose volume keeps moving → levelled. Measured first, so the fix matches the fault.**

English | [繁體中文](./README.zh-TW.md)

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](#install)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-skill%20%2B%20plugin-orange.svg)](https://claude.com/claude-code)
[![Codex](https://img.shields.io/badge/Codex-compatible-black.svg)](https://developers.openai.com/codex/skills)

An agent skill — open [SKILL.md standard](https://developers.openai.com/codex/skills), works in
[Claude Code](https://claude.com/claude-code) **and** [Codex](https://developers.openai.com/codex/skills) —
that fixes podcasts, lectures, interviews and
meeting recordings whose volume keeps going up and down. It **measures the loudness first**, then
applies the stage that matches what is actually wrong, then **re-measures its own output** and
says whether that worked.

## Why?

Reaching for the volume control every few minutes is a bad way to listen to a podcast. But
"inconsistent volume" is not one fault, it is several, and the fix for one does nothing for the
others.

The episode this tool was built from measures `-16.8 LUFS` integrated — already exactly the
podcast convention. Every copy-paste `loudnorm` recipe on the internet adjusts that number, so
every one of them left the episode exactly as unlistenable as before. What was actually wrong
lived one level down, in how far the loudness moved *within* each section.

So the tool does not start by processing. It starts by measuring, and it hands the numbers to
whoever is deciding.

## Features

- **Measure before choosing.** Three numbers — overall spread, drift between sections, swing
  within a section — that distinguish faults needing different fixes.
- **The scripts hold no judgement.** `measure.py` emits numbers, `apply.py` executes explicit
  parameters, and the decision lives in `SKILL.md` for the host model to make.
- **Every render is checked.** `apply` re-measures its own output and reports converged,
  unchanged, or worse. An ineffective filter cannot be presented as a success.
- **Stages compose.** `--filter segmented,speech` fixes level differences between sections and
  swings within them, in that order.
- **Two-pass linear `loudnorm` throughout**, never the dynamic mode that produces pumping.
- **Sources:** local files, Apple Podcasts episode links, anything yt-dlp can fetch — cached by
  episode identity, so measuring and then applying downloads once.

## Install

Needs [ffmpeg](https://ffmpeg.org/) (which brings `ffprobe`), and
[yt-dlp](https://github.com/yt-dlp/yt-dlp) only if you pass URLs.

```bash
brew install ffmpeg          # macOS; apt install ffmpeg on Debian/Ubuntu
pip install yt-dlp           # optional, for URL sources
```

Nothing is installed for you. If a tool is missing you get told which one and
how to get it.

## Usage

```bash
python3 skills/audio-leveler/scripts/cli.py measure <source>
python3 skills/audio-leveler/scripts/cli.py apply <source> --filter speech
```

On Windows the interpreter is usually `python` or `py -3` rather than `python3`.

### As a Codex skill

```bash
git clone https://github.com/AugustusW/audio-leveler.git
cp -r audio-leveler/skills/audio-leveler ~/.codex/skills/audio-leveler        # personal
# or, per-project: cp -r audio-leveler/skills/audio-leveler <repo>/.codex/skills/audio-leveler
```

Invoke it with an `$audio-leveler` mention, or let Codex pick it implicitly when you ask to fix
the volume of a recording. The download cache (`~/.cache/audio-leveler/`, or
`C:\Users\<you>\.cache\audio-leveler\` on Windows) is shared with Claude Code — measure once,
apply from either.

`<source>` is a local audio or video file, an Apple Podcasts episode link, or
any URL yt-dlp can fetch. Downloads are cached by episode identity, so measuring
and then applying on the same link downloads once.

### measure

```
Source: 0:54:07, 2 channel(s)
Integrated loudness: -16.8 LUFS (LRA 9.2 LU)

Short-term loudness (3s window, gated):
  spread (p95 - p5): 10.0 LU
  drift  (between 6-minute windows): 3.9 LU
  intra  (within a window, median): 15.5 LU
  percentiles: p5 -22.6 / p25 -20.5 / p50 -18.6 / p75 -16.1 / p95 -12.6 LUFS
  speech ratio: 100% of samples above the gate

Channels: stereo kept: channel separation 24.9 dB. Below the 60 dB margin
required to call it fake stereo, so a downmix could lose content.
```

`intra` far above `drift` says the loudness moves around *inside* each section
rather than *between* sections — a speaker changing distance from the
microphone, or several people at different volumes. That is what `--filter
speech` addresses.

Add `--json` to get the raw contract instead, for a model or a script to read.

### apply

`--filter` is required and has no `auto`. That is deliberate: without something
reading the measurement, this tool does not guess.

| Stage | For | What it does |
|---|---|---|
| `speech` | `intra` > `drift`: movement inside sections | `speechnorm`, then two-pass linear `loudnorm` |
| `segmented` | `drift` > `intra`: sections at different levels | A gain curve built from the source's own per-window loudness, interpolated between window centres so the level slides rather than steps, then two-pass linear `loudnorm` |
| `loudness` | both small, level simply wrong | two-pass linear `loudnorm` only |

Stages compose, in order: `--filter segmented,speech` fixes level differences
between sections and swings within them. `loudness` means "no pre-stage" and
cannot be combined.

Measured on a source whose two halves differ by 18 LU (spread 18.5 LU):

| | spread after | drift after |
|---|---|---|
| `speech` | 17.5 | 17.1 |
| `segmented` | **4.0** | **0.0** |
| `segmented,speech` | **3.9** | **0.0** |

For comparison, none of ffmpeg's stock dynamics filters get near this on the same
source: `dynaudnorm` reaches 16.4 LU at best, `compand` 16.9, `speechnorm` 17.5.
They are built to move gain gently, and a sustained 18 dB correction is precisely
what they are designed not to do.

Every render finishes by measuring the output again and reporting one of three
outcomes: converged by N%, essentially unchanged, or got worse. An unchanged or
worse result is stated as such — the tool does not describe its own output as a
success without evidence.

The output format follows the `--out` extension — `mp3`, `m4a`, `wav` or `flac`.
`--out talk.wav` really does write WAV, and the lossless formats skip the bitrate
setting entirely. Without `--out`, the default is `<source>-leveled.mp3` next to
the source. An extension this tool does not write is refused up front rather than
silently written as something else.

Other flags: `--out PATH`, `--target-lufs` (default −16), `--mono
auto|force|never`, `--force` to overwrite.

## How it works

1. **`ebur128`** reports short-term loudness every 0.1 s over a 3-second sliding window. Silence
   is gated out relative to the file's own integrated loudness, not at a fixed level — a fixed
   gate changes which samples are in the population when the level changes, which makes
   before/after comparison meaningless.
2. **Three statistics** come out of that series: `spread` (p95 − p5), `drift` (between windows)
   and `intra` (within a window). The statistics window scales with duration.
3. **A dual-mono check** measures the L−R difference signal, not the two channels' RMS. RMS is an
   energy statistic and delaying one channel does not change it, so an audibly stereo signal can
   show a 0.0012 dB difference between channels.
4. **You (or the host model) choose the stages.** There is no automatic mode.
5. **The render** is one filtergraph: mono downmix first (true-peak oversampling scales with
   channel count), then the chosen stages, then a two-pass `loudnorm` in `linear` mode.
6. **The output is measured again** and compared with the input.

`linear` mode has three preconditions and ffmpeg announces none of them — it silently falls back
to the dynamic mode this tool exists to avoid. The tool checks all three itself, refuses before
spending a render when the target is unreachable, and names a target that is reachable.

## As a skill

`skills/audio-leveler/SKILL.md` carries the guidance for reading the numbers.
The scripts hold no judgement at all: `measure.py` produces numbers, `apply.py`
executes explicit parameters, and deciding what the numbers mean happens in
between. That split is why the thresholds are guidance rather than constants —
they came from a small number of recordings, and freezing them in code would
freeze those recordings' characteristics along with them.

## Known limits

These are deliberate, not gaps in testing — for the latter see [Status](#status).

- **Speech only.** In music, dynamic range is the intent, not a defect.
- **No noise reduction** in this version.
- **`--filter` has no automatic mode.** Without a model reading the measurement, the tool does not
  guess. Passing an explicit stage is always required.
- **`drift` needs more than one window to mean anything.** The window scales with duration, but a
  source shorter than about two minutes still yields few windows.
- **`segmented` boosts quiet passages**, so the default −16 LUFS target is more often out of
  reach with it. The tool says so and names a target that works.
- **More than two channels:** the dual-mono check is skipped. `--mono force` still downmixes
  correctly using the layout's own coefficients.

## Status

v0.1.0 ([CHANGELOG](./CHANGELOG.md)) — 165 tests, of which 159 run fully offline (ffmpeg, ffprobe
and yt-dlp are mocked; no network, no media). The remaining 6 drive real ffmpeg and are excluded
from CI.

| Component | Verified version |
|---|---|
| macOS | 26.5.1 (Apple M4 Pro) |
| Windows | measure path only, under Codex |
| Python | 3.9.6 and 3.12.13 locally; 3.10–3.13 in CI |
| ffmpeg | 8.1 |
| yt-dlp | 2026.07.04 |

**Verified end to end on real material:**

- macOS: a 54-minute podcast episode, resolved from an Apple Podcasts link, measured, levelled
  with `speech`, and confirmed by ear — spread 10.0 → 5.8 LU.
- Windows, under Codex: a 50-minute episode resolved, downloaded, cached and measured — spread
  4.16 LU, and the skill correctly declined to process it rather than treating a stable recording
  as a problem. This is the first time that "nothing needs doing" path has run on real material;
  until then it had only been unit-tested.

**Not yet covered:** the render itself has not been run on Windows — only the measure path has.
Linux is untested beyond CI's unit tests. `segmented` has only been verified against synthetic
step material, not a real drifting recording. Dual-mono detection is whole-file, so a recording
whose body is dual mono but whose intro is real stereo reports the intro's lower separation — the
safe answer, not a precise one. The download cache is never pruned.

Issues and PRs welcome.

## License

MIT. See [LICENSE](./LICENSE).

The source-resolution layer (Apple Podcasts lookup, yt-dlp download, cache keys) is ported from
[audio-tldr-skill](https://github.com/AugustusW/audio-tldr-skill), MIT © AugustusW. See the header
of `scripts/source.py`.

---

> A recording worth hearing is worth hearing at one volume.
