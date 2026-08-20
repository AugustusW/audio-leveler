#!/usr/bin/env python3
"""audio-leveler CLI：measure（量測）與 apply（執行）兩個子指令。

刻意拆成兩個子指令而不是一支帶 --dry-run 的指令：判斷發生在兩者之間，由宿主
LLM 讀 measure 的數字後決定要不要 apply、用哪條濾鏡。
"""
import argparse
import json
import sys
from pathlib import Path

import apply
import measure
import source

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_MISSING_TOOL = 3
EXIT_NO_SIGNAL = 4
EXIT_FFMPEG = 5
EXIT_LINEAR_LOST = 6
EXIT_OUTPUT_EXISTS = 7


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


def _resolve_source(spec):
    """-> (本地檔路徑, 是否來自 URL)

    URL 下載到快取目錄，但輸出**不會**寫在那裡——見 cmd_apply 的輸出路徑。
    """
    if source.is_url(spec):
        path, _ = source.fetch(spec)
        return path, True
    path = Path(spec)
    if not path.exists():
        raise FileNotFoundError("source not found: {0}".format(spec))
    return str(path), False


def cmd_measure(args):
    path, _ = _resolve_source(args.source)
    diag = measure.diagnose(path)
    print(json.dumps(diag, indent=2) if args.json else format_report(diag))
    return EXIT_OK


def _verdict(delta):
    """三種結局要講成三句話。把「幾乎沒動」講成「converged 0%」讀起來像成功。"""
    if delta["improved"]:
        return "converged {0:.0f}%".format(delta["converged_pct"])
    if delta["delta_lu"] >= measure.MIN_MEANINGFUL_LU:
        return ("the spread got worse ({0:+.1f} LU). The source is better left as it is, "
                "or try a different filter.".format(delta["delta_lu"]))
    return ("the spread is essentially unchanged. This filter did not address what is "
            "wrong with this source; consider a different one.")


def format_comparison(result, before, after, delta):
    lines = [
        "Filter: {0}{1}".format(result["filter"],
                                " (downmixed to mono)" if result["mono"] else ""),
        "loudnorm: linear, target LRA {0:.1f}".format(result["target_lra"]),
        "",
        "Short-term spread (p95 - p5):",
        "  before: {0:.1f} LU".format(delta["before_lu"]),
        "  after:  {0:.1f} LU".format(delta["after_lu"]),
    ]
    lines.append("  " + _verdict(delta))
    lines += [
        "",
        "Integrated loudness: {0:.1f} -> {1:.1f} LUFS".format(
            before["integrated_lufs"], after["integrated_lufs"]),
        "Output: {0}".format(result["output_path"]),
    ]
    return "\n".join(lines)


def cmd_apply(args):
    path, from_url = _resolve_source(args.source)
    before = measure.diagnose(path)
    out_path = (Path(args.out) if args.out
                else apply.default_output_path(path, from_url=from_url))
    mono = {"auto": before["dual_mono"], "force": True, "never": False}[args.mono]
    try:
        result = apply.level(path, args.filter, out_path, mono=mono,
                             target_lufs=args.target_lufs, force=args.force)
    except apply.LinearModeLost:
        Path(out_path).unlink(missing_ok=True)
        raise
    after = measure.diagnose(result["output_path"])
    print(format_comparison(result, before, after, measure.improvement(before, after)))
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="audio-leveler",
        description="Measure loudness inconsistency in speech audio, then fix it.")
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure", help="measure only; print a diagnosis")
    m.add_argument("source", help="local file, an Apple Podcasts episode link, or any URL yt-dlp can fetch")
    m.add_argument("--json", action="store_true",
                   help="emit the diagnosis contract as JSON for an LLM or a script")
    m.set_defaults(func=cmd_measure)

    a = sub.add_parser("apply", help="apply an explicitly chosen filter, then re-measure")
    a.add_argument("source", help="local file, an Apple Podcasts episode link, or any URL yt-dlp can fetch")
    a.add_argument("--filter", required=True, choices=sorted(apply.FILTERS),
                   help="which filter to apply; there is deliberately no 'auto' — "
                        "without an LLM this tool does not guess")
    a.add_argument("--out", help="output path (default: <source>-leveled.mp3 next to the source)")
    a.add_argument("--target-lufs", type=float, default=apply.TARGET_LUFS,
                   help="integrated loudness target (default -16, the podcast convention)")
    a.add_argument("--mono", choices=["auto", "force", "never"], default="auto",
                   help="auto downmixes only when the source is detected as dual mono")
    a.add_argument("--force", action="store_true", help="overwrite an existing output file")
    a.set_defaults(func=cmd_apply)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except source.DownloadError as e:
        print(str(e), file=sys.stderr)
        return EXIT_BAD_INPUT
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return EXIT_BAD_INPUT
    except measure.MissingTool as e:
        print(str(e), file=sys.stderr)
        return EXIT_MISSING_TOOL
    except measure.InsufficientSignal as e:
        print(str(e), file=sys.stderr)
        return EXIT_NO_SIGNAL
    except apply.LinearModeLost as e:
        print(str(e), file=sys.stderr)
        return EXIT_LINEAR_LOST
    except apply.OutputExists as e:
        print(str(e), file=sys.stderr)
        return EXIT_OUTPUT_EXISTS
    except apply.LoudnormParseError as e:
        print(str(e), file=sys.stderr)
        return EXIT_FFMPEG
    except measure.FfmpegError as e:
        print(str(e), file=sys.stderr)
        return EXIT_FFMPEG


if __name__ == "__main__":
    sys.exit(main())
