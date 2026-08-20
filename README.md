# audio-leveler

Fix speech audio whose volume keeps going up and down — podcasts, lectures,
interviews, meeting recordings.

The value here is not knowing an ffmpeg incantation. It is **measuring before
choosing**, because picking the wrong filter does nothing at all.

The episode this tool was built from measured `-16.8 LUFS` integrated: already
exactly the podcast convention. Every copy-paste `loudnorm` recipe on the
internet adjusts that number, so every one of them left the episode exactly as
unlistenable as before. What was actually wrong lived one level down, in how far
the loudness moved *within* each section.

## Install

Needs [ffmpeg](https://ffmpeg.org/) (which brings `ffprobe`), and
[yt-dlp](https://github.com/yt-dlp/yt-dlp) only if you pass URLs.

```bash
brew install ffmpeg          # macOS; apt install ffmpeg on Debian/Ubuntu
pip install yt-dlp           # optional, for URL sources
```

Nothing is installed for you. If a tool is missing you get told which one and
how to get it.

## Use

```bash
python3 skills/audio-leveler/scripts/cli.py measure <source>
python3 skills/audio-leveler/scripts/cli.py apply <source> --filter speech
```

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

| Filter | For | What it does |
|---|---|---|
| `speech` | `intra` > `drift`: movement inside sections | `speechnorm`, then two-pass linear `loudnorm` |
| `segmented` | `drift` > `intra`: sections at different levels | `dynaudnorm` over a ~2.5 minute window, then two-pass linear `loudnorm` |
| `loudness` | both small, level simply wrong | two-pass linear `loudnorm` only |

Every render finishes by measuring the output again and reporting one of three
outcomes: converged by N%, essentially unchanged, or got worse. An unchanged or
worse result is stated as such — the tool does not describe its own output as a
success without evidence.

Other flags: `--out PATH`, `--target-lufs` (default −16), `--mono
auto|force|never`, `--force` to overwrite.

## As a skill

`skills/audio-leveler/SKILL.md` carries the guidance for reading the numbers.
The scripts hold no judgement at all: `measure.py` produces numbers, `apply.py`
executes explicit parameters, and deciding what the numbers mean happens in
between. That split is why the thresholds are guidance rather than constants —
they came from a small number of recordings, and freezing them in code would
freeze those recordings' characteristics along with them.

## Known limits

- **Speech only.** In music, dynamic range is the intent, not a defect.
- **No noise reduction** in this version.
- `--filter` has no automatic mode; see above.
- **`segmented` does not yet work well.** On a synthetic 18 LU step between two
  halves, none of ffmpeg's stock dynamics filters helped much: dynaudnorm
  reached 16.4 LU, compand 16.9, speechnorm 17.5, against 18.5 unprocessed.
  Correcting a level difference that large needs true segmented processing —
  detect the boundary, normalise each part, concatenate — which this version does
  not do. The branch is kept because `apply` always re-measures and will report
  that nothing improved, but do not expect it to fix drifting material yet.
- The `speech` branch has been verified against one real recording (spread
  10.0 → 5.8 LU, confirmed by ear).
- `drift` needs more than one window to mean anything. The window scales with
  duration, but a source shorter than about two minutes still yields few windows.
- Dual-mono detection is whole-file. A recording whose body is dual mono but
  whose intro is real stereo reports the low separation of the intro, which is
  the safe answer but not a precise one.
- More than two channels: the dual-mono check is skipped. `--mono force` still
  downmixes correctly using the layout's own coefficients.
- The download cache in `~/.cache/audio-leveler` is never pruned.

## License

MIT, see [LICENSE](LICENSE).

The source-resolution layer (Apple Podcasts lookup, yt-dlp download, cache keys)
is ported from [audio-tldr-skill](https://github.com/AugustusW/audio-tldr-skill),
MIT © AugustusW. See the header of `scripts/source.py`.
