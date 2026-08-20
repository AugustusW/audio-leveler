"""量測層：把 ffmpeg 的輸出變成數字。這個模組不做任何決定。

判斷（該用哪條濾鏡、要不要處理）一律在 SKILL.md 交給宿主 LLM，見 spec ADR-4。
"""
import json
import math
import shutil
import statistics
import subprocess

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


FFMPEG_TIMEOUT_SEC = 3600
PROBE_TIMEOUT_SEC = 120

INSTALL_HINT = {
    "ffmpeg": "install with: brew install ffmpeg (macOS) or apt install ffmpeg (Debian/Ubuntu)",
    "ffprobe": "ffprobe ships with ffmpeg — brew install ffmpeg (macOS) or apt install ffmpeg",
}


class MissingTool(Exception):
    """必要的外部工具不在 PATH 上。本專案不自動安裝。"""


class FfmpegError(Exception):
    """ffmpeg / ffprobe 執行失敗，或輸出不是預期的形狀。"""


def require_tool(name):
    path = shutil.which(name)
    if not path:
        raise MissingTool("{0} not found — {1}".format(name, INSTALL_HINT.get(name, "")))
    return path


def _to_float(token):
    try:
        return float(token)
    except ValueError:
        return None


def parse_ebur128_summary(stderr):
    """ebur128 的 stderr summary -> (integrated_LUFS, LRA_LU)

    'LRA low:' 與 'LRA high:' 也以 LRA 開頭，所以比對整個 token 而非前綴。
    """
    integrated = lra = None
    for line in stderr.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        if parts[0] == "I:":
            integrated = _to_float(parts[1])
        elif parts[0] == "LRA:":
            lra = _to_float(parts[1])
    if integrated is None or lra is None:
        raise FfmpegError("ebur128 summary not found in ffmpeg output")
    return integrated, lra


def probe_audio(path):
    """(聲道數, 長度秒)。"""
    require_tool("ffprobe")
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels,duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC)
    if p.returncode != 0:
        raise FfmpegError("ffprobe failed: {0}".format(p.stderr.strip()[:300]))
    try:
        streams = json.loads(p.stdout).get("streams") or []
    except ValueError as e:
        raise FfmpegError("ffprobe returned unparsable JSON: {0}".format(e))
    if not streams:
        raise FfmpegError("no audio stream found in {0}".format(path))
    s = streams[0]
    return int(s.get("channels") or 0), float(s.get("duration") or 0.0)


def run_ebur128(path):
    """跑一趟 ebur128，同時取回 short-term 序列與 integrated / LRA。

    metadata 走 stdout（ametadata file=-），summary 走 stderr——兩者不互相污染，
    所以一趟就夠，不需要為了 integrated 再跑一次。
    """
    require_tool("ffmpeg")
    p = subprocess.run(
        ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path),
         "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.S:file=-",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC)
    if p.returncode != 0:
        raise FfmpegError("ffmpeg ebur128 failed: {0}".format(p.stderr.strip()[-500:]))
    integrated, lra = parse_ebur128_summary(p.stderr)
    return parse_short_term(p.stdout), integrated, lra


DUAL_MONO_MARGIN_DB = 60.0
_RMS_PREFIX = "lavfi.astats.Overall.RMS_level="

_DIFF_FILTER = "pan=mono|c0=0.5*c0-0.5*c1"
_SUM_FILTER = "pan=mono|c0=0.5*c0+0.5*c1"
_ASTATS_TAIL = ("astats=metadata=1:reset=0,"
                "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")


def parse_astats_rms(stdout):
    """astats 的 ametadata 輸出 -> 最後一筆 Overall RMS（dBFS）。

    走 ametadata 而不是 grep astats 的 log 行，是因為 log 行帶
    `[Parsed_astats_1 @ 0x...]` 前綴，位址每次執行都不一樣。
    """
    value = None
    for line in stdout.splitlines():
        if line.startswith(_RMS_PREFIX):
            value = _to_float(line[len(_RMS_PREFIX):].strip())
    if value is None:
        raise FfmpegError("astats RMS not found in ffmpeg output")
    return value


def is_dual_mono(diff_rms_db, program_rms_db, margin_db=DUAL_MONO_MARGIN_DB):
    """差訊號比節目低 margin_db 以上 -> 判為假立體聲。

    用 L-R 差訊號而非比對兩聲道 RMS：RMS 是能量統計量，延遲一個聲道不改變能量，
    所以明確可聽的立體聲兩聲道 RMS 也可能只差 0.0012 dB。任何寬到足以容忍有損
    編碼誤差的容差，都會把真立體聲一起吞掉。

    留 60 dB 而不要求 -inf，是因為來源若曾經有損編碼或重取樣，兩聲道可能不再
    位元相同，但差異仍遠低於可聞範圍。
    """
    if math.isinf(diff_rms_db):
        return True
    if math.isinf(program_rms_db):
        return False
    return (program_rms_db - diff_rms_db) >= margin_db


def _astats_rms(path, pan_filter):
    p = subprocess.run(
        ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path),
         "-af", "{0},{1}".format(pan_filter, _ASTATS_TAIL), "-f", "null", "-"],
        capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SEC)
    if p.returncode != 0:
        raise FfmpegError("ffmpeg astats failed: {0}".format(p.stderr.strip()[-500:]))
    return parse_astats_rms(p.stdout)


def detect_dual_mono(path, channels):
    """只有雙聲道才需要偵測。單聲道無從談起；超過兩聲道 v0.1.0 不處理。"""
    if channels != 2:
        return False
    require_tool("ffmpeg")
    return is_dual_mono(_astats_rms(path, _DIFF_FILTER), _astats_rms(path, _SUM_FILTER))


def diagnose(path, window_sec=DEFAULT_WINDOW_SEC):
    """量測一支素材，回傳診斷契約。這個函式不做任何決定。"""
    channels, duration = probe_audio(path)
    samples, integrated, lra = run_ebur128(path)
    dual_mono = detect_dual_mono(path, channels)
    return build_diagnosis(samples, integrated, lra, duration, channels, dual_mono, window_sec)
