import pytest

import apply


PASS1_STDERR = """[Parsed_loudnorm_2 @ 0x920c0d140] 
{
\t"input_i" : "-9.48",
\t"input_tp" : "-4.37",
\t"input_lra" : "20.00",
\t"input_thresh" : "-23.83",
\t"output_i" : "-16.26",
\t"output_tp" : "-11.02",
\t"output_lra" : "17.70",
\t"output_thresh" : "-28.86",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.26"
}
[out#0/null @ 0x920c0c840] video:0KiB audio:2250KiB
"""


def test_loudnorm_target_lra_floors_at_five():
    # 素材壓得很平時目標仍不得低於 5，否則第二段會靜默退回 dynamic
    assert apply.loudnorm_target_lra(3.1) == 5.0
    assert apply.loudnorm_target_lra(0.0) == 5.0


def test_loudnorm_target_lra_follows_the_measurement():
    assert apply.loudnorm_target_lra(5.10) == 5.10
    assert apply.loudnorm_target_lra(20.00) == 20.00


def test_loudnorm_target_lra_clamps_to_ffmpeg_maximum():
    # ffmpeg loudnorm 的 LRA 上限是 50，超過會被 ffmpeg 直接拒絕
    assert apply.loudnorm_target_lra(99.0) == 50.0


def test_parse_loudnorm_json_reads_the_last_block():
    d = apply.parse_loudnorm_json(PASS1_STDERR)
    assert d["input_i"] == "-9.48"
    assert d["input_lra"] == "20.00"
    assert d["normalization_type"] == "dynamic"


def test_parse_loudnorm_json_raises_when_absent():
    with pytest.raises(apply.LoudnormParseError):
        apply.parse_loudnorm_json("ffmpeg said nothing useful")


def test_verify_linear_accepts_linear():
    apply.verify_linear({"normalization_type": "linear"})


def test_verify_linear_rejects_silent_fallback_to_dynamic():
    # ffmpeg 不報錯、不警告，直接改用 dynamic —— 這正是要擋的
    with pytest.raises(apply.LinearModeLost) as e:
        apply.verify_linear({"normalization_type": "dynamic", "input_lra": "8.0"})
    assert "dynamic" in str(e.value)


def test_second_pass_derived_from_first_pass_is_linear_by_construction():
    """回歸守衛：只要目標 LRA 由 loudnorm_target_lra 推導，linear 的前提就成立。

    ffmpeg 的規則是「目標 LRA >= 實測 LRA」才可能 linear。實測反例：
    measured_LRA=20.00 時 LRA=20 得 linear、LRA=3 得 dynamic。
    """
    measured_lra = 20.00
    assert apply.loudnorm_target_lra(measured_lra) >= measured_lra


def test_filters_cover_exactly_the_three_documented_branches():
    assert set(apply.FILTERS) == {"speech", "segmented", "loudness"}


def test_speech_branch_uses_the_measured_speechnorm_parameters():
    # e/r/l/p 四個都刻意偏離 ffmpeg 原廠值，理由見 spec「預設參數」一節
    assert apply.FILTERS["speech"] == "speechnorm=e=12.5:r=0.0001:l=1:p=0.95"


def test_loudness_branch_adds_no_pre_filter():
    assert apply.FILTERS["loudness"] is None


def test_build_chain_puts_mono_first():
    """單聲道必須在最前面：loudnorm 的 true-peak 超取樣成本隨聲道數，
    先降為單聲道讓 EP13 的整條鏈由 83 秒降到 52 秒。"""
    chain = apply.build_chain("speech", mono=True, loudnorm_args="I=-16")
    assert chain.startswith("pan=mono|c0=0.5*c0+0.5*c1,")
    assert chain == ("pan=mono|c0=0.5*c0+0.5*c1,"
                     "speechnorm=e=12.5:r=0.0001:l=1:p=0.95,loudnorm=I=-16")


def test_build_chain_without_mono_omits_the_pan():
    chain = apply.build_chain("speech", mono=False, loudnorm_args="I=-16")
    assert "pan=" not in chain
    assert chain == "speechnorm=e=12.5:r=0.0001:l=1:p=0.95,loudnorm=I=-16"


def test_build_chain_loudness_branch_is_loudnorm_only():
    assert apply.build_chain("loudness", mono=False, loudnorm_args="I=-16") == "loudnorm=I=-16"


def test_build_chain_rejects_unknown_filter():
    with pytest.raises(ValueError):
        apply.build_chain("magic", mono=False, loudnorm_args="I=-16")


def test_measure_pass_args_requests_json():
    args = apply.measure_pass_args()
    assert "print_format=json" in args
    assert "I=-16.0" in args and "TP=-1.5" in args
    assert "measured_" not in args     # 第一段不得帶 measured_*


def test_apply_pass_args_carries_every_measured_value_and_linear():
    first = {"input_i": "-9.48", "input_tp": "-4.37", "input_lra": "20.00",
             "input_thresh": "-23.83", "target_offset": "0.26"}
    args = apply.apply_pass_args(first)
    for expected in ("measured_I=-9.48", "measured_TP=-4.37", "measured_LRA=20.00",
                     "measured_thresh=-23.83", "offset=0.26", "linear=true",
                     "print_format=json", "LRA=20.0"):
        assert expected in args, expected


def test_apply_pass_args_lifts_lra_to_the_floor_for_flat_material():
    first = {"input_i": "-16.0", "input_tp": "-3.0", "input_lra": "2.0",
             "input_thresh": "-27.0", "target_offset": "0.0"}
    assert "LRA=5.0" in apply.apply_pass_args(first)
