"""執行層：依**明確指定的**濾鏡與參數組 filtergraph 並執行。

這個模組不做任何決定——不猜濾鏡、不猜要不要處理。判斷在 SKILL.md（spec ADR-4）。
"""
import json
import re

TARGET_LUFS = -16.0
TARGET_TP = -1.5
LRA_FLOOR = 5.0
LRA_CEILING = 50.0          # ffmpeg loudnorm 的 LRA 上限

_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)


class LoudnormParseError(Exception):
    """loudnorm 沒有吐出可解析的 JSON。"""


class LinearModeLost(Exception):
    """第二段 loudnorm 靜默退回 dynamic —— 正是本專案要避開的抽送感來源。"""


def loudnorm_target_lra(measured_lra, floor=LRA_FLOOR, ceiling=LRA_CEILING):
    """由實測 LRA 推導第二段的目標 LRA。

    禁止硬編：`linear=true` 只在「目標 LRA >= 實測 LRA」且套用後不超過目標 TP 時
    成立，任一條件不滿足 ffmpeg 就不報錯地改用 dynamic。硬編一個數字只是在某一支
    素材上碰巧成立。
    """
    return min(max(measured_lra, floor), ceiling)


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


MONO_FILTER = "pan=mono|c0=0.5*c0+0.5*c1"

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
