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
