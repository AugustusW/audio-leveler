import json
from pathlib import Path

import pytest

import cli
import measure


DIAG = {
    "duration_sec": 3247.0,
    "window_sec": 360.0,
    "channels": 2,
    "integrated_lufs": -16.8,
    "lra_lu": 9.2,
    "spread_lu": 10.0,
    "drift_lu": 3.5,
    "intra_lu": 16.2,
    "percentiles": {"p5": -22.6, "p25": -20.5, "p50": -18.6, "p75": -16.1, "p95": -12.6},
    "windows": [{"start_sec": 0.0, "median": -19.6, "min": -26.5, "max": -10.0}],
    "dual_mono": True,
    "channel_separation_db": None,
    "speech_ratio": 1.0,
}


def test_format_report_contains_the_three_headline_numbers():
    text = cli.format_report(DIAG)
    assert "10.0" in text and "3.5" in text and "16.2" in text
    assert "-16.8" in text


def test_format_report_states_dual_mono_finding():
    assert "dual mono" in cli.format_report(DIAG).lower()


def test_format_report_has_no_emoji():
    text = cli.format_report(DIAG)
    assert all(ord(ch) < 0x1F000 for ch in text)


def test_measure_command_prints_report(monkeypatch, capsys, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr(cli.measure, "diagnose", lambda path, window_sec=360.0: DIAG)
    assert cli.main(["measure", str(src)]) == 0
    assert "spread" in capsys.readouterr().out.lower()


def test_measure_command_json_flag_emits_the_contract(monkeypatch, capsys, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr(cli.measure, "diagnose", lambda path, window_sec=360.0: DIAG)
    assert cli.main(["measure", str(src), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == DIAG


def test_measure_command_reports_missing_file(capsys, tmp_path):
    assert cli.main(["measure", str(tmp_path / "nope.mp3")]) == 2
    assert "not found" in capsys.readouterr().err.lower()


def test_measure_command_reports_missing_tool(monkeypatch, capsys, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")

    def boom(path, window_sec=360.0):
        raise measure.MissingTool("ffmpeg not found — install with: brew install ffmpeg")

    monkeypatch.setattr(cli.measure, "diagnose", boom)
    assert cli.main(["measure", str(src)]) == 3
    assert "brew install ffmpeg" in capsys.readouterr().err


def test_measure_command_reports_silent_material(monkeypatch, capsys, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")

    def boom(path, window_sec=360.0):
        raise measure.InsufficientSignal("not enough non-silent samples to diagnose")

    monkeypatch.setattr(cli.measure, "diagnose", boom)
    assert cli.main(["measure", str(src)]) == 4
    assert "non-silent" in capsys.readouterr().err


def test_no_subcommand_prints_usage_and_fails(capsys):
    with pytest.raises(SystemExit):
        cli.main([])


AFTER = dict(DIAG, spread_lu=5.8, integrated_lufs=-16.0, dual_mono=False, channels=1)
RESULT = {"filter": "speech", "mono": True, "target_lra": 5.1,
          "normalization_type": "linear", "output_path": "/tmp/x-leveled.mp3",
          "first_pass": {}, "second_pass": {}}


def test_format_comparison_states_before_after_and_percentage():
    text = cli.format_comparison(RESULT, DIAG, AFTER,
                                 {"before_lu": 10.0, "after_lu": 5.8, "delta_lu": -4.2,
                                  "converged_pct": 42.0, "improved": True})
    assert "10.0" in text and "5.8" in text and "42" in text


def test_format_comparison_says_so_when_nothing_improved():
    text = cli.format_comparison(RESULT, DIAG, DIAG,
                                 {"before_lu": 10.0, "after_lu": 10.0, "delta_lu": 0.0,
                                  "converged_pct": 0.0, "improved": False})
    assert "unchanged" in text.lower()
    assert "converged" not in text.lower()


def test_apply_command_measures_before_and_after(monkeypatch, capsys, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    seen = []

    def fake_diagnose(path, window_sec=360.0):
        seen.append(path)
        return DIAG if len(seen) == 1 else AFTER

    monkeypatch.setattr(cli.measure, "diagnose", fake_diagnose)
    monkeypatch.setattr(cli.apply, "level",
                        lambda *a, **k: dict(RESULT, output_path=str(tmp_path / "a-leveled.mp3")))
    assert cli.main(["apply", str(src), "--filter", "speech"]) == 0
    assert len(seen) == 2
    assert "42" in capsys.readouterr().out


def test_apply_command_requires_the_filter_flag(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    with pytest.raises(SystemExit):
        cli.main(["apply", str(src)])


def test_apply_command_rejects_unknown_filter(tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    with pytest.raises(SystemExit):
        cli.main(["apply", str(src), "--filter", "auto"])


def test_apply_command_mono_never_overrides_detection(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    captured = {}
    monkeypatch.setattr(cli.measure, "diagnose", lambda path, window_sec=360.0: DIAG)

    def fake_level(path, filter_name, out_path, **kwargs):
        captured.update(kwargs)
        return dict(RESULT, output_path=str(out_path))

    monkeypatch.setattr(cli.apply, "level", fake_level)
    cli.main(["apply", str(src), "--filter", "speech", "--mono", "never"])
    assert captured["mono"] is False


def test_apply_command_deletes_the_output_when_linear_is_lost(monkeypatch, capsys, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    out = tmp_path / "a-leveled.mp3"

    def fake_level(path, filter_name, out_path, **kwargs):
        Path(out_path).write_bytes(b"bad render")
        raise cli.apply.LinearModeLost("loudnorm fell back to 'dynamic' instead of linear")

    monkeypatch.setattr(cli.measure, "diagnose", lambda path, window_sec=360.0: DIAG)
    monkeypatch.setattr(cli.apply, "level", fake_level)
    assert cli.main(["apply", str(src), "--filter", "speech"]) == 6
    assert not out.exists()
    assert "dynamic" in capsys.readouterr().err


def test_format_comparison_says_unchanged_rather_than_printing_negative_zero():
    text = cli.format_comparison(RESULT, DIAG, DIAG,
                                 {"before_lu": 11.77, "after_lu": 11.76, "delta_lu": -0.01,
                                  "converged_pct": 0.08, "improved": False})
    assert "-0.0" not in text
    assert "unchanged" in text.lower()


def test_format_comparison_calls_a_regression_worse_not_merely_unimproved():
    text = cli.format_comparison(RESULT, DIAG, DIAG,
                                 {"before_lu": 8.0, "after_lu": 9.5, "delta_lu": 1.5,
                                  "converged_pct": -18.75, "improved": False})
    assert "worse" in text.lower()
    assert "+1.5" in text


def test_measure_accepts_a_url_source(monkeypatch, capsys, tmp_path):
    downloaded = tmp_path / "ep13.mp3"
    downloaded.write_bytes(b"x")
    monkeypatch.setattr(cli.source, "fetch", lambda url: (str(downloaded), "EP13"))
    monkeypatch.setattr(cli.measure, "diagnose", lambda path, window_sec=360.0: DIAG)
    assert cli.main(["measure", "https://podcasts.apple.com/tw/podcast/x/id1?i=2"]) == 0
    assert "spread" in capsys.readouterr().out.lower()


def test_url_download_failure_is_reported_not_swallowed(monkeypatch, capsys):
    def boom(url):
        raise cli.source.DownloadError("Apple lookup: episode not found")

    monkeypatch.setattr(cli.source, "fetch", boom)
    assert cli.main(["measure", "https://podcasts.apple.com/tw/podcast/x/id1?i=2"]) == 2
    assert "episode not found" in capsys.readouterr().err


def test_apply_from_url_writes_the_output_to_the_working_directory(monkeypatch, tmp_path):
    """URL 來源的輸出不能落在快取目錄裡——使用者找不到，而且清快取會一起刪掉。"""
    downloaded = tmp_path / "cache" / "ep13.mp3"
    downloaded.parent.mkdir()
    downloaded.write_bytes(b"x")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(cli.source, "fetch", lambda url: (str(downloaded), "EP13"))
    monkeypatch.setattr(cli.measure, "diagnose", lambda path, window_sec=360.0: DIAG)
    captured = {}

    def fake_level(path, filter_name, out_path, **kwargs):
        captured["out"] = out_path
        return dict(RESULT, output_path=str(out_path))

    monkeypatch.setattr(cli.apply, "level", fake_level)
    cli.main(["apply", "https://podcasts.apple.com/tw/podcast/x/id1?i=2", "--filter", "speech"])
    assert captured["out"] == cwd / "ep13-leveled.mp3"


def test_format_report_states_the_separation_when_stereo_is_not_fake():
    diag = dict(DIAG, dual_mono=False, channel_separation_db=24.94)
    text = cli.format_report(diag)
    assert "24.9" in text


def test_format_report_omits_separation_for_mono_sources():
    diag = dict(DIAG, channels=1, dual_mono=False, channel_separation_db=None)
    assert "separation" not in cli.format_report(diag).lower()


def test_format_report_names_the_window_length_actually_used():
    diag = dict(DIAG, window_sec=120.0)
    assert "2-minute" in cli.format_report(diag)
