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


def test_apply_refuses_linear_on_a_source_with_no_true_peak_headroom(tmp_path):
    """素材已經頂在滿刻度時，linear 不可能成立——工具要在算之前就講清楚，
    並附上一個真的可用的目標值。"""
    # 可行與否取決於波峰因數（input_tp - input_i），而它不隨增益改變：
    # ceiling = -(crest) + target_tp，所以 crest < 14.5 dB 時 -16 LUFS 就不可能成立。
    # 這支素材經 speechnorm 後 crest 約 14.5 dB，剛好落在不可行側。
    hot = tmp_path / "hot.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anoisesrc=color=pink:duration=8:amplitude=0.5",
         "-af", "volume='if(lt(t,4),1.0,0.1)':eval=frame,aformat=channel_layouts=stereo",
         str(hot)],
        check=True, timeout=120)
    with pytest.raises(apply.LinearNotPossible) as e:
        apply.level(str(hot), "speech", tmp_path / "hot-leveled.mp3", mono=True)
    message = str(e.value)
    assert "--target-lufs" in message
    suggested = float(message.rsplit("--target-lufs", 1)[1].strip().rstrip("."))
    # 建議值必須真的可行：照抄一次就要成功，不能再撞同一則訊息
    out = tmp_path / "hot-retry.mp3"
    result = apply.level(str(hot), "speech", out, mono=True, target_lufs=suggested)
    assert result["normalization_type"] == "linear"
    assert out.exists()


def test_cli_apply_end_to_end_reports_before_and_after(stepped_source, tmp_path, capsys):
    out = tmp_path / "cli-leveled.mp3"
    rc = cli.main(["apply", str(stepped_source), "--filter", "speech", "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "before" in printed.lower() and "after" in printed.lower()
    assert out.exists()
