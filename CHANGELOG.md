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
