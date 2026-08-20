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
