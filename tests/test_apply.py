import subprocess
from pathlib import Path

import pytest

import apply
import measure


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


def test_derived_target_lra_always_satisfies_the_lra_precondition():
    """回歸守衛：目標 LRA 由 loudnorm_target_lra 推導時，LRA 這個前提一定成立。

    ffmpeg 的規則是「目標 LRA >= 實測 LRA」。實測反例：measured_LRA=20.00 時
    LRA=20 得 linear、LRA=3 得 dynamic。

    注意這只是**兩個前提之一**，另一個是 true peak 的餘裕，見 linear_is_possible。
    EP13 的單聲道路徑正是 LRA 過關、TP 不過關。
    """
    for measured_lra in (0.0, 3.1, 5.0, 5.10, 20.00, 49.0):
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
    assert chain.startswith(apply.MONO_FILTER + ",")
    assert chain == ("aformat=channel_layouts=mono,"
                     "speechnorm=e=12.5:r=0.0001:l=1:p=0.95,loudnorm=I=-16")


def test_build_chain_without_mono_omits_the_pan():
    chain = apply.build_chain("speech", mono=False, loudnorm_args="I=-16")
    assert "aformat=" not in chain
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


def test_default_output_path_sits_next_to_a_local_source(tmp_path):
    src = tmp_path / "ep13.mp3"
    assert apply.default_output_path(str(src), from_url=False) == tmp_path / "ep13-leveled.mp3"


def test_default_output_path_keeps_the_stem_for_other_extensions(tmp_path):
    src = tmp_path / "talk.m4a"
    assert apply.default_output_path(str(src), from_url=False) == tmp_path / "talk-leveled.mp3"


def test_default_output_path_for_url_source_goes_to_cwd(tmp_path):
    got = apply.default_output_path("downloaded episode.mp3", from_url=True, cwd=tmp_path)
    assert got == tmp_path / "downloaded episode-leveled.mp3"


def test_level_refuses_to_overwrite_without_force(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "a-leveled.mp3"
    out.write_bytes(b"old")
    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    with pytest.raises(apply.OutputExists):
        apply.level(str(src), "speech", out, mono=False)


PASS1 = ('{"input_i" : "-9.48", "input_tp" : "-4.37", "input_lra" : "20.00", '
         '"input_thresh" : "-23.83", "normalization_type" : "dynamic", '
         '"target_offset" : "0.26"}')
PASS2 = ('{"input_i" : "-9.48", "input_tp" : "-5.16", "input_lra" : "17.90", '
         '"input_thresh" : "-22.08", "normalization_type" : "linear", '
         '"target_offset" : "-0.02"}')


def _is_measure_pass(cmd):
    return "-f" in cmd and cmd[cmd.index("-f") + 1] == "null"


def test_level_runs_two_passes_and_verifies_linear(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "a-leveled.mp3"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if _is_measure_pass(cmd):
            return subprocess.CompletedProcess(cmd, 0, "", PASS1)
        Path(cmd[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(cmd, 0, "", PASS2)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    result = apply.level(str(src), "speech", out, mono=True)

    assert len(calls) == 2
    assert _is_measure_pass(calls[0])          # 第一段丟棄輸出
    assert str(out) + ".partial" in calls[1]    # 第二段寫暫存檔，驗證過才搬過去
    assert out.read_bytes() == b"rendered"      # 搬完了
    assert result["normalization_type"] == "linear"
    assert result["target_lra"] == 20.0
    assert result["mono"] is True
    assert result["filter"] == "speech"


def test_level_measures_the_same_signal_it_renders(tmp_path, monkeypatch):
    """兩段的前置濾鏡必須一致：第一段量的就是第二段要套的訊號。"""
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if not _is_measure_pass(cmd):
            Path(cmd[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(cmd, 0, "", PASS1 if _is_measure_pass(cmd) else PASS2)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    apply.level(str(src), "speech", tmp_path / "o.mp3", mono=True)

    chain1 = calls[0][calls[0].index("-af") + 1]
    chain2 = calls[1][calls[1].index("-af") + 1]
    assert chain1.split(",loudnorm=")[0] == chain2.split(",loudnorm=")[0]


def test_level_raises_when_second_pass_falls_back_to_dynamic(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", PASS1))
    with pytest.raises(apply.LinearModeLost):
        apply.level(str(src), "speech", tmp_path / "o.mp3", mono=False)


def test_level_raises_ffmpeg_error_on_nonzero_exit(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "Invalid data"))
    with pytest.raises(measure.FfmpegError):
        apply.level(str(src), "speech", tmp_path / "o.mp3", mono=False)


def test_max_linear_target_lufs_is_bounded_by_true_peak_headroom():
    """EP13 單聲道路徑實測：input_i -16.73、input_tp -0.15。

    要拉到 -16.0 需要 +0.73 dB，true peak 會落在 +0.58 dBTP，遠高於 -1.5 的上限，
    所以 linear 不可能成立——而 ffmpeg 對此不報錯，直接改用 dynamic。
    """
    first = {"input_i": "-16.73", "input_tp": "-0.15"}
    assert apply.max_linear_target_lufs(first) == pytest.approx(-18.08, abs=0.01)


def test_max_linear_target_lufs_allows_headroom_when_peaks_are_low():
    first = {"input_i": "-24.0", "input_tp": "-12.0"}
    assert apply.max_linear_target_lufs(first) == pytest.approx(-13.5)


def test_linear_is_feasible_when_the_target_fits_the_headroom():
    first = {"input_i": "-24.0", "input_tp": "-12.0", "input_lra": "6.0"}
    assert apply.linear_is_possible(first, target_lufs=-16.0) is True


def test_linear_is_not_feasible_for_the_ep13_mono_case():
    first = {"input_i": "-16.73", "input_tp": "-0.15", "input_lra": "5.10"}
    assert apply.linear_is_possible(first, target_lufs=-16.0) is False


def test_level_fails_before_rendering_when_linear_cannot_hold(tmp_path, monkeypatch):
    """不可能成立時要在第一段之後就停，不要先花 90 秒算完再說。"""
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "a-leveled.mp3"
    calls = []
    body = ('{"input_i" : "-16.73", "input_tp" : "-0.15", "input_lra" : "5.10", '
            '"input_thresh" : "-26.87", "normalization_type" : "dynamic", '
            '"target_offset" : "0.0"}')

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", body)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    with pytest.raises(apply.LinearNotPossible) as e:
        apply.level(str(src), "speech", out, mono=True)

    assert len(calls) == 1              # 只跑了第一段
    assert not out.exists()
    assert "-18.1" in str(e.value)      # 告訴使用者最高可用的目標
    assert "--target-lufs" in str(e.value)


def test_mono_output_halves_the_bitrate():
    """192 kbps 是給兩聲道的預算。單聲道用同一個數字等於每聲道加倍，檔案白白大一倍
    而聽感沒有變好——8/20 手工版的 75MB -> 38MB 正是來自這裡。"""
    assert apply.output_bitrate(mono=True) == "96k"
    assert apply.output_bitrate(mono=False) == "192k"


def test_level_passes_the_mono_bitrate_to_ffmpeg(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if not _is_measure_pass(cmd):
            Path(cmd[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(cmd, 0, "", PASS1 if _is_measure_pass(cmd) else PASS2)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    apply.level(str(src), "speech", tmp_path / "o.mp3", mono=True)
    render = calls[1]
    assert render[render.index("-b:a") + 1] == "96k"


def test_mono_filter_handles_layouts_beyond_stereo():
    """pan=mono|c0=...c0+c1 只看前兩聲道：5.1 素材的對白在 FC，會被整個丟掉
    （實測輸出 RMS 為 -inf，全靜音）。aformat 讓 ffmpeg 套用該 layout 正確的
    降混係數，且在 stereo 上與舊寫法結果完全相同（實測皆 -33.045916）。"""
    assert apply.MONO_FILTER == "aformat=channel_layouts=mono"


def test_level_leaves_the_target_untouched_when_verification_fails(tmp_path, monkeypatch):
    """--force 是同意「取代」，不是同意「失敗就刪掉」。

    實測過的資料遺失：既有檔被 ffmpeg -y 覆寫，verify_linear 才失敗，CLI 再把它
    unlink——原檔沒了，也沒有東西補上。
    """
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "keepme.mp3"
    out.write_bytes(b"PRECIOUS")
    dynamic = ('{"input_i" : "-24.0", "input_tp" : "-12.0", "input_lra" : "6.0", '
               '"input_thresh" : "-34.0", "normalization_type" : "dynamic", '
               '"target_offset" : "0.0"}')

    def fake_run(cmd, **kwargs):
        if not _is_measure_pass(cmd):
            Path(cmd[-1]).write_bytes(b"rendered garbage")
        return subprocess.CompletedProcess(cmd, 0, "", dynamic)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    with pytest.raises(apply.LinearModeLost):
        apply.level(str(src), "speech", out, mono=False, force=True)
    assert out.read_bytes() == b"PRECIOUS"


def test_level_leaves_no_temp_file_behind_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "o.mp3"
    dynamic = ('{"input_i" : "-24.0", "input_tp" : "-12.0", "input_lra" : "6.0", '
               '"input_thresh" : "-34.0", "normalization_type" : "dynamic", '
               '"target_offset" : "0.0"}')

    def fake_run(cmd, **kwargs):
        if not _is_measure_pass(cmd):
            Path(cmd[-1]).write_bytes(b"garbage")
        return subprocess.CompletedProcess(cmd, 0, "", dynamic)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    with pytest.raises(apply.LinearModeLost):
        apply.level(str(src), "speech", out, mono=False)
    assert list(tmp_path.glob("*.partial")) == []
    assert not out.exists()


def test_suggested_target_is_always_feasible():
    """回報的建議值經過四捨五入後必須仍然可行。

    掃過 input_i x input_tp 的合理範圍，原本的 '%.1f' 有 13518 組會捨進到 ceiling
    之上，使用者照抄後撞上位元相同的錯誤訊息。EP13 (-18.08 -> -18.1) 剛好捨在
    安全的一側——又是單一樣本兩種做法給相同答案。
    """
    bad = []
    for i_i in [x / 100 for x in range(-3500, -500, 37)]:
        for i_tp in [x / 100 for x in range(-900, 0, 41)]:
            first = {"input_i": str(i_i), "input_tp": str(i_tp), "input_lra": "6.0"}
            suggestion = apply.suggested_target_lufs(first)
            if not apply.linear_is_possible(first, target_lufs=suggestion):
                bad.append((i_i, i_tp, suggestion))
    assert bad == [], bad[:5]


def test_suggested_target_rounds_toward_more_headroom():
    first = {"input_i": "-30.0", "input_tp": "-4.86", "input_lra": "6.0"}
    assert apply.max_linear_target_lufs(first) == pytest.approx(-26.64)
    assert apply.suggested_target_lufs(first) == -26.7      # 不是 -26.6


def test_linear_is_impossible_when_measured_lra_is_zero():
    """ffmpeg 把 measured_LRA=0 當成「沒量到」的哨兵值，一律走 dynamic。

    實測（其餘條件不變）：measured_LRA=0.00 -> dynamic、0.10 -> linear。
    完全平坦的素材（已限幅、極短片段、單音）會踩到，原本要跑完整段才被事後驗證抓到。
    """
    flat = {"input_i": "-24.0", "input_tp": "-12.0", "input_lra": "0.00"}
    assert apply.linear_is_possible(flat, target_lufs=-16.0) is False
    assert apply.linear_is_possible(dict(flat, input_lra="0.10"), target_lufs=-16.0) is True


def test_render_names_the_output_format_explicitly(tmp_path, monkeypatch):
    """暫存檔名讓 ffmpeg 推不出 muxer（'.mp3.partial' 不是已知副檔名），所以格式
    必須明講。這個 bug 是 e2e 測試抓到的——mock 測試全部照過。"""
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if not _is_measure_pass(cmd):
            Path(cmd[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(cmd, 0, "", PASS1 if _is_measure_pass(cmd) else PASS2)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    apply.level(str(src), "speech", tmp_path / "o.mp3", mono=False)
    render = calls[1]
    assert render[render.index("-f") + 1] == "mp3"


# 前 60 秒大聲、後 60 秒小聲，供增益整形使用
SAMPLES_STEP = ([(float(t), -33.0) for t in range(0, 60)] +
                [(float(t), -51.0) for t in range(60, 120)])

WINDOWS_STEP = [
    {"start_sec": 0.0, "median": -33.0, "min": -34.0, "max": -32.0},
    {"start_sec": 10.0, "median": -33.0, "min": -34.0, "max": -32.0},
    {"start_sec": 20.0, "median": -51.0, "min": -52.0, "max": -50.0},
    {"start_sec": 30.0, "median": -51.0, "min": -52.0, "max": -50.0},
]


def test_gain_curve_levels_windows_against_each_other_not_an_absolute_target():
    """分段增益的工作是消除段落之間的落差，不是設定絕對音量——後者是 loudnorm 的事。

    以絕對目標為基準會讓素材在進 loudnorm 之前就被推到目標值、峰值一起上去，
    餘裕耗盡，linear 就變得不可能（實測：推完 0.30 dBTP，-16 LUFS 已無法達成）。
    改以素材自己的中位數為基準後，平均增益約為 0，峰值幾乎不動。
    """
    curve = apply.gain_curve(WINDOWS_STEP, smooth_span=1, max_gain_db=100.0)
    gains = [g for _, g in curve]
    # 基準是中位數 -42：大聲的窗降 9 dB，安靜的窗升 9 dB
    assert [round(g, 1) for g in gains] == [-9.0, -9.0, 9.0, 9.0]
    assert abs(sum(gains)) < 1e-9          # 整體不淨增益


def test_gain_curve_is_capped_so_quiet_passages_do_not_lift_the_noise_floor():
    """+31 dB 會把底噪一起推上來。上限不是任意選的：超過這個幅度，被放大的多半
    已經不是語音而是房間噪音。"""
    curve = apply.gain_curve(WINDOWS_STEP, smooth_span=1, max_gain_db=5.0)
    assert max(g for _, g in curve) == 5.0
    assert min(g for _, g in curve) == -5.0


def test_gain_curve_smoothing_creates_the_ramp_across_the_step():
    """平滑本身就是交叉淡入——不需要偵測邊界，也不需要對齊到靜音處。"""
    curve = apply.gain_curve(WINDOWS_STEP, smooth_span=3, max_gain_db=100.0)
    gains = [g for _, g in curve]
    assert gains[0] < gains[1] < gains[2] < gains[3]      # 單調爬升，沒有硬跳
    assert max(gains[i + 1] - gains[i] for i in range(3)) < 18.0   # 原始落差是 18


def test_gain_curve_rejects_an_empty_window_list():
    with pytest.raises(ValueError):
        apply.gain_curve([])


def test_volume_expression_is_piecewise_over_time():
    expr = apply.build_volume_expression([(0.0, 6.0), (10.0, 0.0)], window_sec=10.0)
    assert expr.startswith("volume=volume='")
    assert "eval=frame" in expr
    assert "lt(t,10.0)" in expr
    assert "1.995262" in expr        # 10^(6/20)
    assert "1.000000" in expr


def test_volume_expression_for_a_single_window_is_a_constant():
    expr = apply.build_volume_expression([(0.0, 6.0)], window_sec=10.0)
    assert "if(" not in expr


def test_filter_chain_accepts_composed_stages():
    """分段增益修段落之間、speechnorm 修段落之內——兩者互補，不是二選一。
    實測（18 LU 階梯）：單獨 34% / 5%，疊起來 96%。"""
    assert apply.parse_stages("segmented,speech") == ["segmented", "speech"]
    assert apply.parse_stages("speech") == ["speech"]


def test_filter_chain_rejects_unknown_stages():
    with pytest.raises(ValueError):
        apply.parse_stages("segmented,magic")


def test_loudness_cannot_be_combined_with_other_stages():
    """loudness 的意思是「前面什麼都不加」，跟別的疊在一起沒有意義。"""
    with pytest.raises(ValueError):
        apply.parse_stages("loudness,speech")


def test_build_chain_composes_stages_in_the_given_order(monkeypatch):
    chain = apply.build_chain(["segmented", "speech"], mono=True,
                              loudnorm_args="I=-16",
                              gain_expression="volume=volume='1.5':eval=frame")
    assert chain == ("aformat=channel_layouts=mono,"
                     "volume=volume='1.5':eval=frame,"
                     "speechnorm=e=12.5:r=0.0001:l=1:p=0.95,"
                     "loudnorm=I=-16")


def test_build_chain_requires_a_gain_expression_for_segmented():
    with pytest.raises(ValueError):
        apply.build_chain(["segmented"], mono=False, loudnorm_args="I=-16")


def test_level_builds_the_gain_expression_from_the_supplied_samples(tmp_path, monkeypatch):
    """增益曲線由素材自己的逐窗響度算出來，所以 level 需要拿到樣本。
    呼叫端本來就會先 analyse 一次，不必為此多跑一趟 ffmpeg。"""
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if not _is_measure_pass(cmd):
            Path(cmd[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(cmd, 0, "", PASS1 if _is_measure_pass(cmd) else PASS2)

    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(apply.subprocess, "run", fake_run)
    result = apply.level(str(src), "segmented,speech", tmp_path / "o.mp3", mono=False,
                         samples=SAMPLES_STEP, duration_sec=120.0)
    chain = calls[0][calls[0].index("-af") + 1]
    assert chain.startswith("volume=volume='")
    assert "speechnorm=" in chain
    assert chain.index("volume=") < chain.index("speechnorm=")   # 順序：段落間先於段落內
    assert result["filter"] == "segmented,speech"


def test_level_refuses_segmented_without_window_data(tmp_path, monkeypatch):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr(measure, "require_tool", lambda name: "/usr/bin/" + name)
    with pytest.raises(ValueError):
        apply.level(str(src), "segmented", tmp_path / "o.mp3", mono=False)
