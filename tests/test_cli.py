import json

import pytest

import cli
import measure


DIAG = {
    "duration_sec": 3247.0,
    "channels": 2,
    "integrated_lufs": -16.8,
    "lra_lu": 9.2,
    "spread_lu": 10.0,
    "drift_lu": 3.5,
    "intra_lu": 16.2,
    "percentiles": {"p5": -22.6, "p25": -20.5, "p50": -18.6, "p75": -16.1, "p95": -12.6},
    "windows": [{"start_sec": 0.0, "median": -19.6, "min": -26.5, "max": -10.0}],
    "dual_mono": True,
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
