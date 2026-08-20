"""執行層：依**明確指定的**濾鏡與參數組 filtergraph 並執行。

這個模組不做任何決定——不猜濾鏡、不猜要不要處理。判斷在 SKILL.md（spec ADR-4）。
"""
import json
import math
import os
import re
import statistics
import subprocess
from pathlib import Path

import measure

TARGET_LUFS = -16.0
TARGET_TP = -1.5
LRA_FLOOR = 5.0
LRA_CEILING = 50.0          # ffmpeg loudnorm 的 LRA 上限

_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)


class LoudnormParseError(Exception):
    """loudnorm 沒有吐出可解析的 JSON。"""


class LinearNotPossible(Exception):
    """第一段量測就顯示 linear 不可能成立，還沒開始算就該停。"""


class LinearModeLost(Exception):
    """第二段 loudnorm 靜默退回 dynamic —— 正是本專案要避開的抽送感來源。"""


def loudnorm_target_lra(measured_lra, floor=LRA_FLOOR, ceiling=LRA_CEILING):
    """由實測 LRA 推導第二段的目標 LRA。

    禁止硬編：`linear=true` 只在「目標 LRA >= 實測 LRA」且套用後不超過目標 TP 時
    成立，任一條件不滿足 ffmpeg 就不報錯地改用 dynamic。硬編一個數字只是在某一支
    素材上碰巧成立。
    """
    return min(max(measured_lra, floor), ceiling)


def max_linear_target_lufs(first_pass, target_tp=TARGET_TP):
    """在不超過目標 true peak 的前提下，linear 模式最高能拉到幾 LUFS。

    linear 是整檔套一個固定增益，所以 true peak 跟著整體響度一起走：需要的增益是
    `target_I - input_i`，套完的峰值就是 `input_tp + 增益`。要它不超過 target_tp，
    目標響度的上限即為 `input_i + (target_tp - input_tp)`。

    這只是三個前提中的一個，見 linear_is_possible。
    """
    input_i = float(first_pass["input_i"])
    input_tp = float(first_pass["input_tp"])
    if not (math.isfinite(input_i) and math.isfinite(input_tp)):
        raise LinearNotPossible(
            "this source measures {0} LUFS at {1} dBTP, which leaves nothing to "
            "normalise against — it is most likely silent.".format(input_i, input_tp))
    return input_i + (target_tp - input_tp)


def suggested_target_lufs(first_pass, target_tp=TARGET_TP):
    """回報給使用者的建議目標：把上限往「更多餘裕」的方向取到 0.1 LUFS。

    直接 '%.1f' 會有一半機率捨進到上限之上，使用者照抄後撞上位元相同的錯誤訊息。
    負值往下取（更小 = 更安靜 = 更多 true peak 餘裕）。
    """
    return math.floor(max_linear_target_lufs(first_pass, target_tp) * 10.0) / 10.0


def linear_is_possible(first_pass, target_lufs=TARGET_LUFS, target_tp=TARGET_TP):
    """ffmpeg 的 linear 有**三**個前提，目標 LRA 只是其中一個。

    1. 目標 LRA >= 實測 LRA —— 由 loudnorm_target_lra 保證
    2. 套用增益後不超過目標 true peak —— 不受 LRA 推導控制。EP13 的單聲道路徑
       正是 LRA 過關、TP 不過關，ffmpeg 於是靜默改用 dynamic
    3. 實測 LRA 不得為 0 —— ffmpeg 把 0 當成「沒量到」的哨兵值。實測（其餘條件
       不變）：measured_LRA=0.00 得 dynamic、0.10 得 linear。完全平坦的素材
       （已限幅、極短片段、單音）會踩到

    spec 原本只把第 1 點寫成規定，第 2 點在實作期間爆掉，第 3 點在 code review
    期間爆掉。這裡集中成一個判斷，下一個被發現的前提就是加一行的事。
    """
    if float(first_pass["input_lra"]) == 0.0:
        return False
    return target_lufs <= max_linear_target_lufs(first_pass, target_tp) + 1e-9


def parse_loudnorm_json(stderr):
    """從 ffmpeg stderr 取出 loudnorm 的 JSON 區塊（print_format=json）。"""
    for raw in reversed(_JSON_BLOCK.findall(stderr)):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if "input_i" in data:
            return data
    raise LoudnormParseError("loudnorm JSON not found in ffmpeg output")


def verify_linear(second_pass):
    """第二段結束後必須驗證。dynamic 視為失敗，回報實際值而非默默輸出。"""
    kind = second_pass.get("normalization_type")
    if kind != "linear":
        raise LinearModeLost(
            "loudnorm fell back to '{0}' instead of linear — the output would have "
            "the pumping artefact this tool exists to avoid (measured LRA {1})".format(
                kind, second_pass.get("input_lra", "unknown")))


# pan=mono|c0=0.5*c0+0.5*c1 只引用 c0/c1：5.1 素材的對白在 FC，會被整個丟掉
# （實測輸出 RMS -inf）。aformat 讓 ffmpeg 依 layout 套用正確的降混係數，
# 且在 stereo 上與舊寫法輸出完全相同（實測皆 -33.045916）。
MONO_FILTER = "aformat=channel_layouts=mono"

SMOOTH_SPAN = 3
MAX_SEGMENT_GAIN_DB = 20.0

# 各階段的前置濾鏡。segmented 沒有固定字串——它的增益曲線是由素材自己的逐窗
# 響度算出來的，見 gain_curve。
FILTERS = {
    # 段落內快速起伏（麥克風距離變化、多人音量差）。EP13 實測 10.0 -> 5.8 LU。
    "speech": "speechnorm=e=12.5:r=0.0001:l=1:p=0.95",
    # 跨段漂移（分次錄製、換場景）。逐窗量測 -> 逐窗增益 -> 平滑 -> 隨時間變化的
    # volume 表達式。不切檔、不接檔，平滑本身就是交叉淡入。
    #
    # 現成的動態處理濾鏡在這裡幫不上忙，實測（18 LU 階梯素材，原始 spread 18.5）：
    # dynaudnorm 最好 16.4、compand 16.9、speechnorm 17.5。它們都被設計成溫和地
    # 移動增益，而持續的大幅增益變化正是它們定義中的抽送感。
    "segmented": None,
    # 只是整體音量不對，兩段式 loudnorm 就夠。
    "loudness": None,
}

# 前面什麼都不加，因此不能跟別的階段疊
_EXCLUSIVE_STAGES = {"loudness"}
# 需要由素材算出增益曲線的階段
_NEEDS_GAIN = "segmented"


def parse_stages(spec):
    """`--filter` 的值 -> 階段清單。接受逗號分隔的組合。

    分段增益修「段落之間」、speechnorm 修「段落之內」，兩者互補。實測（18 LU
    階梯素材）：單獨 34% / 5% 收斂，疊起來 96%。單選的介面結構上拿不到這個結果。
    """
    stages = [tok.strip() for tok in spec.split(",") if tok.strip()]
    if not stages:
        raise ValueError("no filter given")
    unknown = [st for st in stages if st not in FILTERS]
    if unknown:
        raise ValueError("unknown filter '{0}' (choose from {1})".format(
            unknown[0], ", ".join(sorted(FILTERS))))
    if len(stages) > 1:
        clash = [st for st in stages if st in _EXCLUSIVE_STAGES]
        if clash:
            raise ValueError(
                "'{0}' means no pre-filter at all, so it cannot be combined "
                "with other stages".format(clash[0]))
    if len(set(stages)) != len(stages):
        raise ValueError("each filter may appear only once: {0}".format(spec))
    return stages


def gain_curve(windows, smooth_span=SMOOTH_SPAN, max_gain_db=MAX_SEGMENT_GAIN_DB):
    """逐窗響度 -> [(起始秒, 增益 dB)]，已平滑並受上限約束。

    基準是**素材自己的中位數**，不是絕對目標響度。這個階段的工作是消除段落之間
    的落差；設定絕對音量是後面 loudnorm 的事。以絕對目標為基準會讓訊號在進
    loudnorm 之前就被整體推高、峰值餘裕耗盡，linear 因此變得不可能（實測：推完
    0.30 dBTP，-16 LUFS 已無法達成）。以自身中位數為基準時平均增益約為 0。

    平滑就是交叉淡入：窗與窗之間的增益不會硬跳，所以既不需要偵測邊界，也不需要
    把邊界對齊到靜音處。

    上限存在是因為極安靜的段落若照補，被放大的多半已經不是語音而是房間噪音。
    """
    if not windows:
        raise ValueError("gain_curve needs at least one window")
    reference = statistics.median([w["median"] for w in windows])
    raw = [reference - w["median"] for w in windows]
    half = max(0, (smooth_span - 1) // 2)
    smoothed = [statistics.mean(raw[max(0, i - half):i + half + 1])
                for i in range(len(raw))]
    return [(w["start_sec"], max(-max_gain_db, min(g, max_gain_db)))
            for w, g in zip(windows, smoothed)]


def build_volume_expression(curve, window_sec):
    """增益曲線 -> ffmpeg volume 濾鏡的表達式。

    增益在**窗中心之間線性內插**（以 dB 計），不是每個窗一個定值。分段常數的寫法
    會在窗邊界瞬間跳一階：實測 18 LU 的階梯素材，三點平滑後單階仍有 6 dB，而 6 dB
    的瞬間跳動聽得出來——那正是這個工具要消滅的東西。平滑讓階變小，只有內插能讓
    它變成斜坡。

    每個窗的代表時間取**窗中心**：增益是整個窗的統計量，掛在中心比掛在起點誠實。
    第一個窗中心之前與最後一個窗中心之後沒有東西可內插，維持定值。

    在 dB 上內插而不是在振幅上，因為聽感是對數的；最後才用 pow(10, dB/20) 換回
    振幅。整條表達式包在單引號內，ffmpeg 因此不會把裡面的逗號當成選項分隔。
    """
    if not curve:
        raise ValueError("build_volume_expression needs at least one point")
    points = [(start + window_sec / 2.0, db) for start, db in curve]

    if len(points) == 1:
        return _volume_filter("{0:.6f}".format(_amplitude(points[0][1])))

    expr = "{0:.6f}".format(points[-1][1])
    for i in range(len(points) - 2, -1, -1):
        t0, g0 = points[i]
        t1, g1 = points[i + 1]
        slope = (g1 - g0) / (t1 - t0)
        ramp = "{0:.6f}+{1:.6f}*(t-{2:.3f})".format(g0, slope, t0)
        expr = "if(lt(t,{0:.3f}),{1},{2})".format(t1, ramp, expr)
    expr = "if(lt(t,{0:.3f}),{1:.6f},{2})".format(points[0][0], points[0][1], expr)
    return _volume_filter("pow(10,({0})/20)".format(expr))


def _amplitude(db):
    return 10.0 ** (db / 20.0)


def _volume_filter(body):
    return "volume=volume='{0}':eval=frame".format(body)


def build_chain(stages, *, mono, loudnorm_args, gain_expression=None):
    """組出完整的 filtergraph。stages 依給定順序串接。

    單聲道一定在最前面：loudnorm 的 true-peak 超取樣成本隨聲道數，先降混讓 54
    分鐘的素材由 128 秒降到 71 秒。
    """
    if isinstance(stages, str):
        stages = parse_stages(stages)
    parts = []
    if mono:
        parts.append(MONO_FILTER)
    for stage in stages:
        if stage == _NEEDS_GAIN:
            if not gain_expression:
                raise ValueError(
                    "'{0}' needs a gain expression built from the source's own "
                    "per-window loudness".format(_NEEDS_GAIN))
            parts.append(gain_expression)
        elif FILTERS[stage]:
            parts.append(FILTERS[stage])
    parts.append("loudnorm={0}".format(loudnorm_args))
    return ",".join(parts)


def measure_pass_args(target_lufs=TARGET_LUFS, target_tp=TARGET_TP):
    """第一段只量測。這一段回報的 normalization_type 永遠是 dynamic，不可拿來判斷。

    LRA=7 是 ffmpeg 的原廠值，在這裡只是佔位：第一段的輸出被丟棄，量測值不受這個
    參數影響。真正要用的目標 LRA 由第一段的實測結果推導，見 loudnorm_target_lra。
    """
    return "I={0}:TP={1}:LRA=7:print_format=json".format(target_lufs, target_tp)


def apply_pass_args(first_pass, target_lufs=TARGET_LUFS, target_tp=TARGET_TP):
    lra = loudnorm_target_lra(float(first_pass["input_lra"]))
    return ("I={0}:TP={1}:LRA={2}:measured_I={3}:measured_TP={4}:measured_LRA={5}"
            ":measured_thresh={6}:offset={7}:linear=true:print_format=json").format(
        target_lufs, target_tp, lra,
        first_pass["input_i"], first_pass["input_tp"], first_pass["input_lra"],
        first_pass["input_thresh"], first_pass["target_offset"])


OUTPUT_SUFFIX = "-leveled.mp3"
OUTPUT_BITRATE_STEREO = "192k"
OUTPUT_BITRATE_MONO = "96k"


class UnsupportedOutputFormat(Exception):
    """輸出副檔名不在支援清單內。不猜、也不默默寫成別的格式。"""


class OutputExists(Exception):
    """輸出檔已存在。預設拒絕覆寫，需 --force。"""


def default_output_path(source, from_url, cwd=None):
    """本地來源輸出到來源同目錄；URL 來源輸出到當前目錄。"""
    stem = Path(source).stem
    base = Path(cwd) if cwd is not None else (Path.cwd() if from_url else Path(source).parent)
    return base / "{0}{1}".format(stem, OUTPUT_SUFFIX)


def output_bitrate(mono):
    """單聲道砍半。192 kbps 是兩聲道的預算，單聲道沿用等於每聲道加倍——檔案大一倍
    而聽感沒有變好。"""
    return OUTPUT_BITRATE_MONO if mono else OUTPUT_BITRATE_STEREO


# 副檔名 -> (ffmpeg codec, muxer, 是否有損)
_OUTPUT_FORMATS = {
    ".mp3": ("libmp3lame", "mp3", True),
    ".m4a": ("aac", "ipod", True),
    ".wav": ("pcm_s16le", "wav", False),
    ".flac": ("flac", "flac", False),
}


def encoder_args(out_path, mono):
    """依輸出副檔名決定編碼參數。

    副檔名要說實話：把 MP3 內容寫進 `.wav` 檔名，後續軟體會依副檔名判斷格式而
    出錯，而且使用者不會察覺。無損格式不帶位元率。

    muxer 一律明講而不讓 ffmpeg 從檔名推——實際寫入的是 `x.wav.partial` 這種
    暫存檔名，推不出來。
    """
    suffix = Path(out_path).suffix.lower()
    if suffix not in _OUTPUT_FORMATS:
        raise UnsupportedOutputFormat(
            "cannot write '{0}': supported output formats are {1}".format(
                suffix or "a file with no extension",
                ", ".join(sorted(k.lstrip(".") for k in _OUTPUT_FORMATS))))
    codec, muxer, lossy = _OUTPUT_FORMATS[suffix]
    args = ["-c:a", codec]
    if lossy:
        args += ["-b:a", output_bitrate(mono)]
    return args + ["-f", muxer]


def refuse_if_taken(out_path):
    """輸出檔已存在就拒絕。分成獨立函式，是為了讓呼叫端能在開工前先問一次。"""
    if Path(out_path).exists():
        raise OutputExists(
            "output already exists: {0} (pass --force to overwrite)".format(out_path))


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=measure.FFMPEG_TIMEOUT_SEC)
    if p.returncode != 0:
        raise measure.FfmpegError("ffmpeg failed: {0}".format(p.stderr.strip()[-500:]))
    return p


def level(path, filter_spec, out_path, *, mono, samples=None, duration_sec=None,
          target_lufs=TARGET_LUFS, target_tp=TARGET_TP, force=False):
    """兩段式執行：先量測、再以 linear=true 套固定增益。回傳執行摘要。

    這個函式不決定 filter_spec 也不決定 mono——兩者都由呼叫端明確給定。
    `segmented` 需要 samples（呼叫端 analyse() 的第二個回傳值）才能算增益曲線；
    那是機械運算（基準減實測），不是判斷。整形用的窗遠細於診斷窗，見
    measure.gain_window_sec。
    """
    stages = parse_stages(filter_spec)
    gain_expression = None
    if _NEEDS_GAIN in stages:
        if not samples:
            raise ValueError(
                "'{0}' needs the short-term samples from analyse()".format(_NEEDS_GAIN))
        win = measure.gain_window_sec(duration_sec or samples[-1][0])
        gain_expression = build_volume_expression(
            gain_curve(measure.window_stats(samples, win)), win)
    out_path = Path(out_path)
    encoder_args(out_path, mono)      # 副檔名不支援的話，在開工前就講
    if not force:
        refuse_if_taken(out_path)
    measure.require_tool("ffmpeg")

    chain1 = build_chain(stages, mono=mono, gain_expression=gain_expression,
                         loudnorm_args=measure_pass_args(target_lufs, target_tp))
    first = parse_loudnorm_json(_run(
        ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path),
         "-af", chain1, "-f", "null", "-"]).stderr)

    if float(first["input_lra"]) == 0.0:
        raise LinearNotPossible(
            "this source measures a loudness range of exactly 0 LU, which ffmpeg treats "
            "as 'not measured' and which forces dynamic normalisation. The material is "
            "already completely flat, so levelling has nothing to correct.")
    if not linear_is_possible(first, target_lufs, target_tp):
        raise LinearNotPossible(
            "linear normalisation to {0:.1f} LUFS is not possible for this source: it "
            "measures {1} LUFS at {2} dBTP, so the required gain would push the true peak "
            "past the {3:.1f} dBTP limit. The ceiling for linear is {4:.2f} LUFS — "
            "rerun with --target-lufs {5:.1f}.".format(
                target_lufs, first["input_i"], first["input_tp"], target_tp,
                max_linear_target_lufs(first, target_tp),
                suggested_target_lufs(first, target_tp)))

    # 先算到暫存檔，驗證通過才搬到目標位置。直接寫目標會在驗證失敗時毀掉既有檔案
    # ——`--force` 是同意「取代」，不是同意「失敗就刪掉」。順帶讓非 --force 的
    # 路徑也不怕中途中斷。
    chain2 = build_chain(stages, mono=mono, gain_expression=gain_expression,
                         loudnorm_args=apply_pass_args(first, target_lufs, target_tp))
    # 副檔名留在最後一段沒有用：ffmpeg 由檔名推 muxer，所以下面明確帶 -f mp3
    partial = out_path.with_name(out_path.name + ".partial")
    try:
        second = parse_loudnorm_json(_run(
            ["ffmpeg", "-y", "-nostats", "-hide_banner", "-i", str(path),
             "-af", chain2] + encoder_args(out_path, mono) + [str(partial)]).stderr)
        verify_linear(second)
        os.replace(partial, out_path)
    finally:
        if partial.exists():
            partial.unlink()
    return {
        "filter": filter_spec,
        "mono": mono,
        "target_lra": loudnorm_target_lra(float(first["input_lra"])),
        "normalization_type": second["normalization_type"],
        "first_pass": first,
        "second_pass": second,
        "output_path": str(out_path),
    }
