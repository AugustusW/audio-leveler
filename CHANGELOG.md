# Changelog

## 0.1.0 — 2026-08-20

First release.

- `measure`: reports short-term loudness spread, drift between windows, and
  intra-window swing, plus percentiles, integrated loudness and a dual-mono
  verdict. `--json` emits the contract for a model or a script to read.
- `apply`: renders with an explicitly chosen filter (`speech`, `segmented`, or
  `loudness`), always through a two-pass linear `loudnorm`, then re-measures the
  output and reports whether the spread actually converged.
- Sources: local files, Apple Podcasts episode links, and anything yt-dlp can
  fetch. Downloads are cached by episode identity, so `measure` followed by
  `apply` on the same link downloads once.
- Fake stereo is detected from the L−R difference signal and downmixed at the
  front of the filter chain, where it also cuts processing time.

### Fixed before release, from code review

- `--force` combined with a failed verification deleted the user's existing file
  and put nothing in its place. Renders now go to a temp file and only replace
  the target after verification.
- `--mono force` dropped the centre channel on layouts beyond stereo, producing
  silence for material whose dialogue sits in FC.
- `linear` has a third precondition: a measured loudness range of exactly 0 is
  ffmpeg's "not measured" sentinel and forces dynamic mode.
- The `--target-lufs` value suggested on failure was rounded rather than floored,
  so about half of them were themselves unreachable.
- `measure --json` emitted bare `Infinity` for dual mono, which is not valid JSON.
- `--target-lufs` accepted `nan` and `inf`.
- Duration read as zero for webm sources, where ffprobe reports it on the
  container rather than the stream.
- The statistics window is now scaled to the source length. At a fixed 6 minutes,
  any source under 12 minutes reported a drift of 0 by construction and was
  routed to the wrong filter.
- Mono output uses half the bitrate; 192 kbps is a two-channel budget.
