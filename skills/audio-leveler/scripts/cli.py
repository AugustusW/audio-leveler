#!/usr/bin/env python3
"""audio-leveler CLI：measure（量測）與 apply（執行）兩個子指令。

刻意拆成兩個子指令而不是一支帶 --dry-run 的指令：判斷發生在兩者之間，由宿主
LLM 讀 measure 的數字後決定要不要 apply、用哪條濾鏡。
"""
import argparse
import json
import sys
from pathlib import Path

import measure

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_MISSING_TOOL = 3
EXIT_NO_SIGNAL = 4
EXIT_FFMPEG = 5


def _fmt_duration(seconds):
    total = int(round(seconds))
    return "{0:d}:{1:02d}:{2:02d}".format(total // 3600, (total % 3600) // 60, total % 60)


def format_report(diag):
    p = diag["percentiles"]
    return "\n".join([
        "Source: {0}, {1} channel(s)".format(_fmt_duration(diag["duration_sec"]),
                                             diag["channels"]),
        "Integrated loudness: {0:.1f} LUFS (LRA {1:.1f} LU)".format(
            diag["integrated_lufs"], diag["lra_lu"]),
        "",
        "Short-term loudness (3s window, gated):",
        "  spread (p95 - p5): {0:.1f} LU".format(diag["spread_lu"]),
        "  drift  (between {0:.0f}-minute windows): {1:.1f} LU".format(
            measure.DEFAULT_WINDOW_SEC / 60.0, diag["drift_lu"]),
        "  intra  (within a window, median): {0:.1f} LU".format(diag["intra_lu"]),
        "  percentiles: p5 {0:.1f} / p25 {1:.1f} / p50 {2:.1f} / p75 {3:.1f} / p95 {4:.1f} LUFS"
        .format(p["p5"], p["p25"], p["p50"], p["p75"], p["p95"]),
        "  speech ratio: {0:.0%} of samples above the gate".format(diag["speech_ratio"]),
        "",
        "Channels: {0}".format(
            "dual mono (fake stereo) — safe to downmix to mono" if diag["dual_mono"]
            else "no dual-mono downmix (mono already, true stereo, or more than 2 channels)"),
    ])


def _resolve_local(source):
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError("source not found: {0}".format(source))
    return str(path)


def cmd_measure(args):
    diag = measure.diagnose(_resolve_local(args.source))
    print(json.dumps(diag, indent=2) if args.json else format_report(diag))
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="audio-leveler",
        description="Measure loudness inconsistency in speech audio, then fix it.")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure", help="measure only; print a diagnosis")
    m.add_argument("source", help="local audio or video file")
    m.add_argument("--json", action="store_true",
                   help="emit the diagnosis contract as JSON for an LLM or a script")
    m.set_defaults(func=cmd_measure)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return EXIT_BAD_INPUT
    except measure.MissingTool as e:
        print(str(e), file=sys.stderr)
        return EXIT_MISSING_TOOL
    except measure.InsufficientSignal as e:
        print(str(e), file=sys.stderr)
        return EXIT_NO_SIGNAL
    except measure.FfmpegError as e:
        print(str(e), file=sys.stderr)
        return EXIT_FFMPEG


if __name__ == "__main__":
    sys.exit(main())
