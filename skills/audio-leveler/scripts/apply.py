"""執行層：依**明確指定的**濾鏡與參數組 filtergraph 並執行。

這個模組不做任何決定——不猜濾鏡、不猜要不要處理。判斷在 SKILL.md（spec ADR-4）。
"""
import json
import math
import os
import re
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
    return float(first_pass["input_i"]) + (target_tp - float(first_pass["input_tp"]))


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

FILTERS = {
    # 段落內快速起伏（麥克風距離變化、多人音量差）。EP13 實測有效。
    "speech": "speechnorm=e=12.5:r=0.0001:l=1:p=0.95",
    # 跨段漂移（分次錄製、換場景）。g=301 x f=500ms 約 2.5 分鐘的高斯窗，刻意遠長於
    # 「調兇沒用」實測的那組（g=31，約 15 秒）。這條分支尚未有真實素材驗證。
    "segmented": "dynaudnorm=g=301:f=500:m=10:p=0.95",
    # 只是整體音量不對，兩段式 loudnorm 就夠。
    "loudness": None,
}


def build_chain(filter_name, *, mono, loudnorm_args):
    if filter_name not in FILTERS:
        raise ValueError("unknown filter '{0}' (choose from {1})".format(
            filter_name, ", ".join(sorted(FILTERS))))
    parts = []
    if mono:
        parts.append(MONO_FILTER)
    pre = FILTERS[filter_name]
    if pre:
        parts.append(pre)
    parts.append("loudnorm={0}".format(loudnorm_args))
    return ",".join(parts)


def measure_pass_args(target_lufs=TARGET_LUFS, target_tp=TARGET_TP):
    """第一段只量測。這一段回報的 normalization_type 永遠是 dynamic，不可拿來判斷。"""
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


def level(path, filter_name, out_path, *, mono, target_lufs=TARGET_LUFS,
          target_tp=TARGET_TP, force=False):
    """兩段式執行：先量測、再以 linear=true 套固定增益。回傳執行摘要。

    這個函式不決定 filter_name 也不決定 mono——兩者都由呼叫端明確給定。
    """
    out_path = Path(out_path)
    if not force:
        refuse_if_taken(out_path)
    measure.require_tool("ffmpeg")

    chain1 = build_chain(filter_name, mono=mono,
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
    chain2 = build_chain(filter_name, mono=mono,
                         loudnorm_args=apply_pass_args(first, target_lufs, target_tp))
    # 副檔名留在最後一段沒有用：ffmpeg 由檔名推 muxer，所以下面明確帶 -f mp3
    partial = out_path.with_name(out_path.name + ".partial")
    try:
        second = parse_loudnorm_json(_run(
            ["ffmpeg", "-y", "-nostats", "-hide_banner", "-i", str(path),
             "-af", chain2, "-c:a", "libmp3lame", "-b:a", output_bitrate(mono),
             "-f", "mp3", str(partial)]).stderr)
        verify_linear(second)
        os.replace(partial, out_path)
    finally:
        if partial.exists():
            partial.unlink()
    return {
        "filter": filter_name,
        "mono": mono,
        "target_lra": loudnorm_target_lra(float(first["input_lra"])),
        "normalization_type": second["normalization_type"],
        "first_pass": first,
        "second_pass": second,
        "output_path": str(out_path),
    }
