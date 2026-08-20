import json
import subprocess

import pytest

import measure


def test_parse_short_term_extracts_time_and_value():
    stdout = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.r128.S=-120.691\n"
        "frame:1    pts:4410    pts_time:0.1\n"
        "lavfi.r128.S=-35.463\n"
    )
    assert measure.parse_short_term(stdout) == [(0.0, -120.691), (0.1, -35.463)]


def test_parse_short_term_ignores_unrelated_lines():
    stdout = "some noise\nframe:0    pts:0       pts_time:0\nlavfi.r128.M=-9.9\nlavfi.r128.S=-20.0\n"
    assert measure.parse_short_term(stdout) == [(0.0, -20.0)]


def test_parse_short_term_handles_inf():
    stdout = "frame:0    pts:0       pts_time:0\nlavfi.r128.S=-inf\n"
    assert measure.parse_short_term(stdout) == [(0.0, float("-inf"))]


def test_parse_short_term_empty_input_returns_empty_list():
    assert measure.parse_short_term("") == []


def test_gate_drops_samples_below_relative_gate():
    # I = -16 -> 相對閘 -36；-40 應被丟掉，-30 應留下
    samples = [(0.0, -30.0), (0.1, -40.0), (0.2, -20.0)]
    assert measure.gate(samples, integrated=-16.0) == [(0.0, -30.0), (0.2, -20.0)]


def test_gate_absolute_floor_wins_for_very_quiet_material():
    # I = -80 -> 相對閘 -100，但絕對閘 -70 較高，取 -70
    samples = [(0.0, -65.0), (0.1, -90.0)]
    assert measure.gate(samples, integrated=-80.0) == [(0.0, -65.0)]


def test_gate_drops_negative_infinity():
    samples = [(0.0, float("-inf")), (0.1, -20.0)]
    assert measure.gate(samples, integrated=-16.0) == [(0.1, -20.0)]


def test_percentile_interpolates_between_neighbours():
    assert measure.percentile([0.0, 10.0], 50) == pytest.approx(5.0)
    assert measure.percentile([0.0, 1.0, 2.0, 3.0, 4.0], 0) == 0.0
    assert measure.percentile([0.0, 1.0, 2.0, 3.0, 4.0], 100) == 4.0


def test_percentile_raises_on_empty():
    with pytest.raises(ValueError):
        measure.percentile([], 50)


def test_window_stats_buckets_by_window_length():
    samples = [(0.0, -20.0), (1.0, -10.0), (10.0, -30.0), (11.0, -34.0)]
    got = measure.window_stats(samples, window_sec=10.0)
    assert got == [
        {"start_sec": 0.0, "median": -15.0, "min": -20.0, "max": -10.0},
        {"start_sec": 10.0, "median": -32.0, "min": -34.0, "max": -30.0},
    ]


def test_window_stats_skips_empty_windows():
    samples = [(0.0, -20.0), (25.0, -22.0)]
    got = measure.window_stats(samples, window_sec=10.0)
    assert [w["start_sec"] for w in got] == [0.0, 20.0]


def test_build_diagnosis_reports_spread_drift_and_intra():
    # 窗 0：中位 -20，內部 -25..-15（intra 10）
    # 窗 10：中位 -18，內部 -19..-17（intra 2）
    samples = [
        (0.0, -25.0), (1.0, -20.0), (2.0, -15.0),
        (10.0, -19.0), (11.0, -18.0), (12.0, -17.0),
    ]
    d = measure.build_diagnosis(
        samples, integrated=-18.0, lra=6.0, duration=20.0,
        channels=2, dual_mono=True, window_sec=10.0,
    )
    assert d["integrated_lufs"] == -18.0
    assert d["duration_sec"] == 20.0
    assert d["dual_mono"] is True
    assert d["channels"] == 2
    assert d["drift_lu"] == pytest.approx(2.0)
    assert d["intra_lu"] == pytest.approx(6.0)
    values = [-25.0, -20.0, -15.0, -19.0, -18.0, -17.0]
    assert d["spread_lu"] == pytest.approx(
        measure.percentile(values, 95) - measure.percentile(values, 5))
    assert set(d["percentiles"]) == {"p5", "p25", "p50", "p75", "p95"}
    assert len(d["windows"]) == 2
    assert d["speech_ratio"] == 1.0


def test_build_diagnosis_speech_ratio_counts_gated_out_samples():
    samples = [(0.0, -20.0), (0.1, -90.0), (0.2, -20.0), (0.3, -90.0)]
    d = measure.build_diagnosis(samples, integrated=-20.0, lra=1.0, duration=1.0,
                                channels=1, dual_mono=False, window_sec=10.0)
    assert d["speech_ratio"] == pytest.approx(0.5)


def test_build_diagnosis_raises_when_everything_is_gated_out():
    samples = [(0.0, float("-inf")), (0.1, float("-inf"))]
    with pytest.raises(measure.InsufficientSignal):
        measure.build_diagnosis(samples, integrated=float("-inf"), lra=0.0, duration=1.0,
                                channels=1, dual_mono=False)


EBUR128_STDERR = """[Parsed_ebur128_0 @ 0x962c0cfc0] Summary:

  Integrated loudness:
    I:         -28.4 LUFS
    Threshold: -41.0 LUFS

  Loudness range:
    LRA:        10.6 LU
    Threshold: -51.0 LUFS
    LRA low:   -38.9 LUFS
    LRA high:  -28.3 LUFS
"""


def test_parse_ebur128_summary_reads_i_and_lra():
    assert measure.parse_ebur128_summary(EBUR128_STDERR) == (-28.4, 10.6)


def test_parse_ebur128_summary_ignores_lra_low_and_high():
    # 'LRA low:' 與 'LRA high:' 也以 LRA 開頭，必須靠精確 token 比對排除
    i, lra = measure.parse_ebur128_summary(EBUR128_STDERR)
    assert lra == 10.6 and i == -28.4


def test_parse_ebur128_summary_raises_when_absent():
    with pytest.raises(measure.FfmpegError):
        measure.parse_ebur128_summary("no summary here")


def test_require_tool_raises_missing_tool(monkeypatch):
    monkeypatch.setattr(measure.shutil, "which", lambda name: None)
    with pytest.raises(measure.MissingTool) as e:
        measure.require_tool("ffmpeg")
    assert "brew install ffmpeg" in str(e.value)


def test_probe_audio_reads_channels_and_duration(monkeypatch):
    payload = json.dumps({"streams": [{"channels": 2, "duration": "3247.000000"}]})
    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, payload, ""))
    assert measure.probe_audio("x.mp3") == (2, 3247.0)


def test_probe_audio_raises_when_no_audio_stream(monkeypatch):
    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, '{"streams": []}', ""))
    with pytest.raises(measure.FfmpegError):
        measure.probe_audio("x.txt")


def test_run_ebur128_returns_samples_and_summary(monkeypatch):
    stdout = "frame:0    pts:0       pts_time:0\nlavfi.r128.S=-20.0\n"
    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, EBUR128_STDERR))
    samples, i, lra = measure.run_ebur128("x.mp3")
    assert samples == [(0.0, -20.0)]
    assert (i, lra) == (-28.4, 10.6)


def test_run_ebur128_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "Invalid data found"))
    with pytest.raises(measure.FfmpegError) as e:
        measure.run_ebur128("broken.mp3")
    assert "Invalid data found" in str(e.value)


def test_parse_astats_rms_takes_last_value():
    stdout = ("frame:1    pts:1    pts_time:0.1\n"
              "lavfi.astats.Overall.RMS_level=-40.0\n"
              "frame:64   pts:262144 pts_time:5.94\n"
              "lavfi.astats.Overall.RMS_level=-33.045916\n")
    assert measure.parse_astats_rms(stdout) == pytest.approx(-33.045916)


def test_parse_astats_rms_handles_inf():
    stdout = "lavfi.astats.Overall.RMS_level=-inf\n"
    assert measure.parse_astats_rms(stdout) == float("-inf")


def test_parse_astats_rms_raises_when_absent():
    with pytest.raises(measure.FfmpegError):
        measure.parse_astats_rms("nothing here")


def test_is_dual_mono_true_when_difference_is_silent():
    # 實測 dual mono：差訊號 -inf、節目 -33.05
    assert measure.is_dual_mono(float("-inf"), -33.045916) is True


def test_is_dual_mono_false_for_real_stereo():
    # 實測右聲道延遲 15ms：差 -59.47、節目 -33.06，相距 26.4 dB < 60
    assert measure.is_dual_mono(-59.473987, -33.055873) is False


def test_is_dual_mono_boundary_is_inclusive_at_margin():
    assert measure.is_dual_mono(-90.0, -30.0) is True    # 正好 60 dB
    assert measure.is_dual_mono(-89.9, -30.0) is False   # 59.9 dB


def test_detect_dual_mono_short_circuits_for_mono_input(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("must not shell out for a 1-channel file")
    monkeypatch.setattr(measure.subprocess, "run", explode)
    assert measure.detect_dual_mono("x.mp3", channels=1) == (False, None)


def test_detect_dual_mono_returns_false_for_more_than_two_channels(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("must not shell out for a 5.1 file")
    monkeypatch.setattr(measure.subprocess, "run", explode)
    assert measure.detect_dual_mono("x.mp3", channels=6) == (False, None)


def test_detect_dual_mono_runs_two_passes_and_compares(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        rms = "-inf" if "0.5*c0-0.5*c1" in " ".join(cmd) else "-33.0"
        return subprocess.CompletedProcess(
            cmd, 0, "lavfi.astats.Overall.RMS_level={0}\n".format(rms), "")

    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run", fake_run)
    verdict, separation = measure.detect_dual_mono("x.mp3", channels=2)
    assert verdict is True and separation == float("inf")
    assert len(calls) == 2


def test_improvement_reports_convergence_ratio():
    d = measure.improvement({"spread_lu": 10.0}, {"spread_lu": 5.8})
    assert d["before_lu"] == 10.0
    assert d["after_lu"] == 5.8
    assert d["delta_lu"] == pytest.approx(-4.2)
    assert d["converged_pct"] == pytest.approx(42.0)
    assert d["improved"] is True


def test_improvement_flags_no_change_as_not_improved():
    d = measure.improvement({"spread_lu": 11.8}, {"spread_lu": 11.8})
    assert d["improved"] is False
    assert d["converged_pct"] == pytest.approx(0.0)


def test_improvement_flags_regression():
    d = measure.improvement({"spread_lu": 8.0}, {"spread_lu": 9.5})
    assert d["improved"] is False
    assert d["converged_pct"] < 0


def test_improvement_handles_zero_before_without_dividing_by_zero():
    d = measure.improvement({"spread_lu": 0.0}, {"spread_lu": 0.0})
    assert d["converged_pct"] == 0.0


def test_improvement_does_not_call_a_rounding_level_change_an_improvement():
    """實測踩到的：11.77 -> 11.76 被判為 improved，報告印出「converged 0%」。

    那句話讀起來像成功，但什麼也沒發生。低於可聞門檻的變化一律不算改善。
    """
    d = measure.improvement({"spread_lu": 11.77}, {"spread_lu": 11.76})
    assert d["improved"] is False


def test_improvement_requires_at_least_half_a_loudness_unit():
    assert measure.improvement({"spread_lu": 10.0}, {"spread_lu": 9.6})["improved"] is False
    assert measure.improvement({"spread_lu": 10.0}, {"spread_lu": 9.5})["improved"] is True


def test_channel_separation_db_reports_the_margin_not_just_a_verdict():
    """布林值不足以判斷。EP13 全片實測差 -44.93 / 節目 -19.98 = 25 dB：本體是
    dual mono，但開頭 30 秒的片頭是真立體聲，把整檔的差訊號能量拉了上來。
    只回報 True/False 的話，沒人看得出這是「幾乎全是 dual mono」還是「真立體聲」。
    """
    assert measure.channel_separation_db(-44.927889, -19.984394) == pytest.approx(24.94, abs=0.01)


def test_channel_separation_db_is_infinite_for_a_silent_difference():
    assert measure.channel_separation_db(float("-inf"), -20.0) == float("inf")


def test_detect_dual_mono_returns_verdict_and_separation(monkeypatch):
    def fake_run(cmd, **kwargs):
        rms = "-inf" if "0.5*c0-0.5*c1" in " ".join(cmd) else "-33.0"
        return subprocess.CompletedProcess(
            cmd, 0, "lavfi.astats.Overall.RMS_level={0}\n".format(rms), "")

    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run", fake_run)
    assert measure.detect_dual_mono("x.mp3", channels=2) == (True, float("inf"))


def test_build_diagnosis_carries_the_separation_into_the_contract():
    samples = [(0.0, -20.0), (1.0, -18.0)]
    d = measure.build_diagnosis(samples, integrated=-19.0, lra=2.0, duration=2.0,
                                channels=2, dual_mono=False, window_sec=10.0,
                                channel_separation_db=24.94)
    assert d["channel_separation_db"] == pytest.approx(24.94)


def test_build_diagnosis_records_the_window_length_it_used():
    """報告要說「幾分鐘的窗」，那個數字必須來自實際用的窗長，不能是模組預設值。"""
    samples = [(0.0, -20.0), (1.0, -18.0)]
    d = measure.build_diagnosis(samples, integrated=-19.0, lra=2.0, duration=2.0,
                                channels=1, dual_mono=False, window_sec=120.0)
    assert d["window_sec"] == 120.0


def test_diagnosis_json_has_no_non_standard_constants():
    """契約在兩份 README 都被描述成「給模型或腳本讀的」，那它就得是合法 JSON。

    dual mono 的分離度是 float('inf')，json.dumps 會寫出裸的 Infinity——Python
    自己讀得回來，Node 的 JSON.parse 直接拒絕，jq 則悄悄轉成 1.8e308。
    """
    samples = [(0.0, -20.0), (1.0, -18.0)]
    d = measure.build_diagnosis(samples, integrated=-19.0, lra=2.0, duration=2.0,
                                channels=2, dual_mono=True, window_sec=10.0,
                                channel_separation_db=float("inf"))
    text = json.dumps(d)

    def reject(constant):
        raise AssertionError("非標準 JSON 常數: " + constant)

    json.loads(text, parse_constant=reject)


def test_dual_mono_separation_is_null_rather_than_infinity():
    samples = [(0.0, -20.0), (1.0, -18.0)]
    d = measure.build_diagnosis(samples, integrated=-19.0, lra=2.0, duration=2.0,
                                channels=2, dual_mono=True, window_sec=10.0,
                                channel_separation_db=float("inf"))
    assert d["channel_separation_db"] is None
    assert d["dual_mono"] is True


def test_probe_audio_falls_back_to_container_duration(monkeypatch):
    """webm 的 stream entry 沒有 duration，而 webm 正是 yt-dlp 抓 YouTube 的常見容器。
    少了 fallback，報告會若無其事地印出 0:00:00。"""
    payload = json.dumps({"streams": [{"channels": 1}],
                          "format": {"duration": "612.345"}})
    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, payload, ""))
    assert measure.probe_audio("x.webm") == (1, 612.345)


def test_probe_audio_prefers_the_stream_duration_when_present(monkeypatch):
    payload = json.dumps({"streams": [{"channels": 2, "duration": "100.0"}],
                          "format": {"duration": "999.0"}})
    monkeypatch.setattr(measure.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(measure.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, payload, ""))
    assert measure.probe_audio("x.mp3") == (2, 100.0)


def test_window_scales_down_for_short_material():
    """6 分鐘的窗是為了「明顯大於一個話題段落」而選的。素材只有 2 分鐘時這個意圖
    根本沒被滿足——整支塞進一個窗，drift 結構性地等於 0。

    實測：2 分鐘、前後兩半差 18 LU 的素材，固定 6 分鐘窗給出 drift=0.00、
    intra=18.55，SKILL.md 的規則會據此判成 speech，完全判反。
    """
    assert measure.auto_window_sec(3247.0) == 360.0     # 長素材維持 6 分鐘
    assert measure.auto_window_sec(120.0) == 30.0       # 2 分鐘 -> 4 個窗
    assert measure.auto_window_sec(600.0) == 150.0
    assert measure.auto_window_sec(10.0) == 30.0        # 有下限，不會切到無意義的碎片


def test_short_material_with_a_step_reports_drift_not_just_intra():
    """同一支素材換成自動窗長之後，drift 必須看得見。"""
    samples = ([(t, -20.0) for t in range(0, 60)] +
               [(t, -38.0) for t in range(60, 120)])
    d = measure.build_diagnosis(samples, integrated=-26.0, lra=18.0, duration=120.0,
                                channels=1, dual_mono=False)
    assert d["window_sec"] == 30.0
    assert len(d["windows"]) == 4
    assert d["drift_lu"] > d["intra_lu"]


def test_explicit_window_still_wins():
    samples = [(0.0, -20.0), (5.0, -18.0)]
    d = measure.build_diagnosis(samples, integrated=-19.0, lra=2.0, duration=10.0,
                                channels=1, dual_mono=False, window_sec=2.0)
    assert d["window_sec"] == 2.0
