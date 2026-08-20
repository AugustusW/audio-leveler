"""端對端：以 ffmpeg 合成素材跑完整條鏈。

合成而不版控二進位檔——這個測試本來就要有 ffmpeg 才能跑，用它產生素材比在 repo
裡放一個 wav 更乾淨，而且素材的形狀寫在程式碼裡看得見。
"""
import shutil
import subprocess

import pytest

import apply
import cli
import measure

pytestmark = pytest.mark.ffmpeg


@pytest.fixture(scope="module")
def stepped_source(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("e2e") / "stepped.wav"
    # 前 4 秒大聲、後 4 秒小聲的粉紅噪音（頻譜行為比純正弦接近語音）
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anoisesrc=color=pink:duration=8:amplitude=0.5",
         "-af", "volume='if(lt(t,4),1.0,0.1)':eval=frame,aformat=channel_layouts=stereo",
         str(out)],
        check=True, timeout=120)
    return out


def test_measure_reports_a_spread_on_a_stepped_source(stepped_source):
    diag = measure.diagnose(str(stepped_source), window_sec=2.0)
    assert diag["channels"] == 2
    assert diag["dual_mono"] is True
    assert diag["spread_lu"] > 3.0
    assert 0.0 < diag["speech_ratio"] <= 1.0


def test_apply_speech_produces_a_linear_render(stepped_source, tmp_path):
    out = tmp_path / "e2e-leveled.mp3"
    result = apply.level(str(stepped_source), "speech", out, mono=True)
    assert result["normalization_type"] == "linear"
    assert out.exists() and out.stat().st_size > 0


def test_apply_loudness_branch_also_stays_linear(stepped_source, tmp_path):
    out = tmp_path / "e2e-loudness.mp3"
    result = apply.level(str(stepped_source), "loudness", out, mono=True)
    assert result["normalization_type"] == "linear"


def test_apply_segmented_branch_also_stays_linear(stepped_source, tmp_path):
    out = tmp_path / "e2e-segmented.mp3"
    result = apply.level(str(stepped_source), "segmented", out, mono=True)
    assert result["normalization_type"] == "linear"


def test_cli_apply_end_to_end_reports_before_and_after(stepped_source, tmp_path, capsys):
    out = tmp_path / "cli-leveled.mp3"
    rc = cli.main(["apply", str(stepped_source), "--filter", "speech", "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "before" in printed.lower() and "after" in printed.lower()
    assert out.exists()
