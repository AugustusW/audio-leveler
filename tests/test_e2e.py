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
    # 前 4 秒大聲、後 4 秒小聲的粉紅噪音（頻譜行為比純正弦接近語音）。
    #
    # seed 一定要給：anoisesrc 沒有 seed 時每次產生的素材都不一樣，實測 LRA 會在
    # 邊界兩側跳動，整套測試因此間歇性失敗（跑四次會壞一次）。振幅也留了餘裕，
    # 真實語音不會頂在滿刻度。
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anoisesrc=color=pink:duration=8:amplitude=0.12:seed=20260820",
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


def test_segmented_removes_drift_and_composes_with_speech(stepped_source, tmp_path):
    """分段增益修「段落之間」，speechnorm 修「段落之內」。

    實測（前後兩半差 18 LU 的素材，原始 spread 18.5）：現成濾鏡最好只到 16.4；
    分段增益單獨 12.3、drift 歸零；再疊 speechnorm 得 0.7 LU，收斂 96%。
    """
    before, samples = measure.analyse(str(stepped_source), window_sec=1.0)
    out = tmp_path / "e2e-segmented.mp3"
    result = apply.level(str(stepped_source), "segmented,speech", out, mono=True,
                         samples=samples, duration_sec=before["duration_sec"],
                         target_lufs=-20.0)
    assert result["normalization_type"] == "linear"
    after = measure.diagnose(str(out), window_sec=1.0)
    assert after["drift_lu"] < before["drift_lu"]
    assert after["spread_lu"] < before["spread_lu"]


def test_apply_refuses_an_unreachable_target_and_its_suggestion_works(stepped_source, tmp_path):
    """可行與否取決於波峰因數：ceiling = input_i - input_tp + target_tp，而波峰因數
    不隨增益改變。要拉到很大聲時，峰值會先撞到上限。

    用目標值（而不是素材）來觸發這個條件，測試才不會依賴 anoisesrc 的隨機種子——
    先前用「夠熱的素材」觸發，剛好卡在 14.5 dB 邊界上，時好時壞。
    """
    with pytest.raises(apply.LinearNotPossible) as e:
        apply.level(str(stepped_source), "speech", tmp_path / "unreachable.mp3",
                    mono=True, target_lufs=-6.0)
    message = str(e.value)
    assert "--target-lufs" in message

    suggested = float(message.rsplit("--target-lufs", 1)[1].strip().rstrip("."))
    # 建議值必須真的可行：照抄一次就要成功，不能再撞同一則訊息
    out = tmp_path / "retry.mp3"
    result = apply.level(str(stepped_source), "speech", out,
                         mono=True, target_lufs=suggested)
    assert result["normalization_type"] == "linear"
    assert out.exists()


def test_cli_apply_end_to_end_reports_before_and_after(stepped_source, tmp_path, capsys):
    out = tmp_path / "cli-leveled.mp3"
    rc = cli.main(["apply", str(stepped_source), "--filter", "speech", "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "before" in printed.lower() and "after" in printed.lower()
    assert out.exists()
