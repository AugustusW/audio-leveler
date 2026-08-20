"""量測層：把 ffmpeg 的輸出變成數字。這個模組不做任何決定。

判斷（該用哪條濾鏡、要不要處理）一律在 SKILL.md 交給宿主 LLM，見 spec ADR-4。
"""
import math
import statistics

_S_PREFIX = "lavfi.r128.S="
_PTS_MARKER = "pts_time:"


def parse_short_term(stdout):
    """ametadata 的 stdout -> [(pts_time, short_term_LUFS), ...]

    格式為兩行一組：先 `frame:N    pts:...    pts_time:T`，再 `lavfi.r128.S=<值>`。
    只有看到 S 行才收一筆，所以缺 pts_time 的孤兒 S 行會沿用上一個時間。
    """
    out = []
    t = 0.0
    for line in stdout.splitlines():
        if _PTS_MARKER in line:
            try:
                t = float(line.split(_PTS_MARKER, 1)[1].split()[0])
            except (IndexError, ValueError):
                continue
        elif line.startswith(_S_PREFIX):
            try:
                out.append((t, float(line[len(_S_PREFIX):].strip())))
            except ValueError:
                continue
    return out


ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = 20.0
DEFAULT_WINDOW_SEC = 360.0


class InsufficientSignal(Exception):
    """全片皆靜音或樣本不足，無法產生有意義的診斷。"""


def gate(samples, integrated):
    """濾掉靜音樣本。

    閘門為 max(-70, I - 20)：-70 是 EBU R128 的絕對閘，擋數位靜音；I - 20 對齊
    R128 算 integrated 時的 relative gate。

    用相對閘而不是固定 -40 是刻意的。固定閘會讓「處理前 vs 處理後」的統計母體
    不同——apply 把整體響度拉高後，原本在閘下的樣本會升上來進入母體，比出來的
    數字因此失真（實測：同一支素材固定閘給 8.8 -> 11.8「變差了」，相對閘給
    11.8 -> 11.8「沒效果」，後者才是真的）。
    """
    if math.isinf(integrated):
        floor = ABSOLUTE_GATE_LUFS
    else:
        floor = max(ABSOLUTE_GATE_LUFS, integrated - RELATIVE_GATE_LU)
    return [(t, v) for t, v in samples if v > floor]


def percentile(values, q):
    """線性內插百分位。

    標準庫沒有直接可用的：statistics.quantiles 回傳的是切點，不含 0 與 100，
    而 spread 正是 p95 - p5。
    """
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def window_stats(samples, window_sec=DEFAULT_WINDOW_SEC):
    """把樣本切成固定長度的窗，每個窗回報中位數與極值。空窗不回報。"""
    buckets = {}
    for t, v in samples:
        buckets.setdefault(int(t // window_sec), []).append(v)
    out = []
    for idx in sorted(buckets):
        vals = buckets[idx]
        out.append({
            "start_sec": idx * window_sec,
            "median": statistics.median(vals),
            "min": min(vals),
            "max": max(vals),
        })
    return out


def build_diagnosis(samples, integrated, lra, duration, channels, dual_mono,
                    window_sec=DEFAULT_WINDOW_SEC):
    """組裝診斷契約。純數字，不含任何決定——判斷在 SKILL.md。"""
    kept = gate(samples, integrated)
    if len(kept) < 2:
        raise InsufficientSignal(
            "not enough non-silent samples to diagnose "
            "(material may be silent, too short, or corrupt)")
    values = [v for _, v in kept]
    pcts = {name: percentile(values, q)
            for name, q in (("p5", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))}
    windows = window_stats(kept, window_sec)
    medians = [w["median"] for w in windows]
    intras = [w["max"] - w["min"] for w in windows]
    return {
        "duration_sec": duration,
        "channels": channels,
        "integrated_lufs": integrated,
        "lra_lu": lra,
        "spread_lu": pcts["p95"] - pcts["p5"],
        "drift_lu": (max(medians) - min(medians)) if medians else 0.0,
        "intra_lu": statistics.median(intras) if intras else 0.0,
        "percentiles": pcts,
        "windows": windows,
        "dual_mono": dual_mono,
        "speech_ratio": len(kept) / len(samples) if samples else 0.0,
    }
