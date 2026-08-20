"""測試共用設定：把 scripts/ 掛進 sys.path，並註冊 ffmpeg marker。

scripts/ 不是套件（skill 的檔案是被宿主以路徑直接呼叫的），所以測試要能 import
就得手動掛路徑——這與 audio-tldr-skill 的做法一致。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "audio-leveler" / "scripts"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "ffmpeg: 需要本機有 ffmpeg 才能跑（CI 以 -m 'not ffmpeg' 排除）")
